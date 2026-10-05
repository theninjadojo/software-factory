import base64
import sqlite3
import unittest

from factory import jobs
from test_render import png


OPEN = []


def db():
    d = sqlite3.connect(":memory:", isolation_level=None)
    OPEN.append(d)
    jobs.ensure_tables(d)
    return d


def tearDownModule():
    for d in OPEN:
        d.close()


def claim(d, worker="mac", platform="macos", recipes=("ios-test",), now=100.0):
    return jobs.claim(d, worker, platform, list(recipes), now, lease=120, claim_wait=900, max_attempts=2)


def art(name, data):
    return {"name": name, "png_b64": base64.b64encode(data).decode()}


class Queue(unittest.TestCase):
    def test_claim_matches_recipe_and_platform(self):
        d = db()
        jid = jobs.enqueue(d, "o/r", 1, "a" * 40, "diff", "ios-test", "macos", 100)
        self.assertIsNone(claim(d, recipes=("web-test",)))                   # wrong recipe
        self.assertIsNone(claim(d, platform="linux"))                       # wrong platform
        self.assertIsNone(claim(d, recipes=()))                             # advertises nothing
        job = claim(d)
        self.assertEqual((job["id"], job["patch"]), (jid, "diff"))
        self.assertIsNone(claim(d, worker="other"))                         # already claimed
        self.assertEqual(jobs.get(d, jid)["worker"], "mac")

    def test_any_platform_matches_every_worker(self):
        d = db()
        jobs.enqueue(d, "o/r", 1, "a" * 40, "diff", "web-test", "any", 100)
        self.assertIsNotNone(claim(d, platform="linux", recipes=("web-test",)))

    def test_oldest_first(self):
        d = db()
        a = jobs.enqueue(d, "o/r", 1, "a" * 40, "x", "ios-test", "macos", 100)
        jobs.enqueue(d, "o/r", 2, "a" * 40, "y", "ios-test", "macos", 101)
        self.assertEqual(claim(d)["id"], a)

    def test_lost_lease_requeues_then_fails(self):
        d = db()
        jid = jobs.enqueue(d, "o/r", 1, "a" * 40, "x", "ios-test", "macos", 100)
        claim(d, now=100)
        self.assertEqual(jobs.expire_stale(d, 300, 120, 900, 2), 1)          # silent for 200s: back in the queue
        self.assertEqual(jobs.get(d, jid)["status"], "queued")
        claim(d, now=300)                                                   # second attempt
        jobs.expire_stale(d, 600, 120, 900, 2)
        j = jobs.get(d, jid)
        self.assertEqual(j["status"], "error")
        self.assertIn("ran out of attempts", j["log"])

    def test_unclaimed_job_fails_after_claim_wait(self):
        d = db()
        jid = jobs.enqueue(d, "o/r", 1, "a" * 40, "x", "ios-test", "macos", 100)
        jobs.expire_stale(d, 1100, 120, 900, 2)
        self.assertEqual(jobs.get(d, jid)["status"], "error")

    def test_heartbeat_only_for_the_claiming_worker(self):
        d = db()
        jid = jobs.enqueue(d, "o/r", 1, "a" * 40, "x", "ios-test", "macos", 100)
        claim(d)
        self.assertEqual(jobs.heartbeat(d, jid, "intruder", 110), "gone")
        self.assertEqual(jobs.heartbeat(d, jid, "mac", 110), "ok")
        jobs.cancel(d, jid, 120)
        self.assertEqual(jobs.heartbeat(d, jid, "mac", 130), "cancel")

    def test_progress_is_stored_cleaned_and_only_from_the_owner(self):
        d = db()
        jid = jobs.enqueue(d, "o/r", 1, "a" * 40, "x", "ios-test", "macos", 100)
        claim(d)
        self.assertEqual(jobs.heartbeat(d, jid, "intruder", 110, "hijack"), "gone")
        self.assertEqual(jobs.get(d, jid)["progress"], "")
        self.assertEqual(jobs.heartbeat(d, jid, "mac", 110, "  building\n\x00 app\t" + "x" * 500), "ok")
        got = jobs.get(d, jid)
        self.assertTrue(got["progress"].startswith("building app xxx"))
        self.assertEqual((len(got["progress"]), got["progress_at"]), (jobs.MAX_PROGRESS, 110))
        self.assertEqual(jobs.heartbeat(d, jid, "mac", 112, "testing"), "ok")          # under 5s later: dropped, lease still extended
        self.assertEqual((jobs.get(d, jid)["progress"][:8], jobs.get(d, jid)["heartbeat"]), ("building", 112))
        jobs.heartbeat(d, jid, "mac", 116, "testing")
        self.assertEqual(jobs.get(d, jid)["progress"], "testing")
        self.assertEqual(jobs.heartbeat(d, jid, "mac", 130, ["not text"]), "ok")        # unusable progress never fails the heartbeat
        self.assertEqual(jobs.get(d, jid)["progress"], "testing")

    def test_cancelled_job_stores_no_progress(self):
        d = db()
        jid = jobs.enqueue(d, "o/r", 1, "a" * 40, "x", "ios-test", "macos", 100)
        claim(d)
        jobs.cancel(d, jid, 120)
        self.assertEqual(jobs.heartbeat(d, jid, "mac", 130, "late"), "cancel")
        self.assertEqual(jobs.get(d, jid)["progress"], "")

    def test_workers_are_recorded(self):
        d = db()
        claim(d)
        self.assertEqual([w["name"] for w in jobs.online_workers(d, 150, 60)], ["mac"])
        self.assertEqual(jobs.online_workers(d, 1000, 60), [])


