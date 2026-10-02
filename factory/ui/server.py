"""The admin UI: an authenticated, standard-library web server. See docs/ui-plan.md for the security model."""
import argparse
import getpass
import hmac
import logging
import os
import sqlite3
import sys
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import db as dbm
from .. import pause
from ..config import load
from . import admin, views
from .auth import AuthStore, Sessions, Throttle
from .settings import Form

log = logging.getLogger("factory.ui")
STATIC = {"style.css": "text/css; charset=utf-8", "app.js": "application/javascript; charset=utf-8"}
MAX_BODY = 64 * 1024
PAGE = 50
HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; "
                               "form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store",
}


class App:
    """State shared by all requests."""

    def __init__(self, config_path: str, allowed_hosts: set[str], secure_cookie: bool = False):
        self.config_path = config_path
        self.allowed_hosts = {h.lower() for h in allowed_hosts}
        self.secure_cookie = secure_cookie
        self.sessions, self.throttle = Sessions(), Throttle()
        self.auth = AuthStore(self.state_dir() / "ui-auth.json")

    def cfg(self):
        return load(self.config_path)

    def state_dir(self) -> Path:
        return Path(self.cfg().db_path).parent

    def ro_db(self):
        """Dashboards read the orchestrator's database read-only; the UI cannot corrupt it."""
        path = Path(self.cfg().db_path)
        if not path.exists():
            return None
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)

    def overview(self) -> dict:
        cfg, db = self.cfg(), self.ro_db()
        summary = {"poll_seconds": cfg.poll_seconds, "live": not cfg.dry_run, "classifier": cfg.classifier_backend,
                   "telegram": f"{cfg.telegram_verbosity}" if cfg.telegram_chat_id else "not set up", "ci": "on" if cfg.ci.enabled else "off"}
        d = {"cfg": summary, "paused": pause.paused(self.state_dir()) or "", "status": {}, "running": None, "runs": [], "events": [], "prs": []}
        if db is not None:
            try:
                d["status"] = dbm.get_status(db)
                d["runs"] = dbm.recent_runs(db, 10)
                d["events"] = dbm.recent_events(db, 15)
                d["prs"] = [p for p in dbm.watched_prs(db, 20) if p["status"] == "watching"]
                running = dbm.recent_runs(db, 1, status="running")
                d["running"] = running[0] if running else None
            finally:
                db.close()
        return d


