"""Per-test results and HTML reports of a Playwright run: read on the worker, checked and stored by the orchestrator, recorded on the
scenarios of the Tests register, and shown (the report sandboxed) on the Screens board."""
import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path

from factory import jobs
from factory import scenarios as SC
from test_ui import UiCase

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "worker"))
import worker as W                                                    # noqa: E402

REPO = "your-org/shop-web"


def playwright_json(root: Path) -> dict:
    """The shape of Playwright's JSON reporter: file suites (relative to config.rootDir), nested describe suites, a test per project."""
    test = lambda status, msg="": {"projectName": "chromium", "status": status,
                                    "results": [{"status": "failed" if msg else "passed", "error": {"message": msg} if msg else None}]}
    return {"config": {"rootDir": str(root / "web" / "e2e")}, "suites": [
        {"title": "shop.spec.ts", "file": "shop.spec.ts", "specs": [
            {"title": "loads the home page", "tests": [test("expected"), {**test("expected"), "projectName": "firefox"}]}],
         "suites": [{"title": "Checkout", "file": "shop.spec.ts", "specs": [
             {"title": "pays with a card", "tests": [test("unexpected", "\x1b[31mExpected: \"Card expired\"\x1b[39m\nReceived: \"Payment failed\""),
                                                     {**test("expected"), "projectName": "firefox"}]},
             {"title": "is skipped", "tests": [test("skipped")]},
             {"title": "flaky one", "tests": [test("flaky")]}]}]},
        {"title": "../../outside.spec.ts", "file": "../../../outside.spec.ts", "specs": [{"title": "x", "tests": [test("expected")]}]}]}


JUNIT = """<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">
<testcase classname="tests.e2e.test_scenarios" name="test_login[desktop]" file="tests/e2e/test_scenarios.py" time="1"/>
<testcase classname="tests.e2e.test_scenarios" name="test_login[phone]" file="tests/e2e/test_scenarios.py" time="1">
  <failure message="AssertionError: not visible">trace line</failure></testcase>
<testcase classname="tests.test_unit.TestThing" name="test_saves" time="0"/>
<testcase classname="tests.test_unit.TestThing" name="test_later" time="0"><skipped message="later"/></testcase>
<testcase classname="nowhere.test_gone" name="test_x" time="0"/>
</testsuite></testsuites>"""


