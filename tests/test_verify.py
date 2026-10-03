import base64
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from factory import db as dbm
from factory import jobs, verify
from factory.config import WorkerCheck
from test_render import png
from test_workerapi import cfg_for


class FakeWorld:
    """A clock that only moves when the gate sleeps, and a 'worker' that acts on each sleep."""
    def __init__(self, db, act=None):
        self.db, self.now, self.act, self.sleeps = db, 1000.0, act, 0

    def clock(self):
        return self.now

    def sleep(self, s):
        self.now += s
        self.sleeps += 1
        if self.act:
            self.act(self)

    def work(self, worker, status, log="", arts=None, recipes=("ios-test",)):
        job = jobs.claim(self.db, worker, "macos", list(recipes), self.now, 120, 900, 2)
        if job:
            body = {"status": status, "exit_code": 0 if status == "passed" else 1, "log": log,
                    "artifacts": [{"name": n, "png_b64": base64.b64encode(p).decode()} for n, p in (arts or {}).items()]}
            jobs.complete(self.db, job["id"], worker, body, self.now)
        return job


class Gate(unittest.TestCase):
    def setUp(self):
        self.cfg = cfg_for(Path(tempfile.mkdtemp()))
        self.db = dbm.connect(self.cfg.db_path)
        self.addCleanup(self.db.close)

    def run_gate(self, world, repo="o/r"):
        return verify.gate(self.cfg, repo, 7, "a" * 40, "diff", db=self.db, sleep=world.sleep, clock=world.clock)

    def test_no_checks_passes_without_a_worker(self):
        self.assertTrue(verify.gate(self.cfg, "o/other", 1, "a" * 40, "d", db=self.db).ok)
        off = replace(self.cfg, workers=replace(self.cfg.workers, enabled=False))
        self.assertEqual(verify.checks_for(off, "o/r"), [])

    def test_pass_with_screenshots(self):
        w = FakeWorld(self.db, lambda w: w.work("mac", "passed", arts={"home": png(20, 20)}))
        rep = self.run_gate(w)
        self.assertTrue(rep.ok, rep.text())
        self.assertEqual([(i["kind"], i["name"]) for i in rep.images], [("verify", "ios-test-home")])
        self.assertIn("passed on mac", rep.text())

    def test_failure_carries_the_log(self):
        w = FakeWorld(self.db, lambda w: w.work("mac", "failed", log="Test Suite 'App' failed\n```evil```"))
        rep = self.run_gate(w)
        self.assertFalse(rep.ok)
        self.assertIn("failed on mac", rep.text())
        self.assertIn("Test Suite 'App' failed", rep.text())
        self.assertNotIn("```evil", rep.text())                       # the log cannot close our code fence

    def test_error_is_not_a_pass(self):
        w = FakeWorld(self.db, lambda w: w.work("mac", "error", log="git clone failed"))
        rep = self.run_gate(w)
        self.assertFalse(rep.ok)
        self.assertIn("could not run", rep.text())

    def test_no_worker_fails_closed_after_claim_wait(self):
        w = FakeWorld(self.db)
        rep = self.run_gate(w)
        self.assertFalse(rep.ok)
        self.assertIn("No worker claimed this job", rep.text())
        self.assertLess(w.now, 1000 + self.cfg.workers.claim_wait_seconds + 10)

    def test_worker_that_vanishes_fails_after_attempts(self):
        def vanish(w):                                              # claims, then never reports
            jobs.claim(w.db, "mac", "macos", ["ios-test"], w.now, 120, 900, 2)
        rep = self.run_gate(FakeWorld(self.db, vanish))
        self.assertFalse(rep.ok)
        self.assertIn("ran out of attempts", rep.text())

    def test_overall_wait_is_bounded(self):
        cfg = replace(self.cfg, workers=replace(self.cfg.workers, max_wait_seconds=30, claim_wait_seconds=86400))
        w = FakeWorld(self.db, lambda w: w.work("mac", "passed", recipes=()) and None)      # a worker that never matches
        rep = verify.gate(cfg, "o/r", 7, "a" * 40, "d", db=self.db, sleep=w.sleep, clock=w.clock)
        self.assertFalse(rep.ok)
        self.assertIn("did not finish within 30s", rep.text())
        self.assertEqual(jobs.recent(self.db)[0]["status"], "cancelled")

    def test_all_checks_must_pass(self):
        cfg = replace(self.cfg, workers=replace(self.cfg.workers, checks=(WorkerCheck("o/r", "ios-test", "macos"), WorkerCheck("o/r", "web-test", "any"))))

        def act(w):
            w.work("mac", "passed", recipes=("ios-test",))
            w.work("mac", "failed", log="npm test failed", recipes=("web-test",))
        w = FakeWorld(self.db, act)
        rep = verify.gate(cfg, "o/r", 7, "a" * 40, "d", db=self.db, sleep=w.sleep, clock=w.clock)
        self.assertFalse(rep.ok)
        self.assertIn("check 'ios-test' passed", rep.text())
        self.assertIn("check 'web-test' failed", rep.text())


class ConfigChecks(unittest.TestCase):
    def test_validation(self):
        from factory.config import _workers
        _workers({"checks": [{"repo": "o/r", "recipe": "ios-test", "platform": "macos"}]}, ["o/r"])
        for raw, msg in (({"mode": "maybe"}, "mode"), ({"listen": "nowhere"}, "listen"), ({"lease_seconds": 0}, "lease_seconds"),
                         ({"checks": [{"repo": "x/y", "recipe": "a"}]}, "not a configured repo"),
                         ({"checks": [{"repo": "o/r", "recipe": "A B"}]}, "recipe"),
                         ({"checks": [{"repo": "o/r", "recipe": "a"}, {"repo": "o/r", "recipe": "a"}]}, "twice")):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, msg):
                _workers(raw, ["o/r"])


if __name__ == "__main__":
    unittest.main()
