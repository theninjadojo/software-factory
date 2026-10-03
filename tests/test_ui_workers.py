import base64
import unittest
from dataclasses import replace

from factory import db as dbm
from factory import jobs, verify
from factory.config import WorkersCfg
from test_render import png
from test_ui import UiCase

WORKERS_TOML = '''
[workers]
enabled = true
[[workers.checks]]
repo = "your-org/shop-mobile"
recipe = "ios-test"
platform = "macos"
'''


class WorkersPage(UiCase):
    def enable(self):
        p = self.root / "config.toml"
        p.write_text(p.read_text() + WORKERS_TOML)

    def job(self, status="passed", log="ok", arts=None, worker="mac"):
        jid = jobs.enqueue(self.db, "your-org/shop-mobile", 7, "a" * 40, "diff", "ios-test", "macos")
        jobs.claim(self.db, worker, "macos", ["ios-test"], 1000.0, 120, 900, 2)
        jobs.complete(self.db, jid, worker, {"status": status, "exit_code": 0, "log": log,
                                             "artifacts": [{"name": n, "png_b64": base64.b64encode(p).decode()} for n, p in (arts or {}).items()]}, 1060.0)
        return jid

    def test_requires_login(self):
        for path in ("/workers", "/workers/job?id=1", "/workerimg?job=1&name=a"):
            self.assertEqual(self.req("GET", path)[0], 303, path)

    def test_off_by_default_explains_how_to_enable(self):
        cookie, _ = self.session()
        s, _, html = self.req("GET", "/workers", cookie=cookie)
        self.assertEqual(s, 200)
        self.assertIn("Verification workers are off", html)
        self.assertIn('href="/workers"', html)                       # listed in the settings side menu

    def test_shows_checks_workers_and_jobs(self):
        self.enable()
        jid = self.job(status="failed", log="<script>alert(1)</script> boom")
        cookie, _ = self.session()
        s, _, html = self.req("GET", "/workers", cookie=cookie)
        self.assertEqual(s, 200)
        for text in ("ios-test", "macos", "your-org/shop-mobile", "mac", f"/workers/job?id={jid}", "failed", "offline"):
            self.assertIn(text, html)
        self.assertNotIn("<script>alert", html)

    def test_job_detail_escapes_the_worker_log_and_serves_screenshots(self):
        self.enable()
        jid = self.job(status="passed", log="<img src=x onerror=alert(1)>", arts={"home": png(20, 20)})
        cookie, _ = self.session()
        s, _, html = self.req("GET", f"/workers/job?id={jid}", cookie=cookie)
        self.assertEqual(s, 200)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html)
        self.assertNotIn("<img src=x", html)
        self.assertIn(f"/workerimg?job={jid}&amp;name=home", html)
        s, h, _ = self.req("GET", f"/workerimg?job={jid}&name=home", cookie=cookie)
        self.assertEqual((s, h["Content-Type"]), (200, "image/png"))

    def test_bad_ids_and_names_are_404(self):
        self.enable()
        cookie, _ = self.session()
        self.assertEqual(self.req("GET", "/workers/job?id=999", cookie=cookie)[0], 404)
        self.assertEqual(self.req("GET", "/workers/job?id=1%3Bdrop", cookie=cookie)[0], 404)
        self.assertEqual(self.req("GET", "/workerimg?job=1&name=../../etc", cookie=cookie)[0], 404)
        self.assertEqual(self.req("GET", "/workerimg?job=x&name=a", cookie=cookie)[0], 404)

    def test_verify_screenshots_show_on_the_run_page(self):
        rid = dbm.start_run(self.db, "build", "o/r", 1, "t", "claude-code", "m", "low", "", 1.0)
        dbm.add_run_images(self.db, rid, [{"kind": "verify", "name": "ios-test-home", "png": png(20, 20)}])
        cookie, _ = self.session()
        s, _, html = self.req("GET", f"/runs/{rid}", cookie=cookie)
        self.assertIn("verify: ios-test-home", html)
        self.assertEqual(self.req("GET", f"/runimg?run={rid}&kind=verify&name=ios-test-home", cookie=cookie)[0], 200)


class Watch(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from test_workerapi import cfg_for
        self.cfg = cfg_for(Path(tempfile.mkdtemp()))
        self.db = dbm.connect(self.cfg.db_path)
        self.addCleanup(self.db.close)
        self.sent = []

    def watch(self, now, cfg=None):
        verify.watch(cfg or self.cfg, self.db, lambda text, event="worker_offline": self.sent.append((event, text)), now)

    def test_alerts_once_when_jobs_wait_with_no_worker_then_once_when_it_clears(self):
        jobs.enqueue(self.db, "o/r", 1, "a" * 40, "d", "ios-test", "macos", 1000)
        self.watch(1050)                                           # waited under the grace period
        self.assertEqual(self.sent, [])
        self.watch(1200)
        self.watch(1260)                                           # still stuck: no repeat
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][0], "worker_offline")
        jobs.claim(self.db, "mac", "macos", ["ios-test"], 1300, 120, 900, 2)
        self.watch(1310)
        self.assertEqual(len(self.sent), 2)
        self.assertIn("back online", self.sent[1][1])

    def test_quiet_when_a_worker_is_online_or_nothing_waits_or_workers_are_off(self):
        self.watch(5000)
        jobs.enqueue(self.db, "o/r", 1, "a" * 40, "d", "ios-test", "macos", 1000)
        jobs.claim(self.db, "mac", "other-recipe-only", [], 5000, 120, 900, 2)     # online, but cannot take the job
        self.watch(5050)
        off = replace(self.cfg, workers=WorkersCfg())
        self.watch(9000, off)
        self.assertEqual(self.sent, [])

    def test_event_is_known_to_telegram_settings(self):
        from factory.events import ALL_EVENTS, event_enabled
        self.assertIn("worker_offline", ALL_EVENTS)
        self.assertTrue(event_enabled("normal", "worker_offline"))
        self.assertFalse(event_enabled("quiet", "worker_offline"))


if __name__ == "__main__":
    unittest.main()
