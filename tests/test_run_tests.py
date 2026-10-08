"""Running tests from the Tests page: all of them or one feature's, the files that limit a run checked at each hop (factory, worker,
recipe), and the newest run's results and Playwright report linked on the page."""
import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlencode

from factory import captures as CP, jobs
from factory import db as dbm
from factory import scenarios as SC

from test_captures import FakeGh, finish, make_cfg
from test_run_results import RESULT, screens_job
from test_ui import UiCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "worker"))
import worker as W                                                       # noqa: E402

REPO = "your-org/shop-web"
RECIPE = Path(__file__).resolve().parent.parent / "worker" / "recipes" / "playwright-screens.sh"


class Files(unittest.TestCase):
    def test_only_plain_relative_files_are_kept(self):
        for good in ("e2e/shop.spec.ts", "tests/e2e/test_login.py", "a/b@c+d_e.spec.js"):
            self.assertEqual(jobs.clean_test_file(good), good)
        for bad in ("/etc/passwd", "../x.ts", "a/../b.ts", "a b.ts", "a;rm.ts", "*.ts", "-x.ts", "a//b.ts", "a/", "", None, "a\nb.ts", "x" * 201):
            self.assertIsNone(jobs.clean_test_file(bad), bad)

    def test_the_files_of_test_ids(self):
        self.assertEqual(jobs.files_of(["e2e/b.spec.ts › Checkout › pays", "e2e/a.spec.ts › x", "e2e/b.spec.ts › y",
                                        "tests/e2e/test_login.py::TestA::test_b", "/abs.ts › x", "my file.ts › x"]),
                         ["e2e/a.spec.ts", "e2e/b.spec.ts", "tests/e2e/test_login.py"])


class Queue(unittest.TestCase):
    def setUp(self):
        self.db = dbm.connect(":memory:")

    def test_a_request_for_some_files_becomes_a_job_limited_to_them(self):
        CP.request(self.db, [REPO], 10.0, files=["e2e/a.spec.ts", "../x.ts"])
        self.assertEqual(CP.requested_files(self.db, REPO), ["e2e/a.spec.ts"])
        self.assertEqual(CP.tick(make_cfg(), FakeGh(), self.db, 11.0), 1)
        job = jobs.get(self.db, jobs.recent(self.db)[0]["id"])
        self.assertEqual(json.loads(job["params"]), {"purpose": "screens", "files": ["e2e/a.spec.ts"]})
        self.assertTrue(jobs.is_screens(job))
        self.assertEqual(jobs.run_files(job), ["e2e/a.spec.ts"])

    def test_asking_for_everything_replaces_a_waiting_feature_run(self):
        CP.request(self.db, [REPO], 10.0, files=["e2e/a.spec.ts"])
        CP.request(self.db, [REPO], 12.0)
        self.assertEqual(CP.requested_files(self.db, REPO), [])
        CP.tick(make_cfg(), FakeGh(), self.db, 13.0)
        self.assertEqual(json.loads(jobs.get(self.db, jobs.recent(self.db)[0]["id"])["params"]), {"purpose": "screens"})

    def test_an_older_requests_table_gets_the_files_column(self):
        db = dbm.connect(":memory:")
        db.execute("DROP TABLE IF EXISTS capture_requests")
        db.execute("CREATE TABLE capture_requests (repo TEXT PRIMARY KEY, created REAL NOT NULL)")
        db.execute("INSERT INTO capture_requests VALUES (?, 1.0)", (REPO,))
        CP.ensure_tables(db)
        self.assertEqual((CP.requested(db), CP.requested_files(db, REPO)), ({REPO}, []))


class Worker(unittest.TestCase):
    def test_the_chosen_files_are_written_outside_the_checkout_one_a_line(self):
        tmp = Path(tempfile.mkdtemp())
        env, d = W.results_env({"params": {"purpose": "screens", "files": ["e2e/a.spec.ts", "tests/e2e/test_b.py"]}}, tmp)
        self.assertEqual(Path(env["FACTORY_TEST_FILES"]).read_text(), "e2e/a.spec.ts\ntests/e2e/test_b.py\n")
        self.assertEqual(env["FACTORY_RESULTS_DIR"], str(d))
        env, _ = W.results_env({"params": {"purpose": "screens"}}, Path(tempfile.mkdtemp()))
        self.assertNotIn("FACTORY_TEST_FILES", env)

    def test_a_file_that_is_not_plain_is_refused(self):
        for bad in (["../x.ts"], ["/x.ts"], ["a b.ts"], ["$(id).ts"], "e2e/a.spec.ts", ["a.ts"] * 51, [3]):
            with self.assertRaises(ValueError, msg=bad):
                W.results_env({"params": {"purpose": "screens", "files": bad}}, Path(tempfile.mkdtemp()))


