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
