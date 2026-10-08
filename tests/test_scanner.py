import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory import db as dbm
from factory import jobs, scanner
from factory.config import parse
from test_schedules import BASE

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("smell_scan", ROOT / "worker/recipes/smell-scan.py")
recipe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recipe)


def cfg_with(scanner_raw=None, workers=True):
    raw = dict(BASE)
    raw["workers"] = {"enabled": workers}
    raw["scanner"] = {"enabled": True, **(scanner_raw or {}),
                      "scans": [{"name": "weekly", "repo": "o/site", "every": "1w", "smells": ["todo-debt", "long-files"]}]}
    return parse(raw)


class FakeGh:
    def __init__(self, open_issues=()):
        self.created, self.open = [], set(open_issues)

    def default_branch(self, repo):
        return "main"

    def branch_sha(self, repo, branch):
        return "a" * 40

    def create_scheduled_issue(self, repo, title, body, labels):
        self.created.append((repo, title, body, labels))
        return {"number": 100 + len(self.created)}

    def get_issue(self, repo, n):
        return {"state": "open" if n in self.open else "closed"}


class ConfigTests(unittest.TestCase):
    def test_valid_and_defaults(self):
        c = cfg_with()
        self.assertEqual(c.scanner.max_tickets, 5)
        self.assertEqual(c.scanner.scans[0].smells, ("todo-debt", "long-files"))

    def test_rejections(self):
        bad = [{"scans": [{"name": "x", "repo": "o/other", "every": "1w", "smells": ["todo-debt"]}]},
               {"scans": [{"name": "x", "repo": "o/site", "every": "1w", "cron": "0 6 * * 1", "smells": ["todo-debt"]}]},
               {"scans": [{"name": "x", "repo": "o/site", "every": "5m", "smells": ["todo-debt"]}]},
               {"scans": [{"name": "x", "repo": "o/site", "every": "1w", "smells": ["nope"]}]},
               {"smells": [{"id": "todo-debt", "name": "n", "pattern": "x"}]},
               {"smells": [{"id": "a", "name": "n", "pattern": "("}]},
               {"smells": [{"id": "a", "name": "n", "pattern": "x*"}]},
               {"smells": [{"id": "a", "name": "n", "pattern": "x", "globs": ["../x"]}]},
               {"labels": [""]}]
        for extra in bad:
            raw = dict(BASE)
            raw["workers"] = {"enabled": True}
            raw["scanner"] = {"enabled": True, **extra}
            with self.assertRaises(ValueError, msg=str(extra)):
                parse(raw)

    def test_needs_workers(self):
        with self.assertRaises(ValueError):
            cfg_with(workers=False)


class FindingTests(unittest.TestCase):
    def test_validate(self):
        ok = [{"smell": "todo-debt", "path": "a/b.py", "line": 3, "snippet": "# TODO\x00 x\ny"}]
        out, why = jobs.validate_findings(ok)
        self.assertEqual(why, "")
        self.assertEqual(out[0]["snippet"], "# TODO x y")
        for bad in ([{"smell": "X", "path": "a", "line": 1, "snippet": ""}],
                    [{"smell": "a", "path": "../a", "line": 1, "snippet": ""}],
                    [{"smell": "a", "path": "/a", "line": 1, "snippet": ""}],
                    [{"smell": "a", "path": "a", "line": True, "snippet": ""}],
                    [{"smell": "a", "path": "a", "line": 1, "snippet": "", "extra": 1}],
                    [{"smell": "a", "path": "a", "line": 1, "snippet": ""}] * 1001, "x"):
            self.assertIsNone(jobs.validate_findings(bad)[0])

    def test_fingerprint_ignores_whitespace(self):
        self.assertEqual(scanner.fingerprint("s", "p", "a  b"), scanner.fingerprint("s", "p", " a b "))
        self.assertNotEqual(scanner.fingerprint("s", "p", "a"), scanner.fingerprint("s", "q", "a"))


