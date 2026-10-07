import tempfile
import time
import unittest
from dataclasses import replace
from unittest import mock

from factory import db as dbm
from factory import main as m
from factory import waves
from factory.config import Project, ProjectRepo, PublishCfg, Route, load, load_raw, parse
from factory.runner import RunResult, build_prompt
from test_roles import CFG, FakeGH, issue
from test_runner import RunTaskEndToEnd

PROJ = Project("ln", (ProjectRepo("o/r", "web", depends_on=("o/pkg",)), ProjectRepo("o/pkg", "packages", publish=PublishCfg(packages=("@o/ui",))),
                      ProjectRepo("o/m", "mobile")))
WCFG = replace(CFG, repos=["o/r", "o/pkg", "o/m"], projects=(PROJ,))
ROUTE = Route("claude-code", "sonnet", "medium")
iso = lambda ago: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - ago))
MERGED_AT, AFTER, OLD = iso(600), iso(60), "2026-01-01T10:00:00Z"


class Config(unittest.TestCase):
    def test_example_config_publishes_and_depends(self):
        cfg = load("config.example.toml")
        p = cfg.projects[0]
        self.assertEqual(p.repo("your-org/shop-web").depends_on, ("your-org/shop-packages",))     # the short name is stored as owner/name
        self.assertEqual(p.repo("your-org/shop-packages").publish, PublishCfg("release", "", ("@your-org/ui",), 48))

    def bad(self, repos):
        raw = load_raw("config.example.toml")
        raw["projects"] = [{"name": "p", "repos": repos}]
        with self.assertRaises(ValueError):
            parse(raw)

    def test_rejects_bad_dependencies_and_publish_settings(self):
        self.bad([{"repo": "o/a", "depends_on": ["b"]}, {"repo": "o/b"}])                          # b publishes nothing
        self.bad([{"repo": "o/a", "depends_on": ["nope"]}])
        self.bad([{"repo": "o/a", "depends_on": ["a"], "publish": {}}])                            # itself
        self.bad([{"repo": "o/a", "publish": {"detect": "npm"}}])
        self.bad([{"repo": "o/a", "publish": {"detect": "workflow"}}])                             # no workflow file
        self.bad([{"repo": "o/a", "publish": {"detect": "workflow", "workflow": "../x.yml"}}])


class Split(unittest.TestCase):
    def test_depending_repo_waits_only_when_its_dependency_changed(self):
        self.assertEqual(waves.split(PROJ, ["o/r", "o/pkg", "o/m"]), (["o/pkg", "o/m"], ["o/r"]))
        self.assertEqual(waves.split(PROJ, ["o/r", "o/m"]), (["o/r", "o/m"], []))
        self.assertEqual(waves.upstream(PROJ, ["o/pkg", "o/m"], ["o/r"]), ["o/pkg"])

    def test_prompt_tells_the_agent_how_the_repos_relate(self):
        p = build_prompt("t", "b", PROJ, "o/r", released="pkg was published as release v1.4.0")
        self.assertIn("pkg/ : packages [publishes packages (@o/ui): when you change a package here, raise its version", p)
        self.assertIn("installs the published packages of pkg", p)
        self.assertIn("<published_dependencies>\npkg was published as release v1.4.0", p)


