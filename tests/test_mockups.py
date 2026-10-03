import tempfile
import unittest
from pathlib import Path

from factory import mockups
from factory.config import MockupsCfg
from factory.render import PNG_MAGIC

PNG = PNG_MAGIC + b"\x00\x00\x00\rIHDR" + (100).to_bytes(4, "big") + (100).to_bytes(4, "big") + b"\x08\x02\x00\x00\x00" + b"x" * 30
SHA = "a" * 40
PREV = {"repo": "o/r", "path": "docs/design/previews/factory-5-home.png", "pr": "",
        "url": f"https://github.com/o/r/blob/{SHA}/docs/design/previews/factory-5-home.png"}


class Gate(unittest.TestCase):
    cfg = MockupsCfg()

    def test_ok_when_there_is_a_preview_or_no_design_stage(self):
        self.assertEqual(mockups.gate(self.cfg, ["stage:designed"], [PREV], False)[0], "ok")
        self.assertEqual(mockups.gate(self.cfg, [], [], False)[0], "ok")

    def test_blocks_a_designed_ticket_without_mockups(self):
        v, msg = mockups.gate(self.cfg, ["stage:designed"], [], False)
        self.assertEqual(v, "block")
        self.assertIn("factory:skip-mockup", msg)

    def test_designer_saying_no_screens_is_not_blocked(self):
        self.assertEqual(mockups.gate(self.cfg, ["stage:designed"], [], True)[0], "ok")

    def test_approval_when_required(self):
        c = MockupsCfg(require_approval=True)
        self.assertEqual(mockups.gate(c, ["stage:designed"], [PREV], False)[0], "block")
        self.assertEqual(mockups.gate(c, ["stage:designed", "factory:design-approved"], [PREV], False)[0], "ok")
        self.assertEqual(mockups.gate(c, ["stage:designed"], [PREV], False, pr_merged=True)[0], "ok")
        self.assertEqual(mockups.gate(c, ["stage:designed", "factory:skip-mockup"], [PREV], False)[0], "ok")
        self.assertEqual(mockups.gate(c, [], [PREV], False)[0], "ok")        # no design stage: nothing to approve

    def test_bypass_label_and_modes(self):
        self.assertEqual(mockups.gate(self.cfg, ["stage:designed", "factory:skip-mockup"], [], False)[0], "ok")
        self.assertEqual(mockups.gate(MockupsCfg(mode="warn"), ["stage:designed"], [], False)[0], "warn")
        self.assertEqual(mockups.gate(MockupsCfg(mode="off"), ["stage:designed"], [], False)[0], "ok")


