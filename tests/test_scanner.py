import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