class Runner(RunTaskEndToEnd):
    def project_cfg(self, cfg):
        return replace(cfg, projects=(Project("proj", (ProjectRepo("o/web", "web", depends_on=("o/mobile",)),
                                                       ProjectRepo("o/mobile", "packages", publish=PublishCfg()))),))

    def test_only_the_publishing_repo_is_pushed_and_the_rest_is_held(self):
        from factory import runner
        def edits(work):
            (work / "web" / "a.txt").write_text("two\n"); (work / "mobile" / "b.txt").write_text("new\n")
        with tempfile.TemporaryDirectory() as t:
            cfg, gh, bare, p1, p2, g = self._setup(t, edits)
            with p1, p2:
                res = runner.run_task(self.project_cfg(cfg), gh, "o/web", {"number": 5, "title": "Bump", "body": "x"}, cfg.routes["low"])
            self.assertEqual(res.status, "pr", res.detail)
            self.assertEqual([r for r, _, _ in gh.prs], ["o/mobile"])
            self.assertEqual(res.held, ["o/web"])
            self.assertIn("web that use this are built after it is merged and published", gh.prs[0][2])
            self.assertNotIn("factory/", g("--git-dir", str(bare["o/web"]), "branch", "--list", cwd=t).stdout)     # nothing pushed to web
            self.assertEqual(gh.comments, [])                                          # one PR: no cross-links

    def test_a_second_wave_may_change_only_the_held_repos(self):
        from factory import runner
        def edits(work):
            (work / "web" / "a.txt").write_text("two\n"); (work / "mobile" / "b.txt").write_text("new\n")
        with tempfile.TemporaryDirectory() as t:
            cfg, gh, bare, p1, p2, g = self._setup(t, edits)
            with p1, p2:
                res = runner.run_task(self.project_cfg(cfg), gh, "o/web", {"number": 5, "title": "Bump", "body": "x"}, cfg.routes["low"],
                                      only=("o/web",), released="published")
            self.assertEqual(res.status, "rejected")
            self.assertIn("o/mobile", res.detail)
            self.assertEqual(gh.prs, [])

    def test_second_wave_opens_the_held_repo_pr(self):
        from factory import runner
        def edits(work):
            (work / "web" / "a.txt").write_text("two\n")
        with tempfile.TemporaryDirectory() as t:
            cfg, gh, bare, p1, p2, g = self._setup(t, edits)
            with p1, p2:
                res = runner.run_task(self.project_cfg(cfg), gh, "o/web", {"number": 5, "title": "Bump", "body": "x"}, cfg.routes["low"],
                                      only=("o/web",), released="published")
            self.assertEqual((res.status, res.held), ("pr", []))
            self.assertIn("Built after the packages it uses were merged and published", gh.prs[0][2])


class WaveGH(FakeGH):
    def __init__(self):
        super().__init__()
        self.pr = {"state": "open", "merged": False}
        self.rels, self.runs, self.ticket_state = [], [], "open"

    def get_issue(self, repo, num): return {"number": num, "state": self.ticket_state, "title": "Add thing", "body": "", "updated_at": "u", "labels": []}
    def get_pr(self, repo, n): return self.pr
    def releases(self, repo): return self.rels
    def tags(self, repo): return []
    def default_branch(self, repo): return "main"
    def workflow_runs(self, repo, wf, branch=""): return self.runs
    def contains(self, repo, ref, sha): return ref in ("v1.4.0", "c2")