class Recipe(unittest.TestCase):
    """The real script with stub npm/npx that log what they were asked."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bin, self.proj, self.log = self.tmp / "bin", self.tmp / "proj", self.tmp / "calls.log"
        self.bin.mkdir()
        stub = '#!/bin/sh\necho "$(basename "$0") $*" >> "$CALLS"\nexit 0\n'
        for name in ("npm", "npx", "node"):
            f = self.bin / name
            f.write_text(stub)
            f.chmod(f.stat().st_mode | stat.S_IEXEC)
        for d in ("web", "admin"):
            (self.proj / d).mkdir(parents=True)
            (self.proj / d / "package.json").write_text("{}")
            (self.proj / d / "package-lock.json").write_text("")
            (self.proj / d / "playwright.config.ts").write_text("export default {}")

    def run_recipe(self, files: list[str] | None):
        env = {"PATH": f"{self.bin}:/usr/bin:/bin", "CALLS": str(self.log), "HOME": str(self.tmp)}
        if files is not None:
            (self.tmp / "files.txt").write_text("".join(f + "\n" for f in files))
            env["FACTORY_TEST_FILES"] = str(self.tmp / "files.txt")
        r = subprocess.run([str(RECIPE)], cwd=self.proj, env=env, capture_output=True, text=True, timeout=60)
        calls = [c for c in (self.log.read_text().splitlines() if self.log.exists() else []) if c.startswith("npx --no-install playwright test")]
        return r.returncode, r.stdout + r.stderr, calls

    def test_without_a_choice_every_folder_runs_whole(self):
        code, out, calls = self.run_recipe(None)
        self.assertEqual(calls, ["npx --no-install playwright test --config playwright.screens.config.ts"] * 2, out)

    def test_only_the_chosen_files_run_and_a_folder_with_none_is_skipped(self):
        code, out, calls = self.run_recipe(["web/e2e/shop.spec.ts", "web/e2e/cart+1.spec.ts", "tests/e2e/test_x.py"])
        self.assertEqual(calls, [r"npx --no-install playwright test --config playwright.screens.config.ts e2e/shop\.spec\.ts e2e/cart\+1\.spec\.ts"], out)
        self.assertIn("none of the chosen test files is in admin; skipped", out)

    def test_none_of_the_chosen_files_in_a_suite_is_nothing_to_run(self):
        code, out, calls = self.run_recipe(["other/x.spec.ts"])
        self.assertEqual((code, calls), (2, []), out)


class Page(UiCase):
    def setUp(self):
        super().setUp()
        p = self.root / "config.toml"
        p.write_text(p.read_text() + f'\n[workers]\nenabled = true\n[screens]\n[[screens.captures]]\nrepo = "{REPO}"\n')
        self.cookie, self.csrf = self.session()
        SC.create(self.db, REPO, {"title": "Pay", "feature": "Checkout", "pw_test": "e2e/shop.spec.ts › Checkout › pays with a card"})
        SC.create(self.db, REPO, {"title": "Refund", "feature": "Checkout", "pw_test": "e2e/refund.spec.ts › refunds"})
        SC.create(self.db, REPO, {"title": "Save", "feature": "Profile", "pw_test": "tests/test_unit.py::TestThing::test_saves"})
        SC.create(self.db, REPO, {"title": "Look", "feature": "Manual"})

    def page(self, **q):
        return self.req("GET", "/scenarios?" + urlencode({"repo": REPO, **q}), cookie=self.cookie)[2]

    def start(self, **fields):
        return self.req("POST", "/scenarios/run", urlencode({"csrf": self.csrf, "repo": REPO, **fields}), cookie=self.cookie)

    def test_run_all_is_always_there_and_a_feature_adds_its_own_button(self):
        html = self.page()
        self.assertIn('<button name="scope" value="all">Run all tests</button>', html)
        self.assertIn("Pick a feature to run only its tests.", html)
        self.assertIn('data-src="/scenarios/runbar"', html)
        html = self.page(feature="Checkout")
        self.assertIn('value="feature">Run “Checkout” tests</button> <span class="muted">2 test files</span>', html)
        self.assertIn("None of its scenarios is linked to a test.", self.page(feature="Manual"))

    def test_running_a_feature_queues_only_its_files_worked_out_on_the_server(self):
        s, h, _ = self.start(scope="feature", feature="Checkout", files="../evil.ts")
        self.assertEqual((s, h["Location"]), (303, "/scenarios?" + urlencode({"repo": REPO, "feature": "Checkout"})))
        self.assertEqual(CP.requested_files(self.db, REPO), ["e2e/refund.spec.ts", "e2e/shop.spec.ts"])
        self.assertIn("starts at the factory", self.page(feature="Checkout"))
        self.assertIn("Run all tests</button>", self.page())
        self.assertIn(" disabled>Run all tests", self.page())                   # nothing more until this one is done
        self.assertEqual(self.start(scope="all", feature="Checkout")[0], 303)
        self.assertEqual((CP.requested(self.db), CP.requested_files(self.db, REPO)), ({REPO}, []))

    def test_a_feature_with_no_linked_test_queues_nothing(self):
        self.start(scope="feature", feature="Manual")
        self.assertEqual(CP.requested(self.db), set())

    def test_the_newest_run_links_its_results_and_report(self):
        jid = screens_job(self.db)
        jobs.complete(self.db, jid, "mac-1", RESULT, 1100.0)
        html = self.req("GET", "/scenarios/runbar?" + urlencode({"repo": REPO}), cookie=self.cookie)[2]
        self.assertIn("<strong>1 passed, 1 failed</strong> · all tests", html)
        self.assertIn(f'<a href="/screens/results?job={jid}">Results</a>', html)
        self.assertIn(f'href="/workerreport?job={jid}&amp;name=root" target="_blank" rel="noopener">Playwright report</a>', html)

    def test_a_run_from_an_older_recipe_says_why_there_is_no_report(self):
        jid = jobs.enqueue(self.db, REPO, 0, "b" * 40, "", "playwright-screens", "any", 1.0, json.dumps({"purpose": jobs.SCREENS_PURPOSE}))
        finish(self.db, jid, 1, "failed")
        self.assertIn("the worker's recipe is older", self.page())

    def test_a_repository_without_a_playwright_run_says_where_to_set_one_up(self):
        p = self.root / "config.toml"
        p.write_text(p.read_text().split("\n[workers]")[0])
        html = self.page()
        self.assertIn("Screens → Set up Playwright runs", html)
        self.assertNotIn("Run all tests", html)
        self.start(scope="all")
        self.assertEqual(CP.requested(self.db), set())


if __name__ == "__main__":
    unittest.main()
