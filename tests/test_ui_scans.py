import sqlite3
import unittest

from factory import scanner
from factory.config import load
from test_ui_admin import AdminCase

REPO = "your-org/standalone-service"
SMELL = {"orig": "", "id": "hardcoded-url", "pattern": r"https?://(localhost|10[.])", "globs": "**/*.py, **/*.ts", "advice": "Move it into configuration.", "sample": ""}
SCAN = [("orig", ""), ("name", "weekly"), ("repo", REPO), ("when", "every"), ("every", "1w"), ("cron", "0 6 * * 1"), ("exclude", "vendor/**"),
        ("enabled", "1"), ("smell", "todo-debt"), ("smell", "hardcoded-url")]


class Scans(AdminCase):
    def setUp(self):
        super().setUp()
        self.cookie, self.csrf = self.session()

    def smell(self, path="/scans/smells/save", **over):
        return self.post(self.cookie, self.csrf, path, {**SMELL, **over})

    def cfg(self):
        return load(str(self.root / "config.toml"))

    def test_requires_login_and_csrf(self):
        for path in ("/scans", "/scans/edit", "/scans/smells", "/scans/smells/edit"):
            self.assertEqual(self.req("GET", path)[0], 303, path)
        for path in ("/scans/save", "/scans/delete", "/scans/run", "/scans/smells/save", "/scans/smells/delete", "/scans/smells/sample"):
            self.assertEqual(self.req("POST", path, "", cookie=self.cookie)[0], 403, path)

    def test_empty_pages_render(self):
        s, _, html = self.req("GET", "/scans", cookie=self.cookie)
        self.assertEqual(s, 200)
        self.assertIn("No scans yet", html)
        self.assertIn("Scans are switched off", html)
        _, _, html = self.req("GET", "/scans/smells", cookie=self.cookie)
        for sid in scanner.PRESETS:
            self.assertIn(sid, html)
        self.assertIn("Only the built-in smells so far", html)
        self.assertIn('name="pattern"', self.req("GET", "/scans/smells/edit", cookie=self.cookie)[2])
        self.assertIn('name="smell"', self.req("GET", "/scans/edit", cookie=self.cookie)[2])

    def test_save_smell_with_advice_and_use_it_in_a_scan(self):
        s, h, _ = self.smell()
        self.assertEqual((s, h["Location"]), (303, "/scans/smells?ok=smell_saved"))
        (entry,) = self.overrides()["scanner"]["smells"]
        self.assertEqual((entry["id"], entry["globs"], entry["advice"]), ("hardcoded-url", ["**/*.py", "**/*.ts"], "Move it into configuration."))
        self.assertTrue((self.state / "RESTART").exists())
        self.assertEqual(scanner.advice_for(self.cfg().scanner, "hardcoded-url"), "Move it into configuration.")
        s, h, _ = self.post(self.cookie, self.csrf, "/scans/save", SCAN)
        self.assertEqual((s, h["Location"]), (303, "/scans?ok=scan_saved"))
        (scan,) = self.cfg().scanner.scans
        self.assertEqual((scan.name, scan.every, scan.smells, scan.exclude), ("weekly", "1w", ("todo-debt", "hardcoded-url"), ("vendor/**",)))
        _, _, html = self.req("GET", "/scans", cookie=self.cookie)
        self.assertIn("weekly", html)
        self.assertIn("(yours)", html)

    def test_invalid_and_slow_patterns_are_refused_and_nothing_is_saved(self):
        for over, text in (({"pattern": "https?://(localhost|10[.]"}, "not a valid regular expression"), ({"pattern": "(a+)+$"}, "too slow or too broad"),
                           ({"pattern": r"(a)\1"}, "too slow or too broad"), ({"pattern": "x*"}, "empty line"), ({"id": "Bad Name"}, "Name"),
                           ({"id": "todo-debt"}, "already exists"), ({"globs": "../x"}, "Files"), ({"advice": "x" * 2001}, "too long")):
            s, _, html = self.smell(**over)
            self.assertEqual(s, 422, over)
            self.assertIn(text, html)
            self.assertIn('name="pattern"', html)                    # the form is shown again
        self.assertIn('aria-invalid="true"', self.smell(pattern="(")[2])
        self.assertIsNone(self.overrides().get("scanner"))

    def test_preset_advice_only(self):
        s, h, _ = self.post(self.cookie, self.csrf, "/scans/smells/save", {"orig": "todo-debt", "preset": "", "advice": "Open an issue and link it."})
        self.assertEqual(s, 303)
        self.assertEqual(self.overrides()["scanner"]["advice"], {"todo-debt": "Open an issue and link it."})
        self.assertEqual(scanner.advice_for(self.cfg().scanner, "todo-debt"), "Open an issue and link it.")
        self.assertIn("Open an issue and link it.", self.req("GET", "/scans/smells/edit?id=todo-debt", cookie=self.cookie)[2])
        self.assertEqual(self.post(self.cookie, self.csrf, "/scans/smells/save", {"orig": "todo-debt", "advice": "x" * 2001})[0], 422)
        self.post(self.cookie, self.csrf, "/scans/smells/save", {"orig": "todo-debt", "advice": ""})            # empty removes it again
        self.assertIsNone(self.overrides().get("scanner"))
        self.assertEqual(self.req("GET", "/scans/smells/edit?id=nope", cookie=self.cookie)[0], 404)

    def test_scan_validation(self):
        self.smell()
        for key, value, text in (("name", "Bad Name", "Name"), ("repo", "o/other", "Repository"), ("every", "30m", "Interval")):
            s, _, html = self.post(self.cookie, self.csrf, "/scans/save", [(k, value if k == key else v) for k, v in SCAN])
            self.assertEqual(s, 422, key)
            self.assertIn(text, html)
        s, _, html = self.post(self.cookie, self.csrf, "/scans/save", [(k, v) for k, v in SCAN if k != "smell"])
        self.assertEqual(s, 422)
        self.assertIn("Choose at least one smell", html)

    def test_delete_smell_refused_while_used_then_scan_and_smell_deleted(self):
        self.smell()
        self.post(self.cookie, self.csrf, "/scans/save", SCAN)
        self.assertEqual(self.post(self.cookie, self.csrf, "/scans/smells/delete", {"id": "hardcoded-url"})[0], 400)
        s, _, html = self.post(self.cookie, self.csrf, "/scans/smells/delete", {"id": "hardcoded-url", "confirm": "1"})
        self.assertEqual(s, 422)
        self.assertIn("weekly", html)
        self.assertEqual(self.post(self.cookie, self.csrf, "/scans/delete", {"name": "weekly"})[0], 400)
        self.assertEqual(self.post(self.cookie, self.csrf, "/scans/delete", {"name": "weekly", "confirm": "1"})[0], 303)
        self.assertEqual(self.post(self.cookie, self.csrf, "/scans/smells/delete", {"id": "hardcoded-url", "confirm": "1"})[0], 303)
        self.assertEqual(self.cfg().scanner.smells, ())

    def test_enable_needs_csrf_a_boolean_and_workers(self):
        self.assertEqual(self.req("POST", "/scans/enable", "enabled=1", cookie=self.cookie)[0], 403)
        self.assertEqual(self.post(self.cookie, self.csrf, "/scans/enable", {"enabled": "yes"})[0], 400)
        s, _, html = self.post(self.cookie, self.csrf, "/scans/enable", {"enabled": "1"})          # workers are off in the test config
        self.assertEqual(s, 422)
        self.assertIn("Workers", html)
        self.assertEqual(self.overrides().get("scanner"), None)
        self.assertFalse((self.state / "RESTART").exists())

    def test_enable_writes_the_override_when_workers_are_on(self):
        ov = self.root / "config.overrides.toml"
        ov.write_text("[workers]\nenabled = true\n")
        s, h, _ = self.post(self.cookie, self.csrf, "/scans/enable", {"enabled": "1"})
        self.assertEqual((s, h["Location"]), (303, "/scans?ok=scan_enabled"))
        self.assertIs(self.overrides()["scanner"]["enabled"], True)
        self.assertTrue((self.state / "RESTART").exists())
        self.assertIn("Turn scans off", self.req("GET", "/scans", cookie=self.cookie)[2])
        self.assertEqual(self.post(self.cookie, self.csrf, "/scans/enable", {"enabled": "0"})[0], 303)
        self.assertNotIn("scanner", self.overrides())

    def test_new_presets_are_listed_with_advice_and_none_is_added_to_a_scan(self):
        for sid in ("hardcoded-secret", "dynamic-eval", "deep-nesting", "test-title-not-user-story"):
            self.assertIn(sid, scanner.PRESETS)
            self.assertIn(sid, self.req("GET", "/scans/smells", cookie=self.cookie)[2])
        self.assertTrue(scanner.advice_for(self.cfg().scanner, "hardcoded-secret"))
        self.assertEqual(scanner.advice_for(self.cfg().scanner, "long-files"), "")

    def test_run_now_queues_a_request_for_the_orchestrator(self):
        self.smell()
        self.post(self.cookie, self.csrf, "/scans/save", SCAN)
        self.assertEqual(self.post(self.cookie, self.csrf, "/scans/run", {"name": "nope"})[0], 404)
        s, h, _ = self.post(self.cookie, self.csrf, "/scans/run", {"name": "weekly"})
        self.assertEqual((s, h["Location"]), (303, "/scans?ok=scan_run"))
        db = sqlite3.connect(self.db_path)
        self.assertEqual(scanner.requested(db), {"weekly"})
        db.close()
        self.assertIn("queued", self.req("GET", "/scans", cookie=self.cookie)[2])

    def test_sample_text_test_shows_matches_escaped_and_saves_nothing(self):
        s, _, html = self.smell("/scans/smells/sample", sample="a = 1\nu = 'http://localhost/<b>x</b>'\n")
        self.assertEqual(s, 200)
        self.assertIn("1 match(es) in 2 line(s)", html)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", html)
        self.assertNotIn("<b>x</b>", html)
        s, _, html = self.smell("/scans/smells/sample", pattern="(a+)+$", sample="aaaa")
        self.assertEqual(s, 422)
        self.assertIsNone(self.overrides().get("scanner"))

    def test_hand_written_entries_are_copied_into_the_overrides(self):
        path = self.root / "config.toml"
        path.write_text(path.read_text() + '\n[[scanner.smells]]\nid = "hand"\nname = "Hand"\npattern = "zzz"\n')
        self.smell()
        ids = [x["id"] for x in self.overrides()["scanner"]["smells"]]
        self.assertEqual(ids, ["hand", "hardcoded-url"])


if __name__ == "__main__":
    unittest.main()