class Tick(unittest.TestCase):
    def setUp(self):
        self.db, self.gh, self.notes, self.events, self.started = dbm.connect(":memory:"), WaveGH(), [], [], []
        waves.record(self.db, "o/r", 5, [("o/pkg", 12)], ["o/r"], ROUTE)

    def tick(self, cfg=WCFG, take=True):
        def submit(repo, num, ws, route):
            self.started.append((repo, num, [w["repo"] for w in ws], route))
            return take
        waves.tick(cfg, self.gh, self.db, lambda text, event="release": self.notes.append((event, text)),
                   lambda kind, msg, repo=None, issue=None: self.events.append(kind), submit)

    def states(self):
        return [w["state"] for w in waves.rows(self.db, "o/r", 5)]

    def test_waits_for_the_merge_then_the_release_then_starts_wave_two(self):
        self.tick()
        self.assertEqual((self.states(), self.started), (["merge"], []))
        self.gh.pr = {"state": "closed", "merged": True, "merge_commit_sha": "c1", "merged_at": MERGED_AT}
        self.tick()
        self.assertEqual(self.states(), ["publish"])
        self.assertIn("Waiting for a GitHub release that contains the merge", self.notes[-1][1])
        self.gh.rels = [{"tag_name": "v1.3.0", "published_at": "2026-09-01T00:00:00Z", "draft": False},            # before the merge
                        {"tag_name": "v1.4.0", "published_at": AFTER, "draft": True}]             # a draft
        self.tick()
        self.assertEqual((self.states(), self.started), (["publish"], []))
        self.gh.rels[1]["draft"] = False
        self.tick()
        self.assertEqual(self.started, [("o/r", 5, ["o/pkg"], ROUTE)])
        w = waves.rows(self.db, "o/r", 5)[0]
        self.assertEqual((w["state"], w["released"]), ("building", "release v1.4.0"))
        self.assertIn("published as release v1.4.0 (packages: @o/ui)", waves.context(WCFG, "o/r", [w]))
        self.assertIn("Change only these repos: r/", waves.context(WCFG, "o/r", [w]))

    def test_no_free_slot_waits_for_a_later_poll(self):
        self.gh.pr = {"state": "closed", "merged": True, "merge_commit_sha": "c1", "merged_at": MERGED_AT}
        self.gh.rels = [{"tag_name": "v1.4.0", "published_at": AFTER}]
        self.tick(take=False)
        self.assertEqual(self.states(), ["released"])
        self.tick()
        self.assertEqual(self.states(), ["building"])

    def test_closed_unmerged_pr_stops_waiting(self):
        self.gh.pr = {"state": "closed", "merged": False}
        self.tick()
        self.assertEqual(self.states(), ["closed"])
        self.assertIn(("rm", waves.WAITING), self.gh.calls)
        self.assertTrue(any(k == "comment" and "closed without being merged" in b for k, b in self.gh.calls))
        self.tick()
        self.assertEqual(self.started, [])

    def test_closed_ticket_stops_waiting(self):
        self.gh.ticket_state = "closed"
        self.tick()
        self.assertEqual(self.states(), ["closed"])

    def test_failed_publish_workflow_and_a_late_publish_are_reported_once(self):
        pkg = replace(PROJ.repos[1], publish=PublishCfg("workflow", "publish.yml", timeout_hours=1))
        cfg = replace(WCFG, projects=(replace(PROJ, repos=(PROJ.repos[0], pkg, PROJ.repos[2])),))
        self.gh.pr = {"state": "closed", "merged": True, "merge_commit_sha": "c1", "merged_at": OLD}
        self.gh.runs = [{"status": "completed", "conclusion": "failure", "head_sha": "c2", "created_at": "2026-01-01T10:05:00Z", "html_url": "u"}]
        self.tick(cfg)
        self.tick(cfg)
        self.tick(cfg)
        self.assertEqual([e for e, t in self.notes].count("failure"), 1)
        self.assertEqual([e for e, t in self.notes].count("needs_human"), 1)        # merged long ago (OLD): late, once
        self.gh.runs.insert(0, {"status": "completed", "conclusion": "success", "head_sha": "c2", "created_at": "2026-01-01T10:30:00Z"})
        self.tick(cfg)
        self.assertEqual(self.states(), ["building"])