class WorkerReads(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_unit.py").write_text("")

    def test_playwright_json_gives_repo_relative_ids_merged_across_browsers(self):
        got = W.merge_tests(W.playwright_tests(playwright_json(self.root), self.root))
        self.assertEqual({t["id"]: t["outcome"] for t in got}, {
            "web/e2e/shop.spec.ts › loads the home page": "pass",
            "web/e2e/shop.spec.ts › Checkout › pays with a card": "fail",
            "web/e2e/shop.spec.ts › Checkout › flaky one": "pass"})             # skipped and outside the checkout are left out
        fail = next(t for t in got if t["outcome"] == "fail")
        self.assertEqual(fail["message"], 'Expected: "Card expired"\nReceived: "Payment failed"')    # colour codes gone

    def test_junit_gives_pytest_node_ids_and_a_failing_parameter_fails_the_test(self):
        got = {t["id"]: t for t in W.merge_tests(W.junit_tests(JUNIT, self.root))}
        self.assertEqual(sorted(got), ["tests/e2e/test_scenarios.py::test_login", "tests/test_unit.py::TestThing::test_saves"])
        self.assertEqual(got["tests/e2e/test_scenarios.py::test_login"]["outcome"], "fail")
        self.assertIn("not visible", got["tests/e2e/test_scenarios.py::test_login"]["message"])
        self.assertEqual(W.junit_tests("<not xml", self.root), [])

    def test_collect_reads_the_results_folder_and_only_index_html_of_each_report(self):
        out = self.root / "results"
        (out / "report-web").mkdir(parents=True)
        (out / "report-web" / "index.html").write_text("<html>report</html>")
        (out / "report-web" / "data").mkdir()
        (out / "report-bad_name").mkdir()
        (out / "report-bad_name" / "index.html").write_text("x")
        (out / "pw-web.json").write_text(json.dumps(playwright_json(self.root)))
        (out / "junit.xml").write_text(JUNIT)
        tests, reports = W.collect_results(out, self.root)
        self.assertEqual(len(tests), 5)
        self.assertEqual([(r["name"], base64.b64decode(r["html_b64"])) for r in reports], [("web", b"<html>report</html>")])
        ok, why = jobs.validate_tests(tests)
        self.assertEqual(why, "")

    def test_a_result_too_big_drops_the_reports_then_the_messages(self):
        big = {"status": "failed", "log": "", "tests": [{"id": "a.py::t", "outcome": "fail", "message": "m" * 10}],
               "reports": [{"name": "r", "html_b64": "x" * (W.MAX_RESULT_BODY + 1)}]}
        fitted = W.fit_result(big)
        self.assertEqual(fitted["reports"], [])
        self.assertIn("left out", fitted["log"])
        self.assertEqual(fitted["tests"][0]["message"], "m" * 10)

    def test_only_a_playwright_run_gets_a_results_folder(self):
        tmp = Path(tempfile.mkdtemp())
        self.assertEqual(W.results_env({"params": {"purpose": "lockfile"}}, tmp), ({}, None))
        env, d = W.results_env({"params": {"purpose": "screens"}}, tmp)
        self.assertEqual(env, {"FACTORY_RESULTS_DIR": str(d)})
        self.assertTrue(d.is_dir())


def screens_job(db, worker="mac-1") -> int:
    jid = jobs.enqueue(db, REPO, 0, "b" * 40, "", "playwright-screens", "any", 1000.0, json.dumps({"purpose": jobs.SCREENS_PURPOSE}))
    jobs.claim(db, worker, "linux", ["playwright-screens"], 1000.0, 120, 900, 2)
    return jid


RESULT = {"status": "failed", "exit_code": 1, "log": "1 failed",
          "tests": [{"id": "e2e/shop.spec.ts › Checkout › pays with a card", "outcome": "fail", "message": "Expected <b>expired</b>"},
                    {"id": "tests/test_unit.py::TestThing::test_saves", "outcome": "pass", "message": ""}],
          "reports": [{"name": "root", "html_b64": base64.b64encode(b"<script>document.title='pw'</script>").decode()}]}


class OrchestratorKeeps(unittest.TestCase):
    def setUp(self):
        from factory import db as dbm
        self.db = dbm.connect(str(Path(tempfile.mkdtemp()) / "f.db"))

    def test_bad_tests_and_reports_are_refused(self):
        for bad in ([{"id": "/etc/passwd::x", "outcome": "pass"}], [{"id": "../x.py::t", "outcome": "pass"}],
                    [{"id": "a.py::t", "outcome": "skipped"}], [{"id": "a\nb", "outcome": "pass"}],
                    [{"id": "a.py::t", "outcome": "pass"}] * 2, "nope"):
            self.assertIsNone(jobs.validate_tests(bad)[0], bad)
        self.assertEqual(jobs.validate_tests([{"id": "a.spec.ts › x/../y", "outcome": "pass"}])[1], "")     # a title may say anything
        for bad in ([{"name": "../x", "html_b64": "eA=="}], [{"name": "r", "html_b64": "not base64!"}], [{"name": "r", "html_b64": ""}],
                    [{"name": f"r{i}", "html_b64": "eA=="} for i in range(jobs.MAX_REPORTS + 1)]):
            self.assertIsNone(jobs.validate_reports(bad)[0], bad)

    def test_a_run_keeps_its_results_and_reports_and_records_linked_scenarios_once(self):
        SC.create(self.db, REPO, {"title": "Pay", "pw_test": "e2e/shop.spec.ts › Checkout › pays with a card"})
        SC.create(self.db, REPO, {"title": "Save", "pw_test": "tests/test_unit.py::TestThing::test_saves"})
        SC.create(self.db, REPO, {"title": "Old", "pw_test": "tests/test_unit.py::TestThing::test_saves", "status": "retired"})
        SC.create(self.db, REPO, {"title": "Manual only"})
        jid = screens_job(self.db)
        self.assertEqual(jobs.complete(self.db, jid, "mac-1", RESULT, 1100.0), "")
        job = jobs.get(self.db, jid)
        self.assertEqual([t["outcome"] for t in jobs.tests(job)], ["fail", "pass"])
        self.assertEqual(jobs.report_names(self.db, jid), ["root"])
        self.assertIn(b"<script>", jobs.report(self.db, jid, "root"))
        rows = {r["ref"]: r for r in SC.listing(self.db, REPO)}
        self.assertEqual({k: r["last_result"] for k, r in rows.items()}, {"TS-1": "fail", "TS-2": "pass", "TS-3": "", "TS-4": ""})
        h = SC.history(self.db, rows["TS-1"]["id"])
        self.assertEqual((h[0]["source"], h[0]["author"], h[0]["run_job"], h[0]["comment"]), ("run", "mac-1", jid, "Expected <b>expired</b>"))
        self.assertEqual(SC.record_run(self.db, REPO, jid, "mac-1", jobs.tests(job)), 0)          # once per run
        self.assertEqual(len(SC.history(self.db, rows["TS-1"]["id"])), 1)

    def test_other_jobs_keep_no_tests_or_reports(self):
        jid = jobs.enqueue(self.db, REPO, 3, "c" * 40, "diff", "web-test", "any", 1000.0)
        jobs.claim(self.db, "w", "linux", ["web-test"], 1000.0, 120, 900, 2)
        self.assertEqual(jobs.complete(self.db, jid, "w", RESULT, 1100.0), "")
        self.assertEqual((jobs.tests(jobs.get(self.db, jid)), jobs.report_names(self.db, jid)), ([], []))


class BoardShows(UiCase):
    def setUp(self):
        super().setUp()
        p = self.root / "config.toml"
        p.write_text(p.read_text() + f'\n[workers]\nenabled = true\n[screens]\n[[screens.captures]]\nrepo = "{REPO}"\n')
        SC.create(self.db, REPO, {"title": "Pay", "pw_test": "e2e/shop.spec.ts › Checkout › pays with a card"})
        self.jid = screens_job(self.db)
        jobs.complete(self.db, self.jid, "mac-1", RESULT, 1100.0)

    def test_the_run_links_its_results_and_report(self):
        cookie, _ = self.session()
        s, _, html = self.req("GET", "/screens/captures", cookie=cookie)
        self.assertIn(f'/screens/results?job={self.jid}">results</a> (1 passed, 1 failed)', html)
        self.assertIn(f'/workerreport?job={self.jid}&amp;name=root"', html)
        s, _, html = self.req("GET", f"/screens/results?job={self.jid}", cookie=cookie)
        self.assertEqual(s, 200)
        self.assertIn("Expected &lt;b&gt;expired&lt;/b&gt;", html)
        self.assertNotIn("<b>expired", html)
        self.assertIn(">TS-1</a>", html)
        s, _, html = self.req("GET", "/scenarios/view?" + "repo=" + REPO + "&ref=TS-1", cookie=cookie)
        self.assertIn(f'href="/screens/results?job={self.jid}">run #{self.jid}</a>', html)

    def test_the_storage_shim_goes_first_in_the_head(self):
        from factory.ui.server import STORAGE_SHIM, sandboxed_report
        self.assertEqual(sandboxed_report(b"<!DOCTYPE html><html><HEAD><meta charset='UTF-8'>x"),
                         b"<!DOCTYPE html><html><HEAD>" + STORAGE_SHIM + b"<meta charset='UTF-8'>x")

    def test_the_report_is_served_sandboxed_and_only_when_signed_in(self):
        self.assertEqual(self.req("GET", f"/workerreport?job={self.jid}&name=root")[0], 303)
        cookie, _ = self.session()
        s, h, body = self.req("GET", f"/workerreport?job={self.jid}&name=root", cookie=cookie)
        self.assertEqual((s, h["Content-Type"]), (200, "text/html; charset=utf-8"))
        csp = h["Content-Security-Policy"]
        self.assertTrue(csp.startswith("sandbox allow-scripts;"))
        self.assertNotIn("allow-same-origin", csp)
        for d in ("default-src 'none'", "form-action 'none'", "frame-ancestors 'none'", "connect-src data: blob:"):
            self.assertIn(d, csp)
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertTrue(body.startswith("<script>(function(){function S()"))           # no <head>: the storage shim goes first
        self.assertIn("<script>document.title='pw'</script>", body)
        for bad in (f"/workerreport?job={self.jid}&name=../x", "/workerreport?job=x&name=root", f"/workerreport?job={self.jid}&name=nope",
                    "/screens/results?job=999", "/screens/results?job=1%3B"):
            self.assertEqual(self.req("GET", bad, cookie=cookie)[0], 404, bad)


if __name__ == "__main__":
    unittest.main()
