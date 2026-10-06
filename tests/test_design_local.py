"""The designer's design_pr = false: mockups are kept in the factory (database, UI, build and review agents), never on a draft PR."""
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from factory import db as dbm, designfiles, main as m, mockups
from factory.config import DEFAULT_ROLES, MockupsCfg
from factory.runner import run_task
from factory.ui import views
from test_designfiles import VALID
from test_designer_files import DesignerFlow, TICKET, ROUTE
from test_render import png

PATH, PREV = "docs/design/factory-3-x.dc.html", "docs/design/previews/factory-3-x.png"
LOCAL_CANVAS = {"repo": "o/web", "path": PATH, "url": "", "pr": "", "html": VALID}
LOCAL_PREVIEW = {"repo": "o/web", "path": PREV, "url": "", "pr": "", "preview": True, "png": png(300, 200)}


class KeptInTheFactory(DesignerFlow):
    def go_local(self, previews, edits=None):
        t = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, t, True)
        cfg, gh, bare, p1, p2, g = self._setup(t, edits or (lambda w: self.write(w, "factory-3-x.dc.html", VALID)))
        roles = tuple(replace(r, design_pr=False) if r.name == "designer" else r for r in DEFAULT_ROLES)
        cfg = replace(cfg, roles=roles, runner=replace(cfg.runner, render_previews=True))
        with p1, p2, mock.patch("factory.runner.preview_render", return_value=previews):
            res = run_task(cfg, gh, "o/web", TICKET, ROUTE, role="designer")
        return res, gh, bare, g, t

    def test_nothing_is_pushed_and_no_pull_request_is_opened(self):
        res, gh, bare, g, t = self.go_local({"factory-3-x": png(300, 200)})
        self.assertEqual((res.status, gh.prs, res.pr_url), ("stage", [], None))
        self.assertIn("did the thing", res.output)                                       # the written document is still returned
        branches = g("--git-dir", str(bare["o/web"]), "for-each-ref", "--format=%(refname:short)", "refs/heads", cwd=t).stdout.split()
        self.assertEqual(branches, ["main"])
        self.assertEqual(g("--git-dir", str(bare["o/web"]), "rev-list", "--count", "main", cwd=t).stdout.strip(), "1")

    def test_the_records_carry_the_canvas_and_the_rendered_image_with_no_url(self):
        res, *_ = self.go_local({"factory-3-x": png(300, 200)})
        self.assertEqual([f["path"] for f in res.files], [PATH, PREV])
        self.assertTrue(all(f["url"] == "" and f["pr"] == "" for f in res.files))
        self.assertEqual(res.files[0]["html"], VALID)
        self.assertTrue(res.files[1]["preview"] and res.files[1]["png"] == png(300, 200))
        self.assertTrue(all(designfiles.record_ok(f) and not designfiles.link_ok(f) for f in res.files))

    def test_a_canvas_that_fails_validation_is_still_dropped(self):
        bad = VALID.replace("<h1", "<script>alert(1)</script><h1", 1)
        res, gh, *_ = self.go_local({"factory-3-x": png()}, lambda w: self.write(w, "factory-3-x.dc.html", bad))
        self.assertEqual((res.files, gh.prs), ([], []))

    def test_no_render_keeps_the_canvas_and_says_no_preview_was_made(self):
        res, *_ = self.go_local({})
        self.assertEqual([f["path"] for f in res.files], [PATH])
        self.assertIn("no preview image", res.notes)