class Handler(BaseHTTPRequestHandler):
    app: App
    server_version = "factory-ui"
    sys_version = ""
    timeout = 20

    def log_message(self, fmt, *args):
        log.info("%s %s", self.address_string(), fmt % args)

    # ---- plumbing ----
    def _send(self, status: int, body: str | bytes, ctype: str = "text/html; charset=utf-8", extra: dict | None = None) -> None:
        data = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in {**HEADERS, **(extra or {})}.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _redirect(self, where: str, extra: dict | None = None) -> None:
        self._send(303, "", extra={"Location": where, **(extra or {})})

    def _session_id(self) -> str | None:
        c = SimpleCookie(self.headers.get("Cookie", ""))
        return c["sf_session"].value if "sf_session" in c else None

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        host = host[1:host.index("]")] if host.startswith("[") and "]" in host else host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        return host in self.app.allowed_hosts            # defeats DNS-rebinding

    def _form(self) -> dict | None:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if n > MAX_BODY:
            return None
        parsed = parse_qs(self.rfile.read(n).decode("utf-8", "replace"), keep_blank_values=True)
        form = Form({k: v[0] for k, v in parsed.items()})
        form.lists = parsed
        return form

    def do_GET(self):
        self._handle()

    def do_HEAD(self):
        self._handle()

    def do_POST(self):
        self._handle()

    # ---- routing ----
    def _handle(self) -> None:
        try:
            if not self._host_ok():
                return self._send(421, "misdirected request", "text/plain")
            url = urlparse(self.path)
            path, q = url.path, {k: v[0] for k, v in parse_qs(url.query).items()}
            if path.startswith("/static/"):
                name = path[len("/static/"):]
                f = Path(__file__).parent / "static" / name
                if name in STATIC and f.is_file():
                    return self._send(200, f.read_bytes(), STATIC[name], {"Cache-Control": "no-cache"})
                return self._send(404, "not found", "text/plain")
            if path == "/healthz":
                return self._send(200, "ok", "text/plain")
            if path == "/login":
                return self._login(q)
            sid = self._session_id()
            sess = self.app.sessions.get(sid)
            if sess is None:
                if path.startswith("/fragment/"):
                    return self._send(401, "sign in", "text/plain")
                return self._redirect("/login")
            csrf = sess["csrf"]
            if self.command == "POST":
                form = self._form()
                if form is None:
                    return self._send(413, "request too large", "text/plain")
                if not hmac.compare_digest(str(form.get("csrf", "")), csrf):
                    return self._send(403, "bad or missing CSRF token", "text/plain")
                return self._post(path, form, csrf, sid)
            return self._get(path, q, csrf)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            log.exception("request failed")
            try:
                self._send(500, "internal error", "text/plain")
            except Exception:
                pass

    def _login(self, q: dict) -> None:
        ip = self.client_address[0]
        if self.command != "POST":
            return self._send(200, views.login_page(setup_hint=not self.app.auth.configured()))
        if self.app.throttle.blocked(ip):
            return self._send(429, views.login_page("Too many attempts. Wait a few minutes."))
        form = self._form()
        if form is None:
            return self._send(413, "request too large", "text/plain")
        if self.app.auth.configured() and self.app.auth.check(form.get("password", "")):
            self.app.throttle.reset(ip)
            sid, _ = self.app.sessions.create()
            cookie = f"sf_session={sid}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200" + ("; Secure" if self.app.secure_cookie else "")
            return self._redirect("/", {"Set-Cookie": cookie})
        self.app.throttle.fail(ip)
        time.sleep(0.4)                                   # slow down guessing
        self._send(401, views.login_page("Wrong password."))

    def _get(self, path: str, q: dict, csrf: str) -> None:
        page = lambda title, body, **kw: self._send(200, views.page(title, body, path, csrf, **kw))
        if path == "/fragment/overview":
            return self._send(200, views.overview_fragment(self.app.overview(), csrf))
        if path == "/":
            return page("Overview", f'<div id="live">{views.overview_fragment(self.app.overview(), csrf)}</div>')
        route = admin.GET.get(path)
        if route:
            return route(self, q, csrf)
        db = self.app.ro_db()
        if db is None:
            return page("No data yet", '<p class="muted">The orchestrator has not created its database yet.</p>')
        try:
            if path == "/runs":
                n = max(0, int(q.get("page", "0") or 0)) if q.get("page", "0").isdigit() else 0
                rows = dbm.recent_runs(db, PAGE + 1, n * PAGE, q.get("status") or None, q.get("repo") or None)
                return page("Runs", views.runs_page(rows[:PAGE], q.get("status", ""), q.get("repo", ""), n, len(rows) > PAGE))
            if path.startswith("/runs/"):
                rid = path[len("/runs/"):]
                run = dbm.get_run(db, int(rid)) if rid.isdigit() else None
                return page(f"Run #{int(rid)}", views.run_detail(run)) if run else self._send(404, "no such run", "text/plain")
            if path == "/tickets":
                rows = dbm.tickets(db)
                return page("Tickets", views.tickets_page(rows, dbm.step_counts(db, rows)))
            if path == "/ticket":
                n = q.get("n", "")
                if not views.REPO.match(q.get("repo", "")) or not n.isdigit():
                    return self._send(404, "no such ticket", "text/plain")
                return page(f"Ticket #{int(n)}", views.ticket_detail(q["repo"], int(n), dbm.steps_for_ticket(db, q["repo"], int(n))))
            if path == "/prs":
                return page("PRs & CI", views.prs_table(dbm.watched_prs(db)))
            if path == "/events":
                before = int(q["before"]) if q.get("before", "").isdigit() else None
                rows = dbm.recent_events(db, 101, q.get("kind") or None, before)
                return page("Events", views.events_page(rows[:100], q.get("kind", ""), rows[99]["id"] if len(rows) > 100 else None))
        finally:
            db.close()
        self._send(404, "not found", "text/plain")

    def _post(self, path: str, form: dict, csrf: str, sid: str | None) -> None:
        state = self.app.state_dir()
        if path == "/logout":
            self.app.sessions.destroy(sid)
            return self._redirect("/login", {"Set-Cookie": "sf_session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"})
        if path == "/action/pause":
            (state / "PAUSED").write_text("")
            return self._redirect("/")
        if path == "/action/resume":
            (state / "PAUSED").unlink(missing_ok=True)
            (state / "pause_until").unlink(missing_ok=True)
            return self._redirect("/")
        route = admin.POST.get(path)
        if route:
            return route(self, form, csrf)
        self._send(404, "not found", "text/plain")


def serve(app: App, host: str, port: int) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"app": app})
    srv = ThreadingHTTPServer((host, port), handler)
    srv.daemon_threads = True
    return srv


def main() -> None:
    ap = argparse.ArgumentParser(description="software-factory admin UI")
    ap.add_argument("--config", default="/srv/factory/config.toml")
    ap.add_argument("--listen", default="127.0.0.1:8787", help="host:port (default loopback only)")
    ap.add_argument("--allowed-host", action="append", default=[], help="extra Host header value to accept (repeatable)")
    ap.add_argument("--secure-cookie", action="store_true", help="mark the session cookie Secure (use behind HTTPS)")
    ap.add_argument("--set-password", action="store_true", help="set the UI password (prompts, or reads FACTORY_UI_PASSWORD) and exit")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    host, _, port = args.listen.rpartition(":")
    extra = [h.strip() for h in os.environ.get("FACTORY_UI_ALLOWED_HOSTS", "").split(",") if h.strip()]
    app = App(args.config, {"localhost", "127.0.0.1", "::1", host, *args.allowed_host, *extra}, args.secure_cookie)
    if args.set_password:
        pw = os.environ.get("FACTORY_UI_PASSWORD") or getpass.getpass("New UI password (min 10 chars): ")
        app.auth.set_password(pw)
        print("password set")
        return
    if not app.auth.configured():
        sys.exit("No UI password is set. Run: python3 -m factory.ui --config <config> --set-password")
    srv = serve(app, host or "127.0.0.1", int(port))
    log.info("UI listening on %s (allowed hosts: %s)", args.listen, sorted(app.allowed_hosts))
    srv.serve_forever()


if __name__ == "__main__":
    main()
