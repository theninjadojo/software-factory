import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from factory import conflicts
from factory import db as dbm
from factory import main as m
from factory.config import CiCfg, ConflictsCfg, Route, load
from factory.runner import RunResult
from test_roles import CFG, FakeClf, FakeGH, issue
from test_runner import RunTaskEndToEnd

ON = replace(CFG, conflicts=ConflictsCfg(enabled=True))
LABEL = "factory:fix-conflicts"
BRANCH = "factory/issue-5-x"


def pr(state="open", mergeable=False, sha="abc", branch=BRANCH, head_repo=None, repo="o/r", n=9):
    return {"state": state, "mergeable": mergeable, "mergeable_state": "dirty" if mergeable is False else "clean",
            "html_url": f"https://github.com/{repo}/pull/{n}", "base": {"ref": "main"},
            "head": {"sha": sha, "ref": branch, "repo": {"full_name": head_repo or repo}}}


class GH(FakeGH):
    def __init__(self, prs=None, perm="admin", **k):
        super().__init__(**k)
        self.prs, self.perm, self.posted, self.created = prs or {}, perm, [], []

    def get_pr(self, repo, n):
        return self.prs[(repo, n)]

    def permission(self, repo, login):
        return self.perm

    def create_label(self, repo, name, color="ededed", description=""):
        self.created.append((repo, name))

    def comment(self, repo, num, body):
        self.posted.append((repo, num, body))
        return super().comment(repo, num, body)


def tracked(*prs, attempts=0):
    conn = dbm.connect(":memory:")
    for r, n in prs:
        dbm.track_pr(conn, r, n, "o/r", 5)
        if attempts:
            dbm.update_conflict(conn, r, n, attempts=attempts)
    return conn


def row(conn, r="o/r", n=9):
    return conn.execute("SELECT state, notified_sha, attempts FROM pr_conflicts WHERE repo=? AND number=?", (r, n)).fetchone()


class Config(unittest.TestCase):
    def test_off_by_default_and_the_label_is_only_polled_when_enabled(self):
        c = ConflictsCfg()
        self.assertEqual((c.enabled, c.auto, c.label, c.max_attempts), (False, True, LABEL, 10))
        self.assertNotIn(LABEL, [l for _, l in m.triggers(CFG)])
        self.assertIn(("conflicts", LABEL), m.triggers(ON))

    def test_loaded_and_validated(self):
        base = open("config.example.toml").read()
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write(base.replace("[conflicts]\nenabled = false", "[conflicts]\nenabled = true"))
        cfg = load(f.name)
        self.assertEqual((cfg.conflicts.enabled, cfg.conflicts.max_attempts), (True, 10))
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as g:
            g.write(base.replace("max_attempts = 10", "max_attempts = -1"))
        with self.assertRaises(ValueError):
            load(g.name)