class RegexSafetyTests(unittest.TestCase):
    def test_refused_and_accepted_patterns(self):
        for p in (r"(a+)+$", r"(a|ab)*c", r"(x*)*", r"(\w+\s?)+", r"(a)\1", r"(?P<n>a)(?P=n)", r"a(?=b)", r"(?<!a)b", r"(?(1)a|b)",
                  r"(?s)a", r"a{1001}", r"a{2,5000}"):
            self.assertTrue(scanner.regex_problem(p), p)
        for p in (r"\b(TODO|FIXME|XXX|HACK)\b", r"^\s*print\(", r"https?://(localhost|10[.])", r"(?i)todo", r"[\]()+*]+x", r"a{2,3}",
                  r"\\1", r"(?:foo)+", r"(foo)+", r"[^]]+", "TODO|FIXME", r"x{,5}"):
            self.assertEqual(scanner.regex_problem(p), "", p)

    def test_advice_limits(self):
        self.assertEqual(scanner.advice_problem("Move it to config.\nThen test."), "")
        for bad in ("x" * 2001, "a\x00b", 5):
            self.assertTrue(scanner.advice_problem(bad))

    def test_config_advice(self):
        c = cfg_with({"smells": [{"id": "a", "name": "n", "pattern": "x", "advice": "Fix it."}], "advice": {"todo-debt": "Open an issue."}})
        self.assertEqual((scanner.advice_for(c.scanner, "a"), scanner.advice_for(c.scanner, "todo-debt"), scanner.advice_for(c.scanner, "long-files")),
                         ("Fix it.", "Open an issue.", ""))
        for extra in ({"advice": {"a": "x"}}, {"advice": {"todo-debt": "x" * 2001}},
                      {"smells": [{"id": "a", "name": "n", "pattern": "x", "advice": "x" * 2001}]}):
            raw = dict(BASE)
            raw["workers"] = {"enabled": True}
            raw["scanner"] = {"enabled": True, **extra}
            with self.assertRaises(ValueError, msg=str(extra)):
                parse(raw)

    def test_hand_written_slow_pattern_still_loads(self):
        with self.assertLogs("factory.config", "WARNING"):
            c = cfg_with({"smells": [{"id": "slow", "name": "n", "pattern": "(a+)+$"}]})
        self.assertEqual(c.scanner.smells[0].id, "slow")


class TicketTests(unittest.TestCase):
    def test_untrusted_text_is_fenced_and_neutralised(self):
        c = cfg_with()
        f = [{"smell": "todo-debt", "path": "a.py", "line": 1, "snippet": "# TODO @octocat ``` ![x](http://e.com/i.png)"}]
        title, body = scanner.render_ticket(c.scanner.scans[0], "TODO", "desc", f, 1, "2026-01-01", 60000)
        self.assertIn("untrusted", body)
        self.assertEqual(body.count("```"), 2)                # the snippet cannot close the fence early
        self.assertIn("'''", body)
        self.assertLess(body.index("untrusted"), body.index("@octocat"))      # the warning comes first; the text is inside the fence
        _, big = scanner.render_ticket(c.scanner.scans[0], "TODO", "desc @octocat", f * 5000, 5000, "d", 2000)
        self.assertLessEqual(len(big), 2100)
        self.assertIn("desc @\u200bocto", big)           # admin prose outside the fence is still neutralised

    def test_advice_sits_outside_the_fence(self):
        c = cfg_with()
        f = [{"smell": "todo-debt", "path": "a.py", "line": 1, "snippet": "# TODO"}]
        _, plain = scanner.render_ticket(c.scanner.scans[0], "TODO", "desc", f, 1, "d", 60000)
        self.assertNotIn("Recommended fix", plain)
        _, body = scanner.render_ticket(c.scanner.scans[0], "TODO", "desc", f, 1, "d", 60000, "Use the tracker, ask @octocat. ```")
        self.assertEqual(body.count("```"), 2)
        self.assertGreater(body.index("## Recommended fix (from the smell definition)"), body.rindex("```"))
        self.assertIn("ask @\u200bocto", body)



class RunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = dbm.connect(os.path.join(self.tmp.name, "f.db"))
        self.cfg = cfg_with()
        self.scan = self.cfg.scanner.scans[0]
        self.events = []

    def tearDown(self):
        self.tmp.cleanup()

    def tick(self, gh, now, **kw):
        scanner.tick(self.cfg, gh, self.db, now, Path(self.tmp.name), lambda *a: self.events.append(a),
                     lambda text, event="": self.events.append(text), **kw)

    def finish(self, findings, status="passed"):
        job = jobs.claim(self.db, "w", "any", ["smell-scan"], 10, 120, 900, 2)
        self.assertEqual(json.loads(job["params"])["smells"][0]["id"], "todo-debt")
        self.assertEqual(jobs.complete(self.db, job["id"], "w", {"status": status, "log": "", "findings": findings}, 11), "")

    def test_full_run_dedupes_and_labels(self):
        gh = FakeGh()
        self.tick(gh, 1000)                                   # first sight: clock starts, nothing queued
        self.assertIsNone(jobs.get(self.db, 1))
        self.tick(gh, 1000 + 604800)
        self.assertEqual(jobs.get(self.db, 1)["base_sha"], "a" * 40)
        self.tick(gh, 1000 + 604800 + 5)                      # still queued: nothing happens
        found = [{"smell": "todo-debt", "path": f"f{i}.py", "line": i, "snippet": "# TODO"} for i in range(10)]
        self.finish(found)
        self.tick(gh, 1000 + 604800 + 10)
        self.assertEqual(len(gh.created), 1)                  # ten hits, one ticket
        self.assertEqual(gh.created[0][3], ["factory:analyze"])
        self.assertEqual(scanner.history(self.db, "weekly")[0]["new_findings"], 10)
        # the next run sees the same findings: nothing new is filed
        self.tick(gh, 1000 + 2 * 604800 + 20)
        self.finish(found)
        self.tick(gh, 1000 + 2 * 604800 + 30)
        self.assertEqual(len(gh.created), 1)

    def test_cap_and_open_ticket(self):
        self.cfg = parse({**BASE, "workers": {"enabled": True}, "scanner": {
            "enabled": True, "max_tickets": 1, "labels": ["x"],
            "scans": [{"name": "weekly", "repo": "o/site", "every": "1w", "smells": ["todo-debt", "long-files"]}]}})
        self.scan = self.cfg.scanner.scans[0]
        gh = FakeGh()
        found = [{"smell": "todo-debt", "path": "a.py", "line": 1, "snippet": "TODO"},
                 {"smell": "long-files", "path": "b.py", "line": 900, "snippet": "more"}]
        seen, new, opened, deferred = scanner.file_tickets(self.cfg, gh, self.db, self.scan, found, 5.0, "d")
        self.assertEqual((new, len(opened), deferred), (2, 1, 1))
        self.assertEqual(gh.created[0][3], ["x"])
        gh2 = FakeGh(open_issues={101})                       # the todo-debt ticket is still open: its new finding waits
        more = found[:1] + [{"smell": "todo-debt", "path": "c.py", "line": 1, "snippet": "TODO"}]
        _, new, opened, deferred = scanner.file_tickets(self.cfg, gh2, self.db, self.scan, more, 6.0, "d")
        self.assertEqual((len(opened), deferred), (0, 1))

    def test_no_run_in_dry_run_or_pause(self):
        gh = FakeGh()
        scanner.set_state(self.db, "weekly", 0, "ok")
        paused = Path(self.tmp.name) / "PAUSED"
        paused.write_text("")
        self.tick(gh, 10 ** 7)
        self.assertIsNone(jobs.get(self.db, 1))
        paused.unlink()
        self.cfg = parse({**BASE, "general": {**BASE["general"], "dry_run": True}, "workers": {"enabled": True},
                          "scanner": {"enabled": True, "scans": [{"name": "weekly", "repo": "o/site", "every": "1w", "smells": ["todo-debt"]}]}})
        self.tick(gh, 10 ** 7)
        self.assertIsNone(jobs.get(self.db, 1))

    def test_failed_job_retries_then_alerts(self):
        gh = FakeGh()
        scanner.set_state(self.db, "weekly", 0, "ok")
        self.tick(gh, 10 ** 7)
        jobs.cancel(self.db, 1, 10 ** 7)
        self.tick(gh, 10 ** 7 + 1)
        st = scanner.get_state(self.db, "weekly")
        self.assertEqual((st["status"], st["fails"]), ("error", 1))
        self.assertTrue(st["retry_at"])
        self.assertTrue(any("failed" in str(e) for e in self.events))

    def test_run_now(self):
        gh = FakeGh()
        scanner.set_state(self.db, "weekly", 10 ** 7, "ok")
        scanner.request_run(self.db, "weekly", 1.0)
        self.tick(gh, 10 ** 7 + 1)
        self.assertIsNotNone(jobs.get(self.db, 1))


