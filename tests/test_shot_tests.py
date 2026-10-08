"""Which test took each screenshot of a Playwright run: found on the worker, checked by the orchestrator (the worker is untrusted), stored
with the shot, and shown both ways: a scenario's screenshots on the Tests page, a screenshot's scenarios on the Screens review page."""
import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlencode

from factory import captures as CP, jobs
from factory import db as dbm
from factory import scenarios as SC
from factory import screenboard as SB

from test_review_notes import png
from test_ui import UiCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "worker"))
import worker as W                                                    # noqa: E402

REPO, OTHER = "your-org/shop-web", "your-org/standalone-service"
SHA = "b" * 40
PAY = "e2e/shop.spec.ts › Checkout › pays with a card"
HOME = "e2e/shop.spec.ts › loads the home page"
B64 = base64.b64encode(png(1280, 800)).decode()


def screens_job(db, repo=REPO, sha=SHA) -> int:
    jid = jobs.enqueue(db, repo, 0, sha, "", "playwright-screens", "any", 1000.0, json.dumps({"purpose": jobs.SCREENS_PURPOSE}))
    jobs.claim(db, "mac-1", "linux", ["playwright-screens"], 1000.0, 120, 900, 2)
    return jid


def result(artifacts, ids=(PAY, HOME)) -> dict:
    return {"status": "passed", "exit_code": 0, "log": "ok", "tests": [{"id": t, "outcome": "pass", "message": ""} for t in ids],
            "artifacts": [{"name": n, "png_b64": B64, **({"test": t} if t is not None else {})} for n, t in artifacts]}