class Detection(unittest.TestCase):
    def setUp(self):
        conflicts._created.clear()                     # the label is created once per repo per process

    def watch(self, gh, conn, cfg=ON):
        sent = []
        conflicts.watch(cfg, gh, conn, lambda text, event: sent.append((event, text)))
        return sent

    def test_states(self):
        self.assertEqual(conflicts.state_of(pr(mergeable=None)), "unknown")
        self.assertEqual(conflicts.state_of(pr(mergeable=False)), "conflicting")
        self.assertEqual(conflicts.state_of(pr(mergeable=True)), "clean")
        self.assertEqual(conflicts.state_of(pr(state="closed", mergeable=False)), "closed")

    def test_unknown_is_never_a_conflict(self):
        gh, conn = GH({("o/r", 9): pr(mergeable=None)}), tracked(("o/r", 9))
        self.assertEqual(self.watch(gh, conn), [])
        self.assertEqual((gh.posted, row(conn)[0]), ([], "unknown"))

    def test_reported_once_per_head_and_the_ticket_is_labelled(self):
        gh, conn = GH({("o/r", 9): pr()}), tracked(("o/r", 9))
        sent = self.watch(gh, conn)
        self.assertEqual([e for e, _ in sent], ["conflict"])
        self.assertEqual(sorted((r, n) for r, n, _ in gh.posted), [("o/r", 5), ("o/r", 9)])      # the PR and the ticket
        self.assertIn(("add", (LABEL,)), gh.calls)
        self.assertEqual(gh.created, [("o/r", LABEL)])
        self.assertEqual(row(conn)[:2], ("conflicting", "abc"))
        gh.calls.clear()
        self.assertEqual(self.watch(gh, conn), [])                                                # same head: quiet
        self.assertEqual((len(gh.posted), gh.calls), (2, []))

    def test_a_conflict_that_clears_and_comes_back_is_reported_again(self):
        gh, conn = GH({("o/r", 9): pr()}), tracked(("o/r", 9))
        self.watch(gh, conn)
        gh.prs[("o/r", 9)] = pr(mergeable=True)
        self.assertEqual(self.watch(gh, conn), [])
        self.assertEqual(row(conn)[:2], ("clean", ""))
        gh.prs[("o/r", 9)] = pr()
        self.assertEqual(len(self.watch(gh, conn)), 1)

    def test_sibling_prs_label_the_ticket_once(self):
        gh = GH({("o/r", 9): pr(), ("o/m", 4): pr(repo="o/m", n=4)})
        self.watch(gh, tracked(("o/r", 9), ("o/m", 4)))
        self.assertEqual(gh.calls.count(("add", (LABEL,))), 1)

    def test_manual_mode_and_the_attempt_limit_only_report(self):
        for cfg, conn in ((replace(ON, conflicts=ConflictsCfg(enabled=True, auto=False)), tracked(("o/r", 9))),
                          (ON, tracked(("o/r", 9), attempts=10))):
            gh = GH({("o/r", 9): pr()})
            self.assertEqual(len(self.watch(gh, conn, cfg)), 1)
            self.assertNotIn(("add", (LABEL,)), gh.calls)

    def test_disabled_does_nothing_and_closed_prs_stop_being_checked(self):
        gh, conn = GH({("o/r", 9): pr()}), tracked(("o/r", 9))
        self.assertEqual((self.watch(gh, conn, CFG), gh.posted), ([], []))
        gh.prs[("o/r", 9)] = pr(state="closed")
        self.watch(gh, conn)
        self.assertEqual(dbm.tracked_prs(conn), [])

    def test_prs_are_tracked_whether_or_not_ci_is_watched(self):
        gh, conn = GH(), dbm.connect(":memory:")
        fake = mock.Mock(return_value=RunResult("pr", "1 pull request(s) opened", "https://github.com/o/r/pull/9"))
        with mock.patch.object(m.runner, "run_task", fake):
            m.dispatch(replace(CFG, ci=CiCfg(enabled=False)), gh, "o/r", issue(), Route("claude-code", "sonnet", "medium"), conn=conn)
        self.assertEqual([(p["repo"], p["number"]) for p in dbm.tracked_prs(conn)], [("o/r", 9)])
        self.assertEqual(dbm.watching(conn), [])


def fake_runner(result=None):
    calls = []

    def fake(cfg, gh, repo, iss, route, **kw):
        calls.append(kw)
        return result or RunResult("pr", "merged the base branch")
    return fake, calls