class RecipeTests(unittest.TestCase):
    def test_scan(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "pkg").mkdir()
            (root / "pkg/a.py").write_text("x = 1  # TODO later\n" + "y = 2\n" * 10)
            (root / "pkg/b.py").write_text("print('x')\n")
            (root / "tests").mkdir()
            (root / "tests/test_b.py").write_text("")
            (root / "vendor").mkdir()
            (root / "vendor/c.py").write_text("# TODO\n")
            (root / "bin.dat").write_bytes(b"\0TODO")
            os.symlink("/etc/passwd", root / "link.py")
            params = {"exclude": ["vendor/**"], "smells": [
                {"id": "todo-debt", "type": "regex", "pattern": r"\bTODO\b", "globs": ["**/*"]},
                {"id": "long-files", "type": "long-file", "max_lines": 5},
                {"id": "missing-tests", "type": "missing-tests"},
                {"id": "no-print", "type": "regex", "pattern": r"^\s*print\(", "globs": ["**/*.py"]}]}
            got = {(f["smell"], f["path"]) for f in recipe.scan(d, params)}
            self.assertEqual(got, {("todo-debt", "pkg/a.py"), ("long-files", "pkg/a.py"), ("missing-tests", "pkg/a.py"),
                                   ("no-print", "pkg/b.py")})

    def test_a_slow_pattern_is_cut_off_and_then_dropped(self):
        if not hasattr(recipe.signal, "setitimer"):
            self.skipTest("no setitimer here")
        with tempfile.TemporaryDirectory() as d:
            for n in range(5):
                (Path(d) / f"f{n}.txt").write_text("a" * 40 + "b\n")
            (Path(d) / "g.txt").write_text("ok TODO\n")
            params = {"smells": [{"id": "slow", "type": "regex", "pattern": r"(a+)+$", "globs": ["**/f*.txt"]},
                                 {"id": "todo", "type": "regex", "pattern": "TODO", "globs": ["**/*"]}]}
            with mock.patch.object(recipe, "FILE_SECONDS", 0.2), mock.patch("sys.stderr", new_callable=io.StringIO) as err:
                got = recipe.scan(d, params)
            self.assertEqual([(f["smell"], f["path"]) for f in got], [("todo", "g.txt")])
            self.assertEqual(err.getvalue().count("took more than"), 3)           # the fourth and fifth file are not tried
            self.assertIn("smell slow dropped: too slow", err.getvalue())

    def test_test_title_not_user_story(self):
        sid = "test-title-not-user-story"
        self.assertEqual(scanner.regex_problem(scanner.PRESETS[sid][2]["pattern"]), "")
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "e2e").mkdir()
            (Path(d) / "e2e/a.spec.ts").write_text(
                'test("coach marks a child absent", async () => {});\n'           # flagged
                "  test.skip('billing page redirects', async () => {});\n"        # flagged
                'it(`renders`, () => {});\n'                                       # flagged
                'test("As a coach, I want to mark a child absent", async () => {});\n'
                'test("As an admin, I want to see jobs", async () => {});\n'
                'test.describe("on a phone", () => {});\n'
                'await test.step("open the page", async () => {});\n'
                "test(\n")
            (Path(d) / "e2e/helpers.ts").write_text('test("not a test file", () => {});\n')
            (Path(d) / "src").mkdir()
            (Path(d) / "src/sum.test.ts").write_text('test("adds two numbers", () => {});\n')       # a unit test: not checked
            rule = dict(scanner.PRESETS[sid][2], id=sid)
            got = [(f["path"], f["line"]) for f in recipe.scan(d, {"smells": [rule]})]
        self.assertEqual(got, [("e2e/a.spec.ts", 1), ("e2e/a.spec.ts", 2), ("e2e/a.spec.ts", 3)])



if __name__ == "__main__":
    unittest.main()