class WorkerFinds(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.out = self.root / "results"
        self.out.mkdir()
        (self.root / "tests" / "e2e").mkdir(parents=True)
        (self.root / "tests" / "e2e" / "test_screens.py").write_text("")

    def test_a_playwright_screenshot_gets_the_test_whose_attachment_it_was_copied_from(self):
        shot = lambda f: {"name": "screenshot", "contentType": "image/png", "path": str(self.root / "web" / "test-results-screens" / f)}
        test = lambda *a: {"status": "expected", "results": [{"attachments": list(a)}]}
        report = {"config": {"rootDir": str(self.root / "web" / "e2e")}, "suites": [
            {"title": "shop.spec.ts", "file": "shop.spec.ts", "specs": [{"title": "loads the home page", "tests": [test(shot("home/test-finished-1.png"))]}],
             "suites": [{"title": "Checkout", "file": "shop.spec.ts", "specs": [
                 {"title": "pays with a card", "tests": [test(shot("pay/test-finished-1.png"), {"contentType": "text/plain", "path": "/x"})]}]}]}]}
        (self.out / "pw-web.json").write_text(json.dumps(report))
        src = self.root / "web" / "." / "test-results-screens"
        (self.out / "shots.tsv").write_text(f"{src}/home/test-finished-1.png\tweb-home-12345.png\n{src}/pay/test-finished-1.png\tweb-pay-67890.png\n"
                                            f"{src}/nobody.png\tweb-nobody-11111.png\nno tab here\n")
        got = W.collect_shot_tests(self.out, self.root, {"web/e2e/shop.spec.ts › loads the home page"})
        self.assertEqual(got, {"web-home-12345": "web/e2e/shop.spec.ts › loads the home page"})      # an id not in this run's results is left out
        got = W.collect_shot_tests(self.out, self.root, {"web/e2e/shop.spec.ts › loads the home page", "web/e2e/shop.spec.ts › Checkout › pays with a card"})
        self.assertEqual(got["web-pay-67890"], "web/e2e/shop.spec.ts › Checkout › pays with a card")

    def test_a_python_suite_names_its_test_the_same_way_as_its_junit_results(self):
        junit = ('<testsuites><testsuite><testcase classname="tests.e2e.test_screens" name="test_page[chromium-/-desktop]" '
                 'file="tests/e2e/test_screens.py"/></testsuite></testsuites>')
        tid = W.junit_tests(junit, self.root)[0][0]
        self.assertEqual(tid, "tests/e2e/test_screens.py::test_page")
        lines = [{"file": "home-desktop.png", "test": "tests/e2e/test_screens.py::test_page[chromium-/::x-desktop] (call)"},
                 {"file": "home-phone.png", "test": "tests/e2e/test_screens.py::test_page[phone]"},
                 {"file": "../escape.png", "test": "tests/e2e/test_screens.py::test_page"},
                 {"file": "sub/x.png", "test": "tests/e2e/test_screens.py::test_page"},
                 {"file": "outside.png", "test": "../../etc/test_x.py::test_page"},
                 {"file": "wrong.png", "test": 3}, ["not", "an", "object"]]
        (self.out / "e2e-shots.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\nnot json\n")
        self.assertEqual(W.collect_shot_tests(self.out, self.root, {tid}), {"home-desktop": tid, "home-phone": tid})
        self.assertEqual(W.collect_shot_tests(self.out, self.root, set()), {})

    def test_no_files_means_no_links(self):
        self.assertEqual(W.collect_shot_tests(self.out, self.root, {PAY}), {})


class OrchestratorChecks(unittest.TestCase):
    def setUp(self):
        self.db = dbm.connect(str(Path(tempfile.mkdtemp()) / "f.db"))

    def test_a_bad_test_on_a_shot_drops_only_the_link(self):
        bad = [("a-1", "/etc/passwd::x"), ("a-2", "../x.py::t"), ("a-3", "a\x07.py::t"), ("a-4", "x" * (jobs.MAX_TEST_ID + 1)),
               ("a-5", 7), ("a-6", ["x"]), ("a-7", "web/never-ran.spec.ts › x"), ("a-8", None), ("a-9", PAY)]
        res, why = jobs.validate_result(result(bad), jobs.MAX_SCREEN_ARTIFACTS)
        self.assertEqual(why, "")
        self.assertEqual(len(res["artifacts"]), 9)                                # every shot is kept
        self.assertEqual(res["artifact_tests"], {"a-9": PAY})

    def test_a_run_stores_each_shot_with_the_tests_that_took_it(self):
        jid = screens_job(self.db)
        body = result([("checkout-desktop-12345", PAY), ("checkout-desktop-67890", HOME), ("home-phone-11111", HOME), ("plain-22222", None)])
        self.assertEqual(jobs.complete(self.db, jid, "mac-1", body, 1100.0), "")
        k = SB.key(REPO, "checkout", "desktop", SHA)
        self.assertEqual(SB.tests_for_shot(self.db, k), sorted([PAY, HOME]))     # two names made one shot: it keeps both tests
        self.assertEqual(SB.shots_for_test(self.db, REPO, HOME), [k, SB.key(REPO, "home", "phone", SHA)])
        self.assertEqual(SB.shots_for_test(self.db, OTHER, HOME), [])
        self.assertEqual(SB.shots_for_test(self.db, REPO, ""), [])
        self.assertEqual(SB.tests_for_shot(self.db, SB.key(REPO, "plain", "run", SHA)), [])
        CP.import_run(self.db, jobs.get(self.db, jid), {"checkout-desktop-12345": png(1280, 800)}, {"checkout-desktop-12345": HOME})
        self.assertEqual(SB.tests_for_shot(self.db, k), [HOME])                  # the same commit run again: its links replace the old

    def test_pruned_shots_take_their_links_with_them(self):
        CP.import_run(self.db, {"repo": REPO, "base_sha": SHA}, {"home-desktop-12345": png(1280, 800)}, {"home-desktop-12345": HOME})
        SB.store(self.db, REPO, "home", "desktop", "c" * 40, png(1280, 800), now=9e12)    # a newer shot of the same screen
        SB.prune(self.db, set())
        self.assertIsNone(SB.image(self.db, SB.key(REPO, "home", "desktop", SHA)))
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM screen_shot_tests").fetchone()[0], 0)

    def test_an_existing_database_gets_the_table_and_keeps_its_shots(self):
        path = str(Path(tempfile.mkdtemp()) / "old.db")
        import sqlite3
        old = sqlite3.connect(path)
        old.execute("CREATE TABLE screen_shots (repo TEXT NOT NULL, page TEXT NOT NULL, view TEXT NOT NULL, sha TEXT NOT NULL, "
                    "png BLOB NOT NULL, created REAL NOT NULL, PRIMARY KEY (repo, page, view, sha))")
        old.execute("INSERT INTO screen_shots VALUES (?,?,?,?,?,?)", (REPO, "home", "desktop", SHA, png(1280, 800), 1.0))
        old.commit()
        old.close()
        db = dbm.connect(path)
        self.assertIsNotNone(SB.image(db, SB.key(REPO, "home", "desktop", SHA)))
        self.assertEqual(SB.tests_for_shot(db, SB.key(REPO, "home", "desktop", SHA)), [])


class PagesShow(UiCase):
    def setUp(self):
        super().setUp()
        p = self.root / "config.toml"
        p.write_text(p.read_text() + f'\n[workers]\nenabled = true\n[screens]\n[[screens.captures]]\nrepo = "{REPO}"\n'
                     f'[[screens.captures]]\nrepo = "{OTHER}"\n')
        SC.create(self.db, REPO, {"title": "Pay <b>now</b>", "pw_test": PAY})
        SC.create(self.db, REPO, {"title": "Pay again", "pw_test": PAY})
        SC.create(self.db, REPO, {"title": "Manual only"})
        SC.create(self.db, OTHER, {"title": "Other repo", "pw_test": PAY})
        jid = screens_job(self.db)
        shots = [(f"checkout-{v}-1234{i}", PAY) for i, v in enumerate(("desktop", "tablet", "phone"))] + [("home-desktop-55555", HOME)]
        jobs.complete(self.db, jid, "mac-1", result(shots), 1100.0)
        self.cookie, _ = self.session()

    def get(self, path):
        return self.req("GET", path, cookie=self.cookie)

    def test_a_scenario_shows_the_screenshots_its_test_took(self):
        s, _, html = self.get("/scenarios/view?" + urlencode({"repo": REPO, "ref": "TS-1"}))
        self.assertEqual(s, 200)
        for v in ("desktop", "tablet", "phone"):
            self.assertIn(f'href="/screens/review?{urlencode({"img": SB.key(REPO, "checkout", v, SHA)})}"', html)
        self.assertNotIn(urlencode({"img": SB.key(REPO, "home", "desktop", SHA)}), html)
        s, _, html = self.get("/scenarios/view?" + urlencode({"repo": REPO, "ref": "TS-3"}))
        self.assertEqual(s, 200)
        self.assertIn("No screenshots yet", html)
        s, _, html = self.get("/scenarios/view?" + urlencode({"repo": OTHER, "ref": "TS-1"}))
        self.assertIn("No screenshots yet", html)                                  # same test id, other repo: nothing
        self.assertNotIn("/screens/review?", html)

    def test_a_screenshot_lists_its_scenarios_escaped_and_from_its_own_repo_only(self):
        s, _, html = self.get("/screens/review?" + urlencode({"img": SB.key(REPO, "checkout", "desktop", SHA)}))
        self.assertEqual(s, 200)
        self.assertIn("Pay &lt;b&gt;now&lt;/b&gt;", html)
        self.assertNotIn("<b>now", html)
        self.assertIn("Pay again", html)
        self.assertIn(f'href="/scenarios/view?{urlencode({"repo": REPO, "ref": "TS-1"})}"'.replace("&", "&amp;"), html)
        self.assertIn("Passed", html)
        self.assertNotIn("Other repo", html)
        self.assertNotIn("Manual only", html)
        s, _, html = self.get("/screens/review?" + urlencode({"img": SB.key(REPO, "home", "desktop", SHA)}))
        self.assertNotIn("Pay again", html)
        self.assertIn("No linked scenarios", html)


if __name__ == "__main__":
    unittest.main()