class Fetch(unittest.TestCase):
    class GH:
        def __init__(self, data): self.data, self.calls = data, []
        def raw_file(self, repo, path, ref): self.calls.append((repo, path, ref)); return self.data

    def test_fetches_by_commit_and_writes_the_png(self):
        with tempfile.TemporaryDirectory() as t:
            gh = self.GH(PNG)
            names = mockups.fetch(gh, [PREV], Path(t) / "mockups")
            self.assertEqual(names, ["factory-5-home.png"])
            self.assertEqual(gh.calls, [("o/r", PREV["path"], SHA)])
            self.assertEqual((Path(t) / "mockups" / names[0]).read_bytes(), PNG)

    def test_rejects_non_png_bad_link_and_failed_fetch(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(mockups.fetch(self.GH(b"<html>"), [PREV], Path(t)), [])
            self.assertEqual(mockups.fetch(self.GH(PNG), [{**PREV, "url": "https://evil.example/x.png"}], Path(t)), [])
            class Boom:
                def raw_file(self, *a): raise OSError("no")
            self.assertEqual(mockups.fetch(Boom(), [PREV], Path(t)), [])

    def test_prompt_section(self):
        self.assertEqual(mockups.prompt_section([]), "")
        self.assertIn("/task/mockups/a.png", mockups.prompt_section(["a.png"]))
        self.assertIn("finding", mockups.prompt_section(["a.png"], reviewer=True))


class Recorded(unittest.TestCase):
    def test_latest_design_run_previews_only(self):
        from factory import db as dbm
        with tempfile.TemporaryDirectory() as t:
            db = dbm.connect(str(Path(t) / "f.db"))
            self.assertEqual(dbm.mockup_previews(db, "o/r", 5), [])
            rid = dbm.start_run(db, "stage", "o/r", 5, "T", "claude-code", "sonnet", "medium", "", "designer")
            canvas = {**PREV, "path": "docs/design/factory-5-home.dc.html", "url": PREV["url"].replace("previews/", "").replace(".png", ".dc.html")}
            dbm.add_design_files(db, rid, [canvas, PREV])
            got = dbm.mockup_previews(db, "o/r", 5)
            self.assertEqual([g["path"] for g in got], [PREV["path"]])
            self.assertEqual(dbm.mockup_previews(db, "o/r", 6), [])

    def test_png_is_stored_for_the_ui(self):
        from factory import db as dbm
        from factory.ui import views
        with tempfile.TemporaryDirectory() as t:
            db = dbm.connect(str(Path(t) / "f.db"))
            rid = dbm.start_run(db, "stage", "o/r", 5, "T", "claude-code", "sonnet", "medium", "", "designer")
            dbm.add_design_files(db, rid, [{**PREV, "png": PNG}])
            self.assertEqual(dbm.mockup_image(db, "o/r", PREV["path"]), PNG)
            self.assertIsNone(dbm.mockup_image(db, "o/r", "docs/design/previews/factory-5-other.png"))
            dbm.add_design_files(db, rid, [{**PREV, "path": "docs/design/previews/factory-5-bad.png", "png": b"<html>",
                                            "url": PREV["url"].replace("home", "bad")}])
            self.assertIsNone(dbm.mockup_image(db, "o/r", "docs/design/previews/factory-5-bad.png"))
            html = views.mockup_img(PREV)
            self.assertIn("/mockup?repo=o%2Fr", html)
            self.assertEqual(views.mockup_img({**PREV, "path": "x/../etc.png"}), "")


if __name__ == "__main__":
    unittest.main()


class Dispatch(unittest.TestCase):
    """main.dispatch: the mockup gate and the single screen fix round (run_task is faked)."""
    def run_dispatch(self, labels, results, cfg=None, designer_doc=""):
        import sys
        from unittest import mock
        from dataclasses import replace
        sys.path.insert(0, str(Path(__file__).parent))
        from test_roles import CFG, FakeGH, issue
        from factory import main as m
        gh = FakeGH(comments=[{"user": {"login": "bot"}, "body": "<!-- factory:stage=designer -->\n" + designer_doc}] if designer_doc else [])
        fake = mock.Mock(side_effect=results)
        with tempfile.TemporaryDirectory() as d, mock.patch.object(m.runner, "run_task", fake):
            cfg = replace(cfg or CFG, db_path=d + "/f.db")
            m._ev = lambda: None
            res = m.dispatch(cfg, gh, "o/r", issue(5, labels), cfg.routes["low"])
        return res, fake, gh

    def test_blocked_without_a_mockup_and_nothing_runs(self):
        res, fake, gh = self.run_dispatch(["stage:designed"], [])
        self.assertEqual(res.status, "failed")
        fake.assert_not_called()
        self.assertTrue(any(c[0] == "comment" and "factory:skip-mockup" in c[1] for c in gh.calls))

    def test_bypass_label_builds(self):
        from factory.runner import RunResult
        res, fake, _ = self.run_dispatch(["stage:designed", "factory:skip-mockup"], [RunResult("no-change", "x")])
        fake.assert_called_once()

    def test_one_screen_fix_round_then_stop(self):
        from factory.runner import RunResult
        bad = RunResult("failed", "screens", screen_failure={"text": "t", "diffs": {}, "built": {}})
        res, fake, _ = self.run_dispatch([], [bad, RunResult("failed", "screens", screen_failure={"text": "t", "diffs": {}, "built": {}})])
        self.assertEqual(fake.call_count, 2)
        self.assertIsNone(fake.call_args_list[0].kwargs.get("screen_retry"))
        self.assertEqual(fake.call_args_list[1].kwargs["screen_retry"]["text"], "t")
        self.assertEqual(res.status, "failed")


class Images(unittest.TestCase):
    def test_run_images_roundtrip_and_view(self):
        from factory import db as dbm
        from factory.ui import views
        with tempfile.TemporaryDirectory() as t:
            db = dbm.connect(str(Path(t) / "f.db"))
            dbm.add_run_images(db, 7, [{"kind": "built", "name": "home-desktop", "png": PNG}, {"kind": "diff", "name": "home-desktop", "png": PNG},
                                      {"kind": "evil", "name": "x", "png": PNG}, {"kind": "built", "name": "../x", "png": PNG},
                                      {"kind": "built", "name": "bad", "png": b"nope"}])
            self.assertEqual([(i["kind"], i["name"]) for i in dbm.run_images(db, 7)], [("built", "home-desktop"), ("diff", "home-desktop")])
            self.assertEqual(dbm.run_image(db, 7, "built", "home-desktop"), PNG)
            self.assertIsNone(dbm.run_image(db, 8, "built", "home-desktop"))
            html = views.run_images_html(7, dbm.run_images(db, 7))
            self.assertIn("/runimg?run=7&amp;kind=built&amp;name=home-desktop", html)
            self.assertEqual(views.run_images_html(7, []), "")

    def test_screen_retry_prompt_and_reviewer_prompt(self):
        from factory import runner
        from factory.config import Project, ProjectRepo
        p = Project("p", (ProjectRepo("o/r"),), "d")
        txt = runner.build_prompt("t", "b", p, "o/r", None, screen_text="home-desktop: 4% differ")
        self.assertIn("/task/diffs", txt)
        self.assertIn("4% differ", txt)
        rv = runner.build_prompt("t", "b", p, "o/r", "reviewer", mockups=["a.png"], built=["home-desktop.png"])
        self.assertIn("/task/built/home-desktop.png", rv)
