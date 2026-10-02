import tempfile
import time
import unittest
from dataclasses import replace
from unittest import mock

from factory import db as dbm
from factory import main as m
from factory.config import ReviewCfg, Route, default_harnesses, RunnerCfg, load
from factory.runner import RunResult
from test_roles import CFG, FakeClf, FakeGH, issue
from test_runner import RunTaskEndToEnd

REVIEW = replace(CFG, review=ReviewCfg(enabled=True))
PRS = "https://github.com/o/r/pull/9 https://github.com/o/m/pull/4"


class GH(FakeGH):
    def __init__(self, *a, branch="factory/issue-5-x", **k):
        super().__init__(*a, **k)
        self.branch = branch
        self.posted = []

    def get_pr(self, repo, n):
        return {"head": {"ref": self.branch, "repo": {"full_name": repo}}}

    def comment(self, repo, num, body):
        self.posted.append((repo, num, body))
        return super().comment(repo, num, body)


def runner_fake(review_output="**Verdict**: Needs changes - missing a test.\n\ncc @mallory", build=None):
    calls = []

    def fake(cfg, gh, repo, iss, route, **kw):
        calls.append((kw.get("role"), route, kw.get("fix_branch")))
        if kw.get("role") == "reviewer":
            if isinstance(review_output, Exception):
                raise review_output
            return review_output if isinstance(review_output, RunResult) else RunResult("stage", "ok", output=review_output)
        return build or RunResult("pr", "2 pull request(s) opened", PRS)
    return fake, calls


class Config(unittest.TestCase):
    def test_off_by_default_and_validated(self):
        self.assertFalse(ReviewCfg().enabled)
        base = open("config.example.toml").read()
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write(base + '\n[review]\nenabled = true\nmodel = "opus"\n')
        cfg = load(f.name)
        self.assertEqual((cfg.review.enabled, cfg.review.model, cfg.review.effort), (True, "opus", "high"))
        for bad in ('\n[review]\nenabled = true\nharness = "codex"\n', '\n[review]\neffort = "extreme"\n'):
            with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as g:
                g.write(base + bad)
            with self.assertRaises(ValueError):
                load(g.name)                                                       # an unavailable harness, or a bad effort

    def test_review_label_is_only_polled_when_enabled(self):
        self.assertNotIn("factory:review", [l for _, l in m.triggers(CFG)])
        self.assertIn("factory:review", [l for _, l in m.triggers(REVIEW)])


class AutomaticReview(unittest.TestCase):
    def run_dispatch(self, cfg=REVIEW, fake=None, branch="factory/issue-5-x"):
        fake_run, calls = fake or runner_fake()
        gh, conn = GH(branch=branch), dbm.connect(":memory:")
        with mock.patch.object(m.runner, "run_task", side_effect=fake_run):
            res = m.dispatch(cfg, gh, "o/r", issue(labels=["factory:ready"]), Route("claude-code", "sonnet", "medium"), conn=conn)
        return res, gh, calls

    def test_a_review_follows_a_build_and_is_posted_on_every_pr(self):
        res, gh, calls = self.run_dispatch()
        self.assertEqual(res.status, "pr")
        self.assertEqual([c[0] for c in calls], [None, "reviewer"])
        role, route, branch = calls[1]
        self.assertEqual((route.model, route.effort, branch), ("sonnet", "high", "factory/issue-5-x"))   # the PR branch is checked out
        self.assertEqual(sorted((r, n) for r, n, _ in gh.posted if "factory:review" in _), [("o/m", 4), ("o/r", 9)])
        body = [b for r, n, b in gh.posted if n == 9][0]
        self.assertTrue(body.startswith("<!-- factory:review -->"))
        self.assertIn("never approves or requests changes", body)
        self.assertNotIn("@mallory", body)                                               # sanitized like every agent document
        self.assertIn(("add", ("stage:reviewed",)), gh.calls)

    def test_it_only_comments_it_never_approves_or_reviews_formally(self):
        _, gh, _ = self.run_dispatch()
        self.assertFalse(hasattr(gh, "create_review"))                                    # the client has no approve/request-changes call
        self.assertTrue(all(c[0] in ("rm", "add", "comment", "actor") for c in gh.calls))

    def test_disabled_or_manual_only_means_no_automatic_review(self):
        for cfg in (CFG, replace(CFG, review=ReviewCfg(enabled=True, auto=False))):
            _, _, calls = self.run_dispatch(cfg)
            self.assertEqual([c[0] for c in calls], [None])

    def test_a_branch_the_factory_did_not_create_is_never_reviewed(self):
        _, gh, calls = self.run_dispatch(branch="someone/else")
        self.assertEqual([c[0] for c in calls], [None])

    def test_a_reviewer_crash_or_rate_limit_never_undoes_the_build(self):
        res, gh, calls = self.run_dispatch(fake=runner_fake(RuntimeError("boom")))
        self.assertEqual(res.status, "pr")
        self.assertIn(("add", ("factory:pr-open",)), gh.calls)
        res, gh, calls = self.run_dispatch(fake=runner_fake(RunResult("rate-limited", "x")))
        self.assertEqual(res.status, "pr")
        self.assertNotIn(("add", ("factory:review",)), gh.calls)                          # automatic: no requeue loop

    def test_a_failed_review_is_reported_not_posted(self):
        _, gh, _ = self.run_dispatch(fake=runner_fake(RunResult("failed", "agent exited 1")))
        self.assertEqual([b for _, _, b in gh.posted if "factory:review" in b], [])


