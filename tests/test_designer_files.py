import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from factory import main as m
from factory.config import DEFAULT_ROLES, Route
from factory.runner import RunResult, build_prompt, run_task
from test_designfiles import VALID
from test_runner import RunTaskEndToEnd

TICKET = {"number": 3, "title": "Add a reorder button", "body": "please"}
ROUTE = Route("claude-code", "sonnet", "medium")


class DesignerFlow(unittest.TestCase):
    _setup = RunTaskEndToEnd._setup

    def go(self, edits, role="designer", cfg_roles=None):
        t = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, t, True)
        cfg, gh, bare, p1, p2, g = self._setup(t, edits)
        if cfg_roles is not None:
            cfg = replace(cfg, roles=cfg_roles)
        with p1, p2:
            res = run_task(cfg, gh, "o/web", TICKET, ROUTE, role=role)
        return res, gh, bare, g, t

    @staticmethod
    def write(work, name, text):
        (work / "web" / "docs" / "design").mkdir(parents=True, exist_ok=True)
        (work / "web" / "docs" / "design" / name).write_text(text)

    def test_a_valid_mockup_becomes_a_draft_pr_with_only_that_file(self):
        res, gh, bare, g, t = self.go(lambda w: self.write(w, "factory-3-reorder.dc.html", VALID))
        self.assertEqual(res.status, "stage")
        self.assertIn("did the thing", res.output)                                     # the written document is still returned
        self.assertEqual([f["path"] for f in res.files], ["docs/design/factory-3-reorder.dc.html"])
        self.assertEqual((len(gh.prs), gh.drafts), (1, [True]))                        # a DRAFT pull request
        branch = gh.prs[0][1]
        self.assertTrue(branch.startswith("factory/design-3-"))
        shown = g("--git-dir", str(bare["o/web"]), "ls-tree", "-r", "--name-only", branch, cwd=t).stdout.split()
        self.assertEqual(sorted(shown), ["a.txt", "docs/design/factory-3-reorder.dc.html"])  # the base file plus exactly one new file
        self.assertEqual(g("--git-dir", str(bare["o/web"]), "rev-list", "--count", "main", cwd=t).stdout.strip(), "1")
        self.assertEqual(res.files[0]["url"], f"https://github.com/o/web/blob/{branch}/docs/design/factory-3-reorder.dc.html")

    def test_a_hostile_mockup_is_dropped_but_the_document_is_still_posted(self):
        bad = VALID.replace("<h1", '<script>alert(1)</script><h1', 1)
        res, gh, bare, g, t = self.go(lambda w: self.write(w, "factory-3-x.dc.html", bad))
        self.assertEqual((res.status, res.files, gh.prs), ("stage", [], []))
        self.assertIn("did the thing", res.output)
        self.assertIn("not published", res.notes)
        self.assertNotIn("factory/design", g("--git-dir", str(bare["o/web"]), "branch", "--list", cwd=t).stdout)

    def test_touching_anything_else_drops_the_whole_set(self):
        def edits(w):
            self.write(w, "factory-3-x.dc.html", VALID)
            (w / "web" / "a.txt").write_text("also edited\n")
        res, gh, bare, g, t = self.go(edits)
        self.assertEqual((res.files, gh.prs), ([], []))
        self.assertIn("not published", res.notes)

    def test_names_and_ticket_numbers_are_enforced(self):
        for name in ("factory-4-x.dc.html", "mine.dc.html", "factory-3-X.dc.html"):
            res, gh, *_ = self.go(lambda w, n=name: self.write(w, n, VALID))
            self.assertEqual((res.files, gh.prs), ([], []), name)

    def test_files_are_collected_even_when_gitignore_covers_the_folder(self):
        """Regression from a live run: a real repository git-ignores `design/` (a pattern that also matches docs/design). The agent created its
        file, git did not see it, and nothing was published. The sandbox now force-adds exactly <design_dir>/factory-*.dc.html."""
        def edits(w):
            (w / "web" / ".git" / "info").mkdir(parents=True, exist_ok=True)
            with open(w / "web" / ".git" / "info" / "exclude", "a") as f:
                f.write("design/\n")                                   # matches docs/design/ too, at any depth
            self.write(w, "factory-3-kept.dc.html", VALID)
            (w / "web" / "docs" / "design" / "notes.txt").write_text("not a design file: must stay ignored\n")
        res, gh, bare, g, t = self.go(edits)
        self.assertEqual([f["path"] for f in res.files], ["docs/design/factory-3-kept.dc.html"])
        shown = g("--git-dir", str(bare["o/web"]), "ls-tree", "-r", "--name-only", gh.prs[0][1], cwd=t).stdout.split()
        self.assertNotIn("docs/design/notes.txt", shown)                    # only the exact factory-*.dc.html pattern is forced

    def test_the_folder_setting_is_honoured_and_validated(self):
        custom = tuple(replace(r, design_dir="mockups") if r.name == "designer" else r for r in DEFAULT_ROLES)
        def edits(w):
            (w / "web" / "mockups").mkdir(exist_ok=True)
            (w / "web" / "mockups" / "factory-3-x.dc.html").write_text(VALID)
        res, gh, *_ = self.go(edits, cfg_roles=custom)
        self.assertEqual([f["path"] for f in res.files], ["mockups/factory-3-x.dc.html"])
        res, gh, *_ = self.go(lambda w: self.write(w, "factory-3-x.dc.html", VALID), cfg_roles=custom)       # wrong folder for that setting
        self.assertEqual((res.files, gh.prs), ([], []))
        base = open("config.example.toml").read()
        for bad in ("../etc", ".github/x", "/abs", "Has Caps"):
            with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
                f.write(base + f'\n[roles.designer]\ndesign_dir = "{bad}"\n')
            from factory.config import load
            with self.assertRaises(ValueError, msg=bad):
                load(f.name)

    def test_the_feature_can_be_switched_off_and_other_roles_never_publish(self):
        off = tuple(replace(r, design_files=False) if r.name == "designer" else r for r in DEFAULT_ROLES)
        res, gh, *_ = self.go(lambda w: self.write(w, "factory-3-x.dc.html", VALID), cfg_roles=off)
        self.assertEqual((res.status, res.files, gh.prs), ("stage", [], []))
        for role in ("analyst", "architect", "reviewer"):
            res, gh, *_ = self.go(lambda w: self.write(w, "factory-3-x.dc.html", VALID), role=role)
            self.assertEqual((res.files, gh.prs), ([], []), role)

    def test_the_prompt_only_offers_design_files_to_the_designer_when_enabled(self):
        from factory.config import Project, ProjectRepo
        pr = Project("p", (ProjectRepo("o/web", "web"),), "")
        on = build_prompt("t", "b", pr, "o/web", "designer", None, None, None, ("o/web", 3), True)
        self.assertIn("DESIGN FILES", on)
        self.assertIn("docs/design/factory-<ticket number>-<short-slug>.dc.html", on)
        self.assertIn("do NOT write into `design/`", on)
        self.assertIn("Its number is 3", on)
        self.assertIn("the only files you may create are the design files", on)
        self.assertNotIn("DESIGN FILES", build_prompt("t", "b", pr, "o/web", "designer", None, None, None, ("o/web", 3), False))
        self.assertNotIn("DESIGN FILES", build_prompt("t", "b", pr, "o/web", "analyst", None, None, None, ("o/web", 3), True))
        self.assertIn("do not create, edit or delete any files", build_prompt("t", "b", pr, "o/web", "designer", None, None, None, ("o/web", 3), False))