class Results(unittest.TestCase):
    def setUp(self):
        self.d = db()
        self.jid = jobs.enqueue(self.d, "o/r", 1, "a" * 40, "x", "ios-test", "macos", 100)
        claim(self.d)

    def test_valid_result_is_stored(self):
        p = png(20, 20)
        self.assertEqual(jobs.complete(self.d, self.jid, "mac", {"status": "passed", "exit_code": 0, "log": "ok\x00\x1b[0m", "artifacts": [art("home", p)]}, 200), "")
        j = jobs.get(self.d, self.jid)
        self.assertEqual((j["status"], j["exit_code"], j["log"]), ("passed", 0, "ok[0m"))   # control characters stripped
        self.assertEqual(jobs.artifacts(self.d, self.jid), {"home": p})

    def test_other_worker_cannot_complete(self):
        self.assertTrue(jobs.complete(self.d, self.jid, "intruder", {"status": "passed"}, 200))
        self.assertEqual(jobs.get(self.d, self.jid)["status"], "claimed")

    def test_cannot_complete_twice(self):
        jobs.complete(self.d, self.jid, "mac", {"status": "failed"}, 200)
        self.assertTrue(jobs.complete(self.d, self.jid, "mac", {"status": "passed"}, 201))
        self.assertEqual(jobs.get(self.d, self.jid)["status"], "failed")

    def test_invalid_results_fail_the_job(self):
        bad = [{"status": "great"}, {"status": "passed", "exit_code": "0"}, {"status": "passed", "exit_code": True},
               {"status": "passed", "artifacts": "x"}, {"status": "passed", "artifacts": [art("Bad Name", png(20, 20))]},
               {"status": "passed", "artifacts": [art("a", b"not a png")]}, {"status": "passed", "artifacts": [{"name": "a", "png_b64": "!!!"}]},
               {"status": "passed", "artifacts": [art("a", png(20, 20)), art("a", png(20, 20))]},
               {"status": "passed", "artifacts": [art(f"a{i}", png(20, 20)) for i in range(9)]}, ["passed"]]
        for body in bad:
            with self.subTest(body=str(body)[:60]):
                d = db()
                jid = jobs.enqueue(d, "o/r", 1, "a" * 40, "x", "ios-test", "macos", 100)
                claim(d)
                self.assertTrue(jobs.complete(d, jid, "mac", body, 200))
                self.assertEqual(jobs.get(d, jid)["status"], "error")     # closed: never a pass
                self.assertEqual(jobs.artifacts(d, jid), {})

    def test_log_is_capped_to_the_end(self):
        jobs.complete(self.d, self.jid, "mac", {"status": "failed", "log": "x" * 500_000 + "THE END"}, 200)
        log = jobs.get(self.d, self.jid)["log"]
        self.assertEqual(len(log), jobs.MAX_LOG)
        self.assertTrue(log.endswith("THE END"))


if __name__ == "__main__":
    unittest.main()