class Records(unittest.TestCase):
    def setUp(self):
        self.db = dbm.connect(":memory:")

    def test_record_ok_accepts_local_records_and_still_rejects_bad_ones(self):
        self.assertTrue(designfiles.record_ok(LOCAL_CANVAS) and designfiles.record_ok(LOCAL_PREVIEW))
        for bad in ({**LOCAL_CANVAS, "path": "docs/design/../x/factory-3-x.dc.html"}, {**LOCAL_CANVAS, "path": "docs/design/notes.txt"},
                    {**LOCAL_CANVAS, "repo": "not a repo"}, {**LOCAL_CANVAS, "pr": "https://evil.example/pull/1"},
                    {**LOCAL_CANVAS, "url": "javascript:alert(1)"}, {"repo": "o/web"}, None):
            self.assertFalse(designfiles.record_ok(bad), str(bad)[:60])
        self.assertFalse(designfiles.link_ok(LOCAL_CANVAS))                             # nothing without a GitHub url is ever linked

    def test_add_design_files_keeps_the_image_and_the_canvas(self):
        dbm.add_design_files(self.db, 1, [LOCAL_CANVAS, LOCAL_PREVIEW, {**LOCAL_CANVAS, "path": "x/y.txt"}])
        self.assertEqual([f["path"] for f in dbm.design_files_for_runs(self.db, [1])[1]], [PATH, PREV])
        self.assertEqual(dbm.mockup_image(self.db, "o/web", PREV), png(300, 200))
        self.assertEqual(dbm.design_source(self.db, "o/web", PATH), VALID)
        self.assertIsNone(dbm.design_source(self.db, "o/web", PREV))

    def test_a_forged_image_is_not_stored(self):
        dbm.add_design_files(self.db, 1, [{**LOCAL_PREVIEW, "png": b"<html>not a png</html>" * 10}])
        self.assertIsNone(dbm.mockup_image(self.db, "o/web", PREV))


class BuildGetsTheImage(unittest.TestCase):
    def test_fetch_uses_the_stored_image_without_asking_github(self):
        gh = mock.Mock()
        with tempfile.TemporaryDirectory() as d:
            names = mockups.fetch(gh, [LOCAL_PREVIEW], Path(d) / "m")
            self.assertEqual(names, ["factory-3-x.png"])
            self.assertEqual((Path(d) / "m" / "factory-3-x.png").read_bytes(), png(300, 200))
        gh.raw_file.assert_not_called()

    def test_fetch_still_downloads_when_there_is_no_stored_image(self):
        sha = "a" * 40
        rec = {"repo": "o/web", "path": PREV, "pr": "", "url": f"https://github.com/o/web/blob/{sha}/{PREV}"}
        gh = mock.Mock(raw_file=mock.Mock(return_value=png(300, 200)))
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(mockups.fetch(gh, [rec], Path(d)), ["factory-3-x.png"])
        gh.raw_file.assert_called_once()

    def test_fetch_skips_a_forged_stored_image_and_a_local_record_with_none(self):
        gh = mock.Mock()
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(mockups.fetch(gh, [{**LOCAL_PREVIEW, "png": b"nope" * 30}, {k: v for k, v in LOCAL_PREVIEW.items() if k != "png"}], Path(d)), [])
        gh.raw_file.assert_not_called()

    def test_the_gate_counts_a_local_preview(self):
        self.assertEqual(mockups.gate(MockupsCfg(), ["stage:designed"], [LOCAL_PREVIEW], False)[0], "ok")


class Shown(unittest.TestCase):
    def test_the_comment_does_not_promise_a_pull_request_or_link_anything(self):
        body = m.design_section([LOCAL_CANVAS, LOCAL_PREVIEW], "")
        self.assertIn("### Design files", body)
        self.assertIn(f"- `{PATH}`", body)
        self.assertIn("kept in the factory", body)
        self.assertNotIn("Draft PR", body)
        self.assertNotIn("](http", body)

    def test_a_pr_comment_is_unchanged_when_everything_is_linked(self):
        sha = "b" * 40
        f = {"repo": "o/web", "path": PATH, "url": f"https://github.com/o/web/blob/{sha}/{PATH}", "pr": "https://github.com/o/web/pull/9"}
        body = m.design_section([f], "")
        self.assertIn("Draft PR: https://github.com/o/web/pull/9", body)
        self.assertIn("merge the draft PR", body)

    def test_the_ui_shows_the_image_and_offers_a_download_for_the_canvas(self):
        self.assertIn("/mockup?", views.mockup_img(LOCAL_PREVIEW))
        links = views.design_links([LOCAL_CANVAS])
        self.assertIn("/design-source?", links)
        self.assertNotIn("github.com", links)
        self.assertEqual(views.design_links([LOCAL_PREVIEW]).count("/design-source"), 0)    # an image has nothing to download
        self.assertEqual(views.mockup_img({**LOCAL_PREVIEW, "path": "x/../y.png"}), "")


if __name__ == "__main__":
    unittest.main()