class Dispatch(unittest.TestCase):
    def run_dispatch(self, result, conn, **kw):
        gh = WaveGH()
        with mock.patch.object(m.runner, "run_task", return_value=result) as rt, tempfile.TemporaryDirectory() as d:
            res = m.dispatch(replace(WCFG, db_path=d + "/f.db"), gh, "o/r", issue(labels=["factory:ready"]), ROUTE, conn=conn, **kw)
        return res, gh, rt

    def test_a_held_build_records_the_wait_and_labels_the_ticket(self):
        conn = dbm.connect(":memory:")
        res, gh, _ = self.run_dispatch(RunResult("pr", "1 pull request(s) opened", "https://github.com/o/pkg/pull/12", held=["o/r"]), conn)
        w = waves.rows(conn, "o/r", 5)
        self.assertEqual([(x["repo"], x["pr"], x["held"], x["state"]) for x in w], [("o/pkg", 12, "o/r", "merge")])
        self.assertIn(("add", (waves.WAITING,)), gh.calls)
        body = [b for k, b in gh.calls if k == "comment"][0]
        self.assertIn("once o/pkg#12 is merged and published (a GitHub release that contains the merge)", body)

    def test_wave_two_changes_only_the_held_repos_and_finishes_the_wait(self):
        conn = dbm.connect(":memory:")
        waves.record(conn, "o/r", 5, [("o/pkg", 12)], ["o/r"], ROUTE)
        waves.set_state(conn, "o/r", 5, "building", released="release v1.4.0", merge_sha="c1")
        res, gh, rt = self.run_dispatch(RunResult("pr", "1 pull request(s) opened", "https://github.com/o/r/pull/3"), conn,
                                        wave=waves.rows(conn, "o/r", 5))
        self.assertEqual(rt.call_args.kwargs["only"], ("o/r",))
        self.assertIn("published as release v1.4.0 (packages: @o/ui)", rt.call_args.kwargs["released"])
        self.assertEqual([x["state"] for x in waves.rows(conn, "o/r", 5)], ["done"])
        self.assertIn(("rm", waves.WAITING), gh.calls)

    def test_build_label_after_a_failed_second_wave_retries_it(self):
        conn = dbm.connect(":memory:")
        waves.record(conn, "o/r", 5, [("o/pkg", 12)], ["o/r"], ROUTE)
        waves.set_state(conn, "o/r", 5, "failed")
        _, _, rt = self.run_dispatch(RunResult("rejected", "nope"), conn)
        self.assertEqual(rt.call_args.kwargs["only"], ("o/r",))
        self.assertEqual([x["state"] for x in waves.rows(conn, "o/r", 5)], ["failed"])

    def test_a_new_full_build_drops_an_earlier_wait(self):
        conn = dbm.connect(":memory:")
        waves.record(conn, "o/r", 5, [("o/pkg", 12)], ["o/r"], ROUTE)
        _, _, rt = self.run_dispatch(RunResult("pr", "1", "https://github.com/o/r/pull/3"), conn)
        self.assertNotIn("only", rt.call_args.kwargs)
        self.assertEqual([x["state"] for x in waves.rows(conn, "o/r", 5)], ["closed"])

    def test_rate_limited_second_wave_waits_without_relabeling(self):
        conn = dbm.connect(":memory:")
        waves.record(conn, "o/r", 5, [("o/pkg", 12)], ["o/r"], ROUTE)
        waves.set_state(conn, "o/r", 5, "building")
        _, gh, _ = self.run_dispatch(RunResult("rate-limited", "limit"), conn, wave=waves.rows(conn, "o/r", 5))
        self.assertEqual([x["state"] for x in waves.rows(conn, "o/r", 5)], ["released"])
        self.assertNotIn(("add", ("factory:ready",)), gh.calls)


class SettingsKeepsDependencies(unittest.TestCase):
    def test_saving_projects_from_the_ui_keeps_publish_and_depends_on(self):
        from pathlib import Path
        from factory.ui import settings as S
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.toml"
            p.write_text(open("config.example.toml").read())
            form = {"p0_name": "shop", "p0_desc": "x"}
            for j, (repo, role) in enumerate([("your-org/shop-web", "web, edited"), ("your-org/shop-packages", "pkgs")]):
                form[f"p0_r{j}_repo"], form[f"p0_r{j}_role"] = repo, role
            captured = {}
            with mock.patch.object(S, "_commit", lambda path, sd, ov: captured.update(ov)):
                S.save_projects(str(p), Path(d), form)
            repos = captured["projects"][0]["repos"]
            self.assertEqual(repos[0]["depends_on"], ["shop-packages"])
            self.assertEqual(repos[0]["role"], "web, edited")
            self.assertEqual(repos[1]["publish"]["detect"], "release")
            del form["p0_r1_repo"]                                              # the packages repo removed: the dependency goes too
            with mock.patch.object(S, "_commit", lambda path, sd, ov: captured.update(ov)):
                S.save_projects(str(p), Path(d), form)
            self.assertNotIn("depends_on", captured["projects"][0]["repos"][0])


if __name__ == "__main__":
    unittest.main()