class ManualReview(unittest.TestCase):
    def go(self, prs, labels=("factory:review",), cfg=REVIEW, fake=None):
        fake_run, calls = fake or runner_fake()
        gh, conn = GH({"factory:review": [issue(labels=labels)]}), dbm.connect(":memory:")
        for r, n in prs:
            dbm.watch_pr(conn, r, n, "o/r", 5)
        with mock.patch.object(m.runner, "run_task", side_effect=fake_run), tempfile.TemporaryDirectory() as d:
            m.poll_once(replace(cfg, db_path=d + "/f.db"), gh, conn, FakeClf())
        return gh, calls, conn

    def test_the_label_reviews_the_tickets_open_factory_prs(self):
        gh, calls, conn = self.go([("o/r", 9), ("o/m", 4)])
        self.assertEqual([c[0] for c in calls], ["reviewer"])
        self.assertEqual(sorted(n for _, n, b in gh.posted if "factory:review" in b), [4, 9])
        self.assertIn(("rm", "factory:review"), gh.calls)                                 # claimed
        self.assertIn(("rm", "factory:working-review"), gh.calls)                         # released
        self.assertEqual(conn.execute("select outcome from decisions").fetchone()[0], "run:stage")

    def test_no_prs_means_a_short_comment_and_no_agent_run(self):
        gh, calls, _ = self.go([])
        self.assertEqual(calls, [])
        self.assertTrue(any("No open factory pull requests" in b for _, _, b in gh.posted))

    def test_closed_prs_are_not_reviewed(self):
        conn = dbm.connect(":memory:")
        dbm.watch_pr(conn, "o/r", 9, "o/r", 5)
        dbm.update_pr(conn, "o/r", 9, status="closed")
        self.assertEqual(dbm.prs_for_issue(conn, "o/r", 5), [])

    def test_the_label_is_ignored_when_review_is_disabled_or_by_untrusted_users(self):
        gh, calls, _ = self.go([("o/r", 9)], cfg=CFG)
        self.assertEqual(calls, [])                                                       # not even polled
        class Untrusted(GH):
            def permission(self, repo, login): return "read"
        gh2, conn = Untrusted({"factory:review": [issue(labels=["factory:review"])]}), dbm.connect(":memory:")
        dbm.watch_pr(conn, "o/r", 9, "o/r", 5)
        fake_run, calls = runner_fake()
        with mock.patch.object(m.runner, "run_task", side_effect=fake_run), tempfile.TemporaryDirectory() as d:
            m.poll_once(replace(REVIEW, db_path=d + "/f.db"), gh2, conn, FakeClf())
        self.assertEqual(calls, [])

    def test_dry_run_only_records(self):
        gh, calls, conn = self.go([("o/r", 9)], cfg=replace(REVIEW, dry_run=True))
        self.assertEqual(calls, [])
        self.assertEqual(conn.execute("select outcome from decisions").fetchone()[0], "review")

    def test_recovery_requeues_an_interrupted_review(self):
        from factory.config import RunnerCfg
        class RGH(FakeGH):
            def labeled_issues(self, repo, label): return [{"number": 3}] if label == "factory:working-review" else []
        gh = RGH()
        with tempfile.TemporaryDirectory() as d, mock.patch.object(m.subprocess, "run"):
            m.recover(replace(REVIEW, runner=RunnerCfg(work_dir=d)), gh)
        self.assertEqual([c for c in gh.calls if c[0] in ("rm", "add")], [("rm", "factory:working-review"), ("add", ("factory:review",))])


class ReviewerRun(unittest.TestCase):
    _setup = RunTaskEndToEnd._setup

    def test_the_reviewer_sees_the_pr_branch_and_changes_nothing(self):
        from pathlib import Path
        from factory import runner
        prompts = []
        with tempfile.TemporaryDirectory() as t:
            def edits(work):
                (work / "web" / "a.txt").write_text("a reviewer must not be able to change this\n")
            cfg, gh, bare, p1, p2, g = self._setup(t, edits)
            g("--git-dir", str(bare["o/web"]), "branch", "factory/issue-1-x", "main", cwd=t)
            real = p2.new

            def spy(cmd, *a, **k):
                if cmd[0] in ("podman", "docker"):
                    for i, x in enumerate(cmd):
                        if x == "-v" and cmd[i + 1].endswith(":/task:ro"):
                            prompts.append((Path(cmd[i + 1].split(":")[0]) / "prompt.txt").read_text())
                return real(cmd, *a, **k)
            with p1, mock.patch("subprocess.run", spy):
                res = runner.run_task(cfg, gh, "o/web", {"number": 1, "title": "t", "body": ""}, Route("claude-code", "sonnet", "high"),
                                      role="reviewer", fix_branch="factory/issue-1-x")
            self.assertEqual((res.status, gh.prs), ("stage", []))
            self.assertIn("Code reviewer", prompts[0])
            self.assertIn("do not approve or reject", prompts[0])
            for repo in bare:                                                              # nothing was pushed anywhere: still one commit
                self.assertEqual(g("--git-dir", str(bare[repo]), "rev-list", "--count", "--all", cwd=t).stdout.strip(), "1")


if __name__ == "__main__":
    unittest.main()