class TicketComment(unittest.TestCase):
    ROLE = DEFAULT_ROLES[1]

    def test_the_comment_links_the_files_from_validated_values_only(self):
        files = [{"repo": "o/web", "path": "design/factory-3-x.dc.html", "url": "https://github.com/o/web/blob/factory/design-3-t/design/factory-3-x.dc.html",
                  "pr": "https://github.com/o/web/pull/9"},
                 {"repo": "o/web", "path": "design/factory-3-y.dc.html", "url": "javascript:alert(1)", "pr": "https://evil.example/pull/1"}]
        body = m.stage_comment(self.ROLE, ROUTE, "# Design\nbody", None, files, "web: dropped @mallory ![x](http://evil/p.png)")
        self.assertIn("### Design files", body)
        self.assertIn("Draft PR: https://github.com/o/web/pull/9", body)
        self.assertIn("[`design/factory-3-x.dc.html`](https://github.com/o/web/blob/factory/design-3-t/", body)
        self.assertNotIn("javascript:", body)
        self.assertNotIn("evil.example", body)
        self.assertNotIn("@mallory", body)                                            # notes are sanitized like any agent text
        self.assertNotIn("evil/p.png", body)

    def test_no_files_means_no_section(self):
        self.assertNotIn("Design files", m.stage_comment(self.ROLE, ROUTE, "# Design", None))

    def test_dispatch_stage_puts_the_files_on_the_ticket(self):
        from test_roles import CFG, FakeClf, FakeGH, issue
        from factory import db as dbm
        gh = FakeGH({"factory:design": [issue(labels=["factory:design"])]})
        res = RunResult("stage", "ok", output="# Design", pr_url="https://github.com/o/r/pull/9",
                        files=[{"repo": "o/r", "path": "design/factory-5-x.dc.html", "pr": "https://github.com/o/r/pull/9",
                                "url": "https://github.com/o/r/blob/factory/design-5-t/design/factory-5-x.dc.html"}])
        with mock.patch.object(m.runner, "run_task", return_value=res), tempfile.TemporaryDirectory() as d:
            m.poll_once(replace(CFG, db_path=d + "/f.db"), gh, dbm.connect(":memory:"), FakeClf())
        comment = [c[1] for c in gh.calls if c[0] == "comment"][0]
        self.assertIn("### Design files", comment)
        self.assertIn("design/factory-5-x.dc.html", comment)


if __name__ == "__main__":
    unittest.main()
