import sqlite3
import threading
import types
import unittest
from unittest import mock

from factory import jobs, main as m
from factory.pool import Pool, current_cancel, key


class PoolCancel(unittest.TestCase):
    def test_cancel_reaches_only_the_job_of_that_ticket(self):
        pool, started, seen = Pool(2, threaded=True), threading.Barrier(3), {}

        def job(k):
            ev = current_cancel()
            started.wait(5)
            seen[k] = ev.wait(5)

        a, b = key("o/r", 1), key("o/r", 2)
        pool.submit(a, "build", lambda: job(a))
        pool.submit(b, "build", lambda: job(b))
        started.wait(5)
        self.assertTrue(pool.cancel(a))
        self.assertFalse(pool.cancel(key("o/r", 3)))            # nothing running for it
        self.assertEqual([j["issue"] for j in pool.running() if "cancel" in j], [])      # the event is not exposed
        pool.cancel(b)
        pool.join()
        self.assertEqual(seen, {a: True, b: True})

    def test_no_event_outside_a_job(self):
        self.assertIsNone(current_cancel())


class StopClosed(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:", isolation_level=None)
        jobs.ensure_tables(self.db)
        self.cfg = types.SimpleNamespace(repos=["O/R"])
        self.pool, self.release, self.cancelled = Pool(1, threaded=True), threading.Event(), threading.Event()

    def run_job(self):
        started = threading.Event()

        def job():
            ev = current_cancel()
            started.set()
            if ev.wait(5):
                self.cancelled.set()
        self.pool.submit(key("O/R", 5), "build", job)
        started.wait(5)

    def test_a_closed_ticket_cancels_its_job_and_queued_checks(self):
        jid = jobs.enqueue(self.db, "O/R", 5, "a" * 40, "diff", "web-test", "any", 1)
        self.run_job()
        gh = mock.Mock()
        gh.get_issue.return_value = {"state": "closed"}
        with mock.patch.object(m, "pool", self.pool), mock.patch.object(m, "emit"):
            m.stop_closed(self.cfg, gh, self.db)
        self.pool.join()
        self.assertTrue(self.cancelled.is_set())
        self.assertEqual(jobs.get(self.db, jid)["status"], "cancelled")

    def test_an_open_ticket_is_left_running(self):
        jid = jobs.enqueue(self.db, "O/R", 5, "a" * 40, "diff", "web-test", "any", 1)
        self.run_job()
        gh = mock.Mock()
        gh.get_issue.return_value = {"state": "open"}
        with mock.patch.object(m, "pool", self.pool):
            m.stop_closed(self.cfg, gh, self.db)
            self.assertFalse(self.cancelled.is_set())
            self.pool.cancel(key("O/R", 5))                      # let the job end
        self.pool.join()
        self.assertEqual(jobs.get(self.db, jid)["status"], "queued")


if __name__ == "__main__":
    unittest.main()