class Resolution(unittest.TestCase):
    def go(self, gh, conn, cfg=ON, result=None):
        fake, calls = fake_runner(result)
        with mock.patch.object(m.runner, "run_task", side_effect=fake), tempfile.TemporaryDirectory() as d:
            m.handle_issue(replace(cfg, db_path=d + "/f.db"), gh, conn, FakeClf(), "o/r", issue(labels=[LABEL]), "conflicts", LABEL)
        return calls

    def test_trusted_label_merges_every_conflicting_pr_one_run_per_branch(self):
        gh = GH({("o/r", 9): pr(), ("o/m", 4): pr(repo="o/m", n=4), ("o/x", 2): pr(mergeable=True, repo="o/x", n=2)})
        conn = tracked(("o/r", 9), ("o/m", 4), ("o/x", 2))
        calls = self.go(gh, conn)
        self.assertEqual(len(calls), 1)                                                             # siblings share the branch
        self.assertEqual((calls[0]["fix_branch"], calls[0]["merge_base"]), (BRANCH, {"o/m": "main", "o/r": "main"}))
        self.assertIn(("rm", LABEL), gh.calls)                                                      # claimed
        self.assertNotIn(("rm", "factory:pr-open"), gh.calls)                                      # the PRs are still open
        self.assertIn(("rm", "factory:working-conflicts"), gh.calls)                                # released
        self.assertEqual([row(conn, *k)[2] for k in (("o/r", 9), ("o/m", 4), ("o/x", 2))], [1, 1, 0])
        self.assertEqual(row(conn)[:2], ("unknown", ""))                                           # GitHub re-checks the new head
        self.assertTrue(any(n == 9 and "Merged `main`" in b for _, n, b in gh.posted))
        self.assertEqual(conn.execute("select outcome from decisions").fetchone()[0], "run:pr")

    def test_untrusted_label_is_ignored(self):
        gh, conn = GH({("o/r", 9): pr()}, perm="read"), tracked(("o/r", 9))
        self.assertEqual(self.go(gh, conn), [])
        self.assertEqual(conn.execute("select outcome from decisions").fetchone()[0], "ignored")
        self.assertEqual(row(conn)[2], 0)

    def test_a_branch_the_factory_did_not_create_or_a_fork_is_never_touched(self):
        for p in (pr(branch="feature/x"), pr(head_repo="mallory/r")):
            gh, conn = GH({("o/r", 9): p}), tracked(("o/r", 9))
            self.assertEqual(self.go(gh, conn), [])
            self.assertTrue(any("not a factory branch" in b for _, _, b in gh.posted))

    def test_attempt_limit_stops_and_asks_a_person(self):
        gh, conn = GH({("o/r", 9): pr()}), tracked(("o/r", 9), attempts=10)
        self.assertEqual(self.go(gh, conn), [])
        self.assertTrue(any("all 10 resolution attempts" in b for _, _, b in gh.posted))
        self.assertEqual(row(conn)[0], "needs-person")

    def test_no_conflict_means_a_comment_and_no_run(self):
        gh, conn = GH({("o/r", 9): pr(mergeable=True)}), tracked(("o/r", 9))
        self.assertEqual(self.go(gh, conn), [])
        self.assertTrue(any("nothing was merged" in b for _, _, b in gh.posted))

    def test_rate_limit_requeues_without_using_an_attempt(self):
        gh, conn = GH({("o/r", 9): pr()}), tracked(("o/r", 9))
        self.go(gh, conn, result=RunResult("rate-limited", "x"))
        self.assertIn(("add", (LABEL,)), gh.calls)
        self.assertEqual(row(conn)[2], 0)

    def test_failure_is_reported_counted_and_not_relabelled_for_that_head(self):
        gh, conn = GH({("o/r", 9): pr()}), tracked(("o/r", 9))
        dbm.update_conflict(conn, "o/r", 9, state="conflicting", notified_sha="abc")
        self.go(gh, conn, result=RunResult("failed", "agent exited 1. Log tail: ping @mallory"))
        self.assertEqual(row(conn), ("needs-person", "abc", 1))
        self.assertTrue(any("Could not resolve" in b for _, n, b in gh.posted if n == 9))
        self.assertFalse(any("@mallory" in b for _, _, b in gh.posted))                           # an agent's log never reaches GitHub
        gh.calls.clear()
        conflicts.watch(ON, gh, conn, lambda *a: None)
        self.assertNotIn(("add", (LABEL,)), gh.calls)

    def test_ci_is_watched_again_after_a_push(self):
        gh, conn = GH({("o/r", 9): pr()}), tracked(("o/r", 9))
        dbm.watch_pr(conn, "o/r", 9, "o/r", 5)
        dbm.update_pr(conn, "o/r", 9, status="passed")
        self.go(gh, conn)
        self.assertEqual(conn.execute("select status from prs").fetchone()[0], "watching")

    def test_dry_run_only_records(self):
        gh, conn = GH({("o/r", 9): pr()}), tracked(("o/r", 9))
        self.assertEqual(self.go(gh, conn, cfg=replace(ON, dry_run=True)), [])
        self.assertEqual(conn.execute("select outcome from decisions").fetchone()[0], "conflicts")

    def test_recovery_requeues_an_interrupted_resolution(self):
        from factory.config import RunnerCfg

        class RGH(FakeGH):
            def labeled_issues(self, repo, label): return [{"number": 3}] if label == "factory:working-conflicts" else []
        gh = RGH()
        with tempfile.TemporaryDirectory() as d, mock.patch.object(m.subprocess, "run"):
            m.recover(replace(ON, runner=RunnerCfg(work_dir=d)), gh)
        self.assertEqual([c for c in gh.calls if c[0] in ("rm", "add")], [("rm", "factory:working-conflicts"), ("add", (LABEL,))])


