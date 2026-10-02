import http.client
import json
from urllib.parse import quote
import re
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from factory import db as dbm
from factory.ui import auth, views
from factory.ui.server import App, serve

PASSWORD = "correct horse battery"


class UiCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        self.db_path = str(d / "state" / "factory.db")
        (d / "state").mkdir()
        # EVERY path in the config must point into the temp dir: UI tests write secrets, and /srv/factory may be a live install.
        cfg = Path("config.example.toml").read_text().replace("/srv/factory", str(d))
        assert "/srv/factory" not in cfg
        (d / "config.toml").write_text(cfg)
        self.state = d / "state"
        self.root = d
        self.db = dbm.connect(self.db_path)
        self.app = App(str(d / "config.toml"), {"localhost", "127.0.0.1"})
        self.app.auth.set_password(PASSWORD)
        self.srv = serve(self.app, "127.0.0.1", 0)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        p = mock.patch("factory.ui.server.time.sleep")
        p.start()
        self.addCleanup(p.stop)

    def req(self, method, path, body=None, cookie=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": f"127.0.0.1:{self.port}", **(headers or {})}
        if cookie:
            h["Cookie"] = cookie
        if body is not None:
            h["Content-Type"] = "application/x-www-form-urlencoded"
        c.request(method, path, body=body, headers=h)
        r = c.getresponse()
        data = r.read().decode("utf-8", "replace")
        out = (r.status, dict(r.getheaders()), data)
        c.close()
        return out

    def login(self, password=PASSWORD):
        s, h, _ = self.req("POST", "/login", f"password={password}")
        return s, (h.get("Set-Cookie") or "").split(";")[0]

    def session(self):
        s, cookie = self.login()
        self.assertEqual(s, 303)
        _, _, html = self.req("GET", "/", cookie=cookie)
        return cookie, re.search(r'name="csrf" value="([0-9a-f]+)"', html).group(1)


class Auth(UiCase):
    def test_everything_requires_a_session(self):
        for path in ("/", "/runs", "/tickets", "/prs", "/events", "/runs/1", "/ticket?repo=o/r&n=4"):
            s, h, _ = self.req("GET", path)
            self.assertEqual((s, h["Location"]), (303, "/login"), path)
        self.assertEqual(self.req("GET", "/fragment/overview")[0], 401)          # a status code, not a login page to inject
        self.assertEqual(self.req("GET", "/healthz")[0], 200)
        self.assertEqual(self.req("GET", "/login")[0], 200)

    def test_login_logout_and_cookie_flags(self):
        s, h, _ = self.req("POST", "/login", "password=nope")
        self.assertEqual(s, 401)
        s, h, _ = self.req("POST", "/login", f"password={PASSWORD}")
        cookie = h["Set-Cookie"]
        self.assertEqual(s, 303)
        for flag in ("HttpOnly", "SameSite=Strict", "Path=/"):
            self.assertIn(flag, cookie)
        c = cookie.split(";")[0]
        self.assertEqual(self.req("GET", "/", cookie=c)[0], 200)
        _, csrf = self.session()
        c2, csrf2 = self.session()
        self.req("POST", "/logout", f"csrf={csrf2}", cookie=c2)
        self.assertEqual(self.req("GET", "/", cookie=c2)[0], 303)                # session is gone server-side

    def test_login_is_throttled(self):
        for _ in range(5):
            self.assertEqual(self.req("POST", "/login", "password=bad")[0], 401)
        self.assertEqual(self.req("POST", "/login", f"password={PASSWORD}")[0], 429)   # even the right password, for a while

    def test_password_rules_and_hashing(self):
        with self.assertRaises(ValueError):
            self.app.auth.set_password("short")
        rec = auth.hash_password("abcdefghijk")
        self.assertNotIn("abcdefghijk", json.dumps(rec))
        self.assertFalse(auth.verify_password("abcdefghijl", rec))
        self.assertEqual(oct((Path(self.tmp.name) / "state" / "ui-auth.json").stat().st_mode & 0o777), "0o600")

    def test_unconfigured_ui_cannot_be_logged_into(self):
        (Path(self.tmp.name) / "state" / "ui-auth.json").unlink()
        self.assertEqual(self.req("POST", "/login", "password=anything")[0], 401)
        self.assertIn("--set-password", self.req("GET", "/login")[2])


class RequestSafety(UiCase):
    def test_host_header_allowlist_blocks_dns_rebinding(self):
        self.assertEqual(self.req("GET", "/healthz", headers={"Host": "evil.example"})[0], 421)
        self.assertEqual(self.req("GET", "/healthz", headers={"Host": "evil.example:8787"})[0], 421)

    def test_security_headers_on_every_response(self):
        for path in ("/login", "/healthz", "/static/style.css"):
            _, h, _ = self.req("GET", path)
            self.assertIn("default-src 'none'", h["Content-Security-Policy"])
            self.assertEqual((h["X-Frame-Options"], h["X-Content-Type-Options"], h["Referrer-Policy"]), ("DENY", "nosniff", "no-referrer"))

    def test_static_files_are_whitelisted(self):
        self.assertEqual(self.req("GET", "/static/app.js")[0], 200)
        for bad in ("/static/../server.py", "/static/..%2fserver.py", "/static/auth.py", "/static/", "/static/%2e%2e/%2e%2e/etc/passwd"):
            self.assertEqual(self.req("GET", bad)[0], 404, bad)

    def test_csrf_is_required_for_every_post(self):
        cookie, csrf = self.session()
        for path in ("/action/pause", "/action/resume", "/logout"):
            self.assertEqual(self.req("POST", path, "", cookie=cookie)[0], 403, path)
            self.assertEqual(self.req("POST", path, "csrf=wrong", cookie=cookie)[0], 403, path)
        self.assertFalse((self.state / "PAUSED").exists())
        self.assertEqual(self.req("POST", "/action/pause", f"csrf={csrf}", cookie=cookie)[0], 303)
        self.assertTrue((self.state / "PAUSED").exists())
        self.req("POST", "/action/resume", f"csrf={csrf}", cookie=cookie)
        self.assertFalse((self.state / "PAUSED").exists())

    def test_csrf_token_is_per_session(self):
        a, csrf_a = self.session()
        b, _ = self.session()
        self.assertEqual(self.req("POST", "/action/pause", f"csrf={csrf_a}", cookie=b)[0], 403)

    def test_oversized_bodies_are_rejected(self):
        cookie, csrf = self.session()
        self.assertEqual(self.req("POST", "/action/pause", "csrf=" + "x" * 70000, cookie=cookie)[0], 413)

    def test_dashboard_database_is_read_only(self):
        import sqlite3
        db = self.app.ro_db()
        with self.assertRaises(sqlite3.OperationalError):
            db.execute("DELETE FROM runs")
        db.close()


class Pages(UiCase):
    def test_overview_runs_events_and_detail_render_recorded_data(self):
        rid = dbm.start_run(self.db, "stage", "o/web", 7, "Add the thing", "claude-code", "sonnet", "medium",
                            json.dumps({"kind": "feature", "stage": "design"}), "designer")
        dbm.finish_run(self.db, rid, "stage", "designer document ready", "", "# The design\nbody", "agent log tail")
        dbm.add_event(self.db, "alert:pr_ready", "PR ready", "o/web", 7, rid)
        dbm.record(self.db, "o/web", 7, "t1", "run:stage", "why")
        dbm.watch_pr(self.db, "o/web", 9, "o/web", 7)
        dbm.set_status(self.db, "last_poll_ok", str(__import__("time").time()))
        cookie, _ = self.session()
        _, _, home = self.req("GET", "/", cookie=cookie)
        self.assertIn("orchestrator healthy", home)
        self.assertIn("Add the thing", home)
        self.assertIn("PR ready", home)
        _, _, runs = self.req("GET", "/runs?status=stage", cookie=cookie)
        self.assertIn("sonnet", runs)
        _, _, detail = self.req("GET", f"/runs/{rid}", cookie=cookie)
        for needle in ("# The design", "agent log tail", "designer", "&quot;stage&quot;: &quot;design&quot;"):
            self.assertIn(needle, detail)
        self.assertIn("watching", self.req("GET", "/prs", cookie=cookie)[2])
        self.assertIn("run:stage", self.req("GET", "/tickets", cookie=cookie)[2])
        self.assertIn("alert:pr_ready", self.req("GET", "/events?kind=alert", cookie=cookie)[2])
        self.assertEqual(self.req("GET", "/runs/999", cookie=cookie)[0], 404)
        self.assertEqual(self.req("GET", "/runs/abc", cookie=cookie)[0], 404)

    def test_health_goes_red_when_the_orchestrator_stops_reporting(self):
        dbm.set_status(self.db, "last_poll_ok", "1")
        cookie, _ = self.session()
        self.assertIn("orchestrator not reporting", self.req("GET", "/fragment/overview", cookie=cookie)[2])

    def test_hostile_text_is_escaped_everywhere(self):
        evil = '<script>alert(1)</script><img src=x onerror=alert(2)>'
        rid = dbm.start_run(self.db, "build", "o/web", 1, evil, evil, evil, evil, evil)
        dbm.finish_run(self.db, rid, "failed", evil, "javascript:alert(3) https://evil.example/pull/1", evil, evil)
        dbm.add_event(self.db, "error", evil, "o/web", 1, rid)
        dbm.record(self.db, "o/web", 1, "t", evil, evil)
        dbm.watch_pr(self.db, "o/web", 2, "o/web", 1)
        dbm.update_pr(self.db, "o/web", 2, summary=evil)
        cookie, _ = self.session()
        for path in ("/", "/fragment/overview", "/runs", f"/runs/{rid}", "/tickets", "/prs", "/events", f"/runs?repo={quote(evil)}", f"/events?kind={quote(evil)}"):
            _, _, html = self.req("GET", path, cookie=cookie)
            self.assertNotIn("<script>alert", html, path)
            self.assertNotIn("<img src=x", html, path)
            self.assertNotIn('href="javascript:', html, path)

    def test_only_github_urls_become_links(self):
        self.assertIn("<a ", views.gh_link("https://github.com/o/r/pull/5"))
        for bad in ("javascript:alert(1)", "https://evil.example/o/r/pull/5", "https://github.com/o/r/pull/5/../../x", "https://github.com.evil.com/o/r/pull/5"):
            self.assertNotIn("<a ", views.gh_link(bad), bad)
        self.assertEqual(views.pr_links("javascript:x https://github.com/o/r/pull/5").count("<a "), 1)
        self.assertNotIn("<a ", views.ticket_link("evil/../x", 1))

    def test_pages_work_before_the_orchestrator_has_created_its_database(self):
        Path(self.db_path).unlink()
        for ext in ("-wal", "-shm"):
            Path(self.db_path + ext).unlink(missing_ok=True)
        cookie, _ = self.session()
        self.assertIn("not created its database", self.req("GET", "/runs", cookie=cookie)[2])
        self.assertEqual(self.req("GET", "/", cookie=cookie)[0], 200)


if __name__ == "__main__":
    unittest.main()