class MergeRun(unittest.TestCase):
    """runner.run_task in merge mode, with local bare repos as 'GitHub' and the fake sandbox from test_runner."""
    _setup = RunTaskEndToEnd._setup

    def diverge(self, t, g, bare, branch_files, main_files, repo="o/web"):
        w = Path(t) / "diverge"
        g("clone", "-q", str(bare[repo]), str(w), cwd=t)
        for ref, files in ((BRANCH, branch_files), ("main", main_files)):
            g("checkout", "-q", *(["-b"] if ref == BRANCH else []), ref, cwd=w)
            for p, text in files.items():
                (w / p).parent.mkdir(parents=True, exist_ok=True)
                (w / p).write_text(text)
            g("add", "-A", cwd=w)
            g("commit", "-qm", ref, cwd=w)
        g("push", "-q", "origin", "main", BRANCH, cwd=w)

    def run_merge(self, edits, branch_files, main_files):
        from factory import runner
        prompts = []
        with tempfile.TemporaryDirectory() as t:
            cfg, gh, bare, p1, p2, g = self._setup(t, edits)
            self.diverge(t, g, bare, branch_files, main_files)
            before = g("--git-dir", str(bare["o/web"]), "rev-parse", BRANCH, cwd=t).stdout.strip()
            real = p2.new

            def spy(cmd, *a, **k):
                if cmd[0] in ("podman", "docker"):
                    for i, x in enumerate(cmd):
                        if x == "-v" and cmd[i + 1].endswith(":/task:ro"):
                            prompts.append((Path(cmd[i + 1].split(":")[0]) / "prompt.txt").read_text())
                return real(cmd, *a, **k)
            with p1, mock.patch("subprocess.run", spy):
                res = runner.run_task(cfg, gh, "o/web", {"number": 5, "title": "t", "body": ""}, cfg.routes["low"],
                                      fix_branch=BRANCH, merge_base={"o/web": "main"})
            head = g("--git-dir", str(bare["o/web"]), "rev-parse", BRANCH, cwd=t).stdout.strip()
            parents = g("--git-dir", str(bare["o/web"]), "rev-list", "--parents", "-n", "1", BRANCH, cwd=t).stdout.split()[1:]
            text = g("--git-dir", str(bare["o/web"]), "show", f"{BRANCH}:a.txt", cwd=t).stdout
            mains = g("--git-dir", str(bare["o/web"]), "rev-parse", "main", cwd=t).stdout.strip()
            return res, prompts, {"moved": head != before, "parents": parents, "before": before, "main": mains, "a": text, "prs": gh.prs}

    def test_a_clean_merge_is_pushed_without_an_agent(self):
        res, prompts, out = self.run_merge(lambda w: None, {"a.txt": "branch\n"}, {"b.txt": "from main\n"})
        self.assertEqual(res.status, "pr", res.detail)
        self.assertEqual(prompts, [])                                                              # no sandbox
        self.assertEqual(out["parents"], [out["before"], out["main"]])                             # a merge commit, fast-forward
        self.assertEqual(out["prs"], [])

    def test_the_agent_resolves_the_conflict_and_the_merge_is_pushed(self):
        def edits(w):
            self.assertIn("<<<<<<<", (w / "web" / "a.txt").read_text())                            # the agent sees the markers
            (w / "web" / "a.txt").write_text("both\n")
        res, prompts, out = self.run_merge(edits, {"a.txt": "branch\n"}, {"a.txt": "main\n", "c.txt": "new on main\n"})
        self.assertEqual(res.status, "pr", res.detail)
        self.assertIn("<merge_conflicts>", prompts[0])
        self.assertIn("web/: a.txt", prompts[0])
        self.assertEqual(out["a"], "both\n")
        self.assertEqual(out["parents"], [out["before"], out["main"]])

    def test_conflict_markers_left_behind_are_refused(self):
        for edits in (lambda w: None, lambda w: (w / "web" / "a.txt").write_text("<<<<<<< HEAD\nbranch\n")):
            res, _, out = self.run_merge(edits, {"a.txt": "branch\n"}, {"a.txt": "main\n"})
            self.assertIn(res.status, ("no-change", "rejected"))
            self.assertFalse(out["moved"])

    def test_the_agent_may_only_edit_repos_with_conflicts_and_no_protected_paths(self):
        def other_repo(w):
            (w / "web" / "a.txt").write_text("both\n"); (w / "mobile" / "a.txt").write_text("sneaky\n")

        def protected(w):
            (w / "web" / "a.txt").write_text("both\n"); (w / "web" / ".claude").mkdir(); (w / "web" / ".claude" / "s.json").write_text("{}")
        for edits in (other_repo, protected):
            res, _, out = self.run_merge(edits, {"a.txt": "branch\n"}, {"a.txt": "main\n"})
            self.assertEqual(res.status, "rejected", res.detail)
            self.assertFalse(out["moved"])

    def test_protected_conflicts_and_workflow_changes_go_to_a_person(self):
        for branch_files, main_files in (({".claude/s.json": "a\n"}, {".claude/s.json": "b\n"}),
                                         ({"a.txt": "branch\n"}, {".github/workflows/ci.yml": "on: push\n"})):
            res, prompts, out = self.run_merge(lambda w: None, branch_files, main_files)
            self.assertEqual(res.status, "needs-person", res.detail)
            self.assertEqual(prompts, [])
            self.assertFalse(out["moved"])

    def test_merge_mode_needs_a_factory_branch(self):
        from factory import runner
        cfg = CFG
        self.assertEqual(runner.run_task(cfg, GH(), "o/r", issue(), cfg.routes["low"], merge_base={"o/r": "main"}).status, "failed")
        self.assertEqual(runner.run_task(cfg, GH(), "o/r", issue(), cfg.routes["low"], fix_branch="main",
                                         merge_base={"o/r": "main"}).status, "failed")


if __name__ == "__main__":
    unittest.main()
