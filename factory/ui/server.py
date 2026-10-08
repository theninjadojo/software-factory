"""The admin UI: an authenticated, standard-library web server. See docs/ui-plan.md for the security model."""
import argparse
import getpass
import hmac
import json
import logging
import os
import shutil
import signal
import sqlite3
import sys
import threading
import time
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import backup, machines
from .. import db as dbm
from .. import designfiles
from .. import jobs, pause, screenboard, updates, version
from .. import questions as Q
from ..config import HealthCfg, load
from .. import tracker
from ..tracker import display, is_local
from . import admin, board, features, floor, floorplan, kanban, views
from . import chat as CH
from . import splitcard as SPC
from . import reviewactions as RA
from . import workers as WK
from . import labels as L
from . import localtickets as LT
from .auth import AuthStore, Sessions, Throttle
from .settings import Form
from .workerproc import WorkerApiProcess

log = logging.getLogger("factory.ui")
STATIC = {"style.css": "text/css; charset=utf-8", "app.js": "application/javascript; charset=utf-8",
          "floor-edit.js": "application/javascript; charset=utf-8", "terrain.js": "application/javascript; charset=utf-8", "town.js": "application/javascript; charset=utf-8", "town.css": "text/css; charset=utf-8", "fonts/space-grotesk-latin.woff2": "font/woff2", "fonts/jetbrains-mono-latin.woff2": "font/woff2"}
MAX_BODY = 64 * 1024
MAX_LAYOUT_BODY = 256 * 1024             # saving the floor layout, terrain and all (signed in, CSRF-checked like every form)
PAGE = 50
# A Playwright report is a page of scripts from a worker. It runs in an opaque origin (sandbox without allow-same-origin), so it cannot
# read this site's cookies or pages, and the session cookie (SameSite=Strict) is not sent with anything it requests. It may run its own
# inline scripts and read the data it embeds, but loads nothing from the network, submits no form and cannot be framed.
REPORT_HEADERS = {
    "Content-Security-Policy": "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline' 'unsafe-eval' blob: data:; "
                               "style-src 'unsafe-inline' data: blob:; img-src data: blob:; font-src data:; media-src data: blob:; "
                               "connect-src data: blob:; worker-src blob: data:; form-action 'none'; base-uri 'none'; frame-ancestors 'none'",
    "Cross-Origin-Opener-Policy": "same-origin", "Cross-Origin-Resource-Policy": "same-origin",
}
# The opaque origin has no localStorage (reading it throws), and Playwright's report stops on that. This script, ours and fixed, goes in
# before the report's own and gives it a storage that lives in memory for the page.
STORAGE_SHIM = (b"<script>(function(){function S(){var d={};return{getItem:function(k){return Object.prototype.hasOwnProperty.call(d,k)?d[k]:null},"
                b"setItem:function(k,v){d[k]=String(v)},removeItem:function(k){delete d[k]},clear:function(){d={}},key:function(i){return Object.keys(d)[i]||null},"
                b"get length(){return Object.keys(d).length}}}['localStorage','sessionStorage'].forEach(function(n){try{window[n].length}catch(e){"
                b"Object.defineProperty(window,n,{value:S(),configurable:true})}})})();</script>")


def sandboxed_report(html: bytes) -> bytes:
    """A worker's HTML report with STORAGE_SHIM first in its <head> (or first of all, when it has none)."""
    i = html[:4096].lower().find(b"<head>")
    return html[:i + 6] + STORAGE_SHIM + html[i + 6:] if i >= 0 else STORAGE_SHIM + html
HEADERS = {
    "Content-Security-Policy": "default-src 'none'; style-src 'self'; script-src 'self'; connect-src 'self'; img-src 'self' data:; font-src 'self'; "
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

    def refresh_update_notice(self) -> None:
        """Set the banner from the cached release check; a stale cache is refreshed in the background, never on the request."""
        try:
            c, have = self.cfg(), version.current()
            if not c.updates.check or have == "dev":
                views.UPDATE.update(tag="", url="")
                return
            state = self.state_dir()
            if updates.due(state):
                threading.Thread(target=updates.refresh, args=(state, c.updates.repo), kwargs={"token": self.update_token(c)}, daemon=True).start()
            got = updates.available(state, have) or {}
            views.UPDATE.update(tag=got.get("tag", ""), url=got.get("url", ""))
        except Exception:
            log.exception("update notice failed")

    @staticmethod
    def update_token(c) -> str:
        return updates.token_for(c)

    def state_dir(self) -> Path:
        return Path(self.cfg().db_path).parent

    def ro_db(self):
        """Dashboards read the orchestrator's database read-only; the UI cannot corrupt it."""
        path = Path(self.cfg().db_path)
        if not path.exists():
            return None
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)

    def add_approval(self, repo: str, issue: int, action: str) -> None:
        """Queue a person's decision for the orchestrator, exactly as the Telegram and Slack buttons do: it runs the ticket without asking the
        classifier again. The only write the UI makes to the database; the caller has validated repo, issue and action."""
        db = sqlite3.connect(self.cfg().db_path, timeout=10)
        try:
            db.execute("INSERT OR REPLACE INTO approvals VALUES (?,?,?,?)", (repo, int(issue), action, time.time()))
            db.commit()
        finally:
            db.close()

    def request_schedule_run(self, name: str) -> None:
        """Queue a "Run now" for the orchestrator (the UI has no GitHub token). The caller has checked `name` is a configured schedule."""
        from .. import schedules
        db = sqlite3.connect(self.cfg().db_path, timeout=10)
        try:
            schedules.ensure_tables(db)
            schedules.request_run(db, name, time.time())
        finally:
            db.close()

    def request_scan_run(self, name: str) -> None:
        """Queue a "Run now" for a scan for the orchestrator (the UI has no GitHub token). The caller has checked `name` is a configured scan."""
        from .. import scanner
        db = sqlite3.connect(self.cfg().db_path, timeout=10)
        try:
            scanner.ensure_tables(db)
            scanner.request_run(db, name, time.time())
        finally:
            db.close()

    def request_ticket_review(self, repo: str, sources: str) -> bool:
        """Queue a ticket review for the orchestrator (the UI has no GitHub token for this). The caller has checked repo and sources.
        False when one is already waiting or running for the repository."""
        from .. import ticketreview
        db = sqlite3.connect(self.cfg().db_path, timeout=10)
        try:
            return ticketreview.request(db, repo, sources, time.time()) is not None
        finally:
            db.close()

    def decide_proposals(self, repo: str, ids: list[int], accept: bool) -> int:
        """Queue (accept) or reject the picked ticket review proposals of a repository for the orchestrator. Returns how many changed."""
        from .. import ticketreview
        db = sqlite3.connect(self.cfg().db_path, timeout=10)
        try:
            ticketreview.ensure_tables(db)
            ok = [r[0] for r in db.execute(f"SELECT id FROM review_proposals WHERE repo=? AND id IN ({','.join('?' * len(ids))})", (repo, *ids))]
            return ticketreview.decide(db, ok, accept, time.time())
        finally:
            db.close()

    def decide_split(self, pid: int, repo: str, issue: int, accept: bool, start_action: str) -> bool:
        """Queue (accept) or reject a split proposal for the orchestrator, which creates the tickets at its next poll. The caller has
        checked repo, issue and that start_action comes from the start options; the proposal must be this ticket's and still waiting."""
        from .. import split
        from . import labels
        cfg = self.cfg()
        db = sqlite3.connect(cfg.db_path, timeout=10)
        try:
            split.ensure_tables(db)
            return split.decide(db, pid, repo, issue, accept, start_action, [o[2] for o in labels.start_options(cfg) if o[2]])
        finally:
            db.close()

    def overview(self) -> dict:
        cfg, db = self.cfg(), self.ro_db()
        summary = {"poll_seconds": cfg.poll_seconds, "live": not cfg.dry_run, "classifier": cfg.classifier_backend,
                   "telegram": f"{cfg.telegram_verbosity}" if cfg.telegram_chat_id else "not set up",
                   "slack": f"{cfg.slack_verbosity}" if cfg.slack_channel and cfg.slack_user_id else "not set up", "ci": "on" if cfg.ci.enabled else "off"}
        summary["max_parallel"] = cfg.runner.max_parallel
        summary["review"], summary["conflicts"] = cfg.review.enabled, cfg.conflicts.enabled
        summary["roles"], summary["workers"] = [r.name for r in cfg.roles], cfg.workers.enabled
        summary["power"], summary["repos"] = power_uses(cfg), list(cfg.repos)
        if cfg.local_enabled:                                        # the floor's Add a ticket offers attachments only when tickets can be local
            from . import localtickets as LT
            summary["attach_help"] = LT.attach_help(cfg)
            summary["attach_limits"] = (cfg.attach_max_files, cfg.attach_max_mb)
        summary["notify"] = {"telegram": bool(cfg.telegram_chat_id), "slack": bool(getattr(cfg, "slack_channel", None))}   # Slack: once configured
        d = {"cfg": summary, "paused": pause.paused(self.state_dir()) or "", "status": {}, "running": [], "queued": [], "runs": [], "events": [], "prs": [],
             "recent": [], "decided": {}, "conflicting": 0, "workers": [], "floor_layout": floorplan.read(self.state_dir())}
        if db is not None:
            try:
                d["status"] = dbm.get_status(db)
                d["runs"] = dbm.recent_runs(db, 10)
                d["events"] = dbm.recent_events(db, 15)
                d["prs"] = [p for p in dbm.watched_prs(db, 20) if p["status"] != "closed"]
                d["running"] = dbm.recent_runs(db, 20, status="running")      # every job in flight, newest first
                d["recent"] = dbm.recent_runs(db, 200)                        # for each station's numbers today
                d["decided"] = dict(db.execute("SELECT outcome, COUNT(*) FROM decisions WHERE decided_at > ? GROUP BY outcome", (time.time() - 86400,)))
                try:
                    d["schedules"] = schedule_trips(cfg, db, time.time())
                except sqlite3.Error:
                    pass                                                       # no schedule tables yet
                try:
                    d["server"] = server_floor(cfg)
                except OSError:
                    pass                                                       # no disk to read: the building shows no numbers
                try:
                    d["workers"] = workers_seen(db, time.time(), cfg.health)
                except sqlite3.Error:
                    pass                                                       # no worker tables yet
                try:
                    d["conflicting"] = sum(1 for p in dbm.tracked_prs(db) if p["state"] == "conflicting")
                except sqlite3.Error:
                    pass                                                       # an older database without the table
                try:
                    queued = json.loads(d["status"]["pool"]["value"])["queued"] if "pool" in d["status"] else []
                    d["queued"] = [q for q in queued if isinstance(q, dict) and isinstance(q.get("issue"), int)][:50]
                except (ValueError, KeyError, TypeError):
                    d["queued"] = []
            finally:
                db.close()
        return d


def power_uses(cfg) -> list[dict]:
    """Every enabled agent harness and the stations whose agents use it (for the floor's power stations)."""
    from ..config import default_harnesses
    hs = cfg.harnesses or default_harnesses(cfg.runner)
    uses = {n: [] for n, h in hs.items() if getattr(h, "enabled", True)}
    uses.setdefault("claude-code", [])
    for r in cfg.roles:
        if r.harness in uses:
            uses[r.harness].append(r.name)
    for route in cfg.routes.values():
        if route.harness in uses and "build" not in uses[route.harness]:
            uses[route.harness].append("build")
    if cfg.review.enabled and cfg.review.harness in uses:
        uses[cfg.review.harness].append("review")
    return [{"name": n, "uses": u} for n, u in uses.items()]


def _short(s: float) -> str:
    s = max(0, int(s))
    return f"{s // 86400}d" if s >= 86400 else f"{s // 3600}h" if s >= 3600 else f"{max(1, s // 60)}m"


def schedule_trips(cfg, db, now: float) -> list[dict]:
    """Each enabled schedule as a round trip: how far it is from its last run to its next."""
    from .. import schedules as sc
    out = []
    for sch in cfg.schedules:
        if not sch.enabled:
            continue
        st = sc.get_state(db, sch.name)
        nxt = sc.next_run(sch, st, now)
        if st is None or nxt is None:
            continue
        span = max(1.0, nxt - st["last_run"])
        due = now >= nxt
        every = f"every {sch.every}" if sch.every else "cron"
        out.append({"name": sch.name, "source": str((sch.source or {}).get("type") or "http"), "progress": min(1.0, (now - st["last_run"]) / span),
                    "due": due, "when": f"{every} · " + ("due now" if due else f"back in {_short(nxt - now)}")})
    return out


def workers_seen(db, now: float, hc=None) -> list[dict]:
    """Every verification worker the factory has seen, whether it is online, and the job it holds now (for the floor's trains)."""
    from .. import jobs
    from ..verify import ONLINE_SECONDS
    held = {w: {"issue": int(i), "recipe": r} for w, i, r in db.execute("SELECT worker, issue, recipe FROM verify_jobs WHERE status = 'claimed' ORDER BY claimed")}
    return [{"name": w["name"], "online": w["last_seen"] >= now - ONLINE_SECONDS, "job": held.get(w["name"]), **worker_report(w, hc)}
            for w in jobs.online_workers(db, 0, float("inf"))]


def worker_report(row: dict, hc) -> dict:
    """What a worker last said about its machine (its work disk, memory, load) and how that rates, for the floor and its page."""
    hc = hc or HealthCfg()
    try:
        stats = machines.clean_stats(json.loads(row.get("stats") or "{}"))
    except ValueError:
        stats = None
    return {"stats": stats, "level": machines.worker_level(stats, hc)}


def server_floor(cfg) -> dict:
    """What the floor shows on the server building: its rating and its fullest disk."""
    s = machines.server_stats(cfg)
    fullest = min(s["disks"], key=lambda d: d["free"] / max(d["total"], 1), default=None)
    return {"level": s["level"], "free": fullest["free"] if fullest else 0, "total": fullest["total"] if fullest else 0}


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

    def _send_file(self, path: Path, ctype: str, filename: str) -> None:
        """Stream a file as a download, in chunks. `filename` is generated by us (plain ASCII), never taken from a request."""
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        for k, v in HEADERS.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            with open(path, "rb") as f:
                shutil.copyfileobj(f, self.wfile, backup.CHUNK)

    def _spool(self, n: int, work: Path) -> Path | None:
        """Save the request body (n bytes) to a private file in work; None after answering if it was cut short."""
        body = work / "body"
        with open(body, "wb") as f:
            left = n
            while left > 0:
                chunk = self.rfile.read(min(backup.CHUNK, left))
                if not chunk:
                    self._send(400, "upload cut short", "text/plain")
                    return None
                f.write(chunk)
                left -= len(chunk)
        return body

    def _length(self, limit: int) -> int | None:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0:
            self._send(411, "length required", "text/plain")
        elif n > limit:
            self._send(413, "upload too large", "text/plain")
        else:
            return n
        return None

    def _restore_upload(self, csrf: str) -> None:
        """POST /backup/restore: a multipart body far larger than _form() allows. It is spooled to a private file (size-capped), the
        CSRF token is checked before anything is read from it, and the work files are always removed."""
        n = self._length(backup.MAX_RESTORE + MAX_BODY)
        if n is None:
            return
        work = backup.tmpdir(self.app.state_dir(), backup.TMP_RESTORE)
        try:
            body = self._spool(n, work)
            if body is None:
                return
            try:
                fields, span = backup.read_multipart(body, self.headers.get("Content-Type", ""))
            except backup.BackupError:
                return self._send(400, "bad upload", "text/plain")
            if not hmac.compare_digest(str(fields.get("csrf", "")), csrf):
                return self._send(403, "bad or missing CSRF token", "text/plain")
            admin.backup_restore(self, fields, csrf, body, span, work)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _ticket_upload(self, path: str, csrf: str) -> None:
        """A multipart ticket form (/tickets/create with files, /tickets/local/attach): spooled to a private size-capped file, the
        CSRF token checked before the handler sees anything, and the work files always removed. The handler gets the text fields
        and the files as (name, bytes), bytes being None for a file over the size limit."""
        max_bytes, max_files, max_total = tracker.attach_limits(self.app.cfg())
        n = self._length(max_total + 8 * MAX_BODY + 64 * 1024 * (max_files + 1))
        if n is None:
            return
        work = backup.tmpdir(self.app.state_dir(), backup.TMP_RESTORE)
        try:
            body = self._spool(n, work)
            if body is None:
                return
            try:
                fields, parts = backup.read_parts(body, self.headers.get("Content-Type", ""), 8 * MAX_BODY, max_files + 8, truncate=True)
            except backup.BackupError:
                return self._send(400, "bad upload", "text/plain")
            if not hmac.compare_digest(str(fields.get("csrf", "")), csrf):
                return self._send(403, "bad or missing CSRF token", "text/plain")
            files = [(fname, backup.read_span(body, span) if span[1] - span[0] <= max_bytes else None)
                     for name, fname, span in parts if name == "file" and (fname or span[1] > span[0])]
            form = Form(fields)
            form.lists = {k: [v] for k, v in fields.items()}
            admin.POST_UPLOAD[path](self, form, csrf, files)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _redirect(self, where: str, extra: dict | None = None) -> None:
        self._send(303, "", extra={"Location": where, **(extra or {})})

    def _session_id(self) -> str | None:
        c = SimpleCookie(self.headers.get("Cookie", ""))
        return c["sf_session"].value if "sf_session" in c else None

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").lower()
        host = host[1:host.index("]")] if host.startswith("[") and "]" in host else host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        return host in self.app.allowed_hosts            # defeats DNS-rebinding

    def _form(self, limit: int = MAX_BODY) -> dict | None:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if n > limit:
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
                if path == "/backup/restore":
                    return self._restore_upload(csrf)
                if path in admin.POST_UPLOAD and (path != "/tickets/create" or (self.headers.get("Content-Type") or "").lower().startswith("multipart/form-data")):
                    return self._ticket_upload(path, csrf)
                form = self._form(MAX_LAYOUT_BODY if path == "/floor/layout/save" else MAX_BODY)
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

    def _doc(self, db, repo: str, n: int, stage: str) -> str:
        """The stored document first; GitHub (the factory's own stage comment) when it is missing or was cut short."""
        doc, notice, source = dbm.stage_doc(db, repo, n, stage), "", "Stored output"
        text = Q.readable(doc["output"]) if doc else ""
        if not doc or doc["truncated"]:
            full = L.stage_comment(self, repo, n, stage)
            if full:
                text, source = Q.readable(full), "Complete, from GitHub"
            elif doc:
                notice = ('<p class="muted"><strong>Warning:</strong> this document was cut short when it was stored, and the full text could not be '
                          'fetched from GitHub. Open the ticket on GitHub for the rest.</p>')
        return views.doc_page(repo, n, stage, dbm.doc_stages(db, repo, n), doc, text, source, notice, "/needs",
                              dbm.mockup_previews(db, repo, n) if stage == "designer" else ())

    def _tickets(self, path: str, q: dict, csrf: str, badges) -> None:
        """Tickets: the filtered list beside one ticket (/ticket names it; on a phone that is its own screen)."""
        from urllib.parse import quote
        cfg, now = self.app.cfg(), time.time()
        db = self.app.ro_db()
        if db is None:
            return self._send(200, views.page("Tickets", '<p class="muted">The orchestrator has not created its database yet.</p>', path, csrf, badges=badges))
        # GitHub is slow: the page is drawn from the factory's database and the last GitHub read; when that read is too old, the
        # ticket's detail (its questions come from GitHub) loads in place, behind a loader. A fragment always reads GitHub.
        fragment = path.startswith("/fragment/")
        gh_on = cfg.github_issues_enabled
        needs = L.needs_you(self) if fragment or not gh_on else L.needs_cached()      # GitHub off: only local tickets are read, no GitHub call
        cold = gh_on and needs is None and not fragment and L.has_token(self)
        try:
            all_rows = board.ticket_rows(db, needs, L.titles_cached(), now, cfg.github_issues_enabled)
            projects = board.project_options(cfg)
            asked = q.get("project") or ""
            project = asked if asked in {v for v, _ in projects} else ""      # only a configured name (or Standalone) is ever used
            rows = board.in_project(all_rows, cfg, project)
            if path == "/tickets" and q.get("view") == "board":          # any other value of view is the list
                shown = L.flash_pop(csrf)
                return self._send(200, views.page("Board · Tickets", kanban.board_page(rows, cfg, csrf, project, projects, L.has_token(self), now, gh_on),
                                                  path, csrf, wide=True, badges=badges, bare=True,
                                                  flash=shown[0] if shown else None, flash_kind=shown[1] if shown else "ok"))
            c = board.counts(rows)
            keys ={k for k, _, _ in board.FILTERS}
            flt = q.get("stage") if q.get("stage") in keys else ("needs" if c["needs"] else "all")
            at = q.get("at") if q.get("at") in board.LABEL else ""
            text, order = (q.get("q") or "").strip()[:100], "oldest" if q.get("sort") == "oldest" else "latest"
            repo, n = q.get("repo", ""), q.get("n", "")
            explicit = path in ("/ticket", "/fragment/ticket", "/fragment/detail")
            if explicit and not (views.REPO.match(repo) and n.isdigit() and len(n) < 10):
                return self._send(404, "no such ticket", "text/plain")
            if explicit:
                sel = next((r for r in all_rows if r["repo"] == repo and r["issue"] == int(n)), None)
                if sel is None and not gh_on and not is_local(int(n)):
                    return self._send(404, "This ticket is hidden: it is a GitHub issue and Work from GitHub issues is off. Nothing was deleted.", "text/plain")
                sel = sel or board.row_for(db, repo, int(n), needs, L.titles_cached(), now)
            else:
                shown = board.pick(rows, flt, at, text, order)
                sel = shown[0] if shown else None
            detail = ""
            if sel is not None:
                files, docs, events, images = board.ticket_extras(db, sel["repo"], sel["issue"], sel["journey"])
                sel["design_imports"] = dbm.design_imports(db, sel["repo"], sel["issue"])
                sel["verify"] = board.ticket_verify(db, sel["repo"], sel["issue"])
                back = f"/ticket?repo={quote(sel['repo'], safe='')}&n={int(sel['issue'])}" + (f"&project={quote(project, safe='')}" if project else "")
                needs_html = (board.summary_card(db, sel["repo"], sel["issue"], docs, files) if sel["need"] else "") + board.needs_card(sel["need"], csrf, back)
                if path == "/fragment/ticket":
                    return self._send(200, board.live_part(sel, files, docs, events, cfg.ci.fix_rounds, now, csrf, needs_html))
                if cold:
                    detail = board.slot("/fragment/detail?" + board._qs(repo=sel["repo"], n=sel["issue"], project=project), "Loading the ticket from GitHub", "sd-detail sd-loading")
                else:
                    local = ""
                    if is_local(sel["issue"]) and (issue := LT.ticket(db, sel["repo"], sel["issue"])) is not None:
                        local = LT.card(cfg, issue, sel["repo"], csrf, back, L._decisions(self, sel["repo"]).get((sel["repo"], sel["issue"])),
                                        (sel["repo"], sel["issue"]) in L._approved(self))
                    local += SPC.card(cfg, db, sel["repo"], sel["issue"], csrf, back) + CH.card(cfg, db, sel["repo"], sel["issue"], csrf, back)
                    local += RA.card(db, sel["repo"], sel["issue"], csrf, back)
                    decided = L._decisions(self, sel["repo"]).get((sel["repo"], sel["issue"])) or {}
                    close = "" if is_local(sel["issue"]) else L.close_form(sel["repo"], {"number": sel["issue"], "title": sel["title"]}, csrf, back,
                                                                          sel["state"] in ("working", "needs"))
                    detail = board.detail_html(sel, needs_html, files, docs, events, cfg.ci.fix_rounds, now, explicit, images,
                                               local, features.ticket_handling(cfg, sel["repo"], sel["issue"], decided.get("detail", "")), close, csrf)
                if path == "/fragment/detail":
                    return self._send(200, detail)
        finally:
            db.close()
        new = (L.new_ticket_form(cfg, cfg.repos[0], csrf) + L.import_form(cfg, cfg.repos[0], csrf)) if cfg.repos else ""
        shown = L.flash_pop(csrf)
        title = f"{display(int(sel['issue']))} · Tickets" if explicit and sel else "Tickets"
        strip = features.tickets_strip(cfg, csrf, q.get("ask") == "local")
        return self._send(200, views.page(title, board.tickets_page(rows, sel, explicit, flt, text, at, order, detail, new, now, csrf, strip, project, projects, bool(asked and projects and not project), gh_on), path, csrf, wide=True,
                                          badges=badges, bare=True, flash=shown[0] if shown else None, flash_kind=shown[1] if shown else "ok"))

    def _get(self, path: str, q: dict, csrf: str) -> None:
        self.app.refresh_update_notice()
        cached = L.needs_cached()                            # the nav count: never a GitHub call, only what the tray already read
        badges = {"/tickets": len(cached)} if cached else None
        page = lambda title, body, **kw: self._send(200, views.page(title, body, path, csrf, badges=badges, **kw))
        flt = q.get("need", "") if q.get("need") in ("questions", "decisions") else ""
        if path in ("/", "/fragment/overview"):
            # the page itself never waits for GitHub: with no recent read, the Needs-you tray loads in place (the refresh reads GitHub)
            d = self.app.overview()
            d["settings_strip"] = features.factory_strip(self.app.cfg(), d["paused"], csrf)
            gh_on = self.app.cfg().github_issues_enabled
            needs = L.needs_you(self) if path == "/fragment/overview" or not gh_on else L.needs_cached()
            if gh_on and needs is None and path == "/" and L.has_token(self):
                d["needs_loading"] = True
            db = self.app.ro_db()
            try:
                d["ticket_rows"] = board.ticket_rows(db, needs, L.titles_cached(), None, self.app.cfg().github_issues_enabled) if db is not None else []
            finally:
                if db is not None:
                    db.close()
            body = views.overview_fragment(d, csrf, q.get("station"), needs, L.action_forms_for, flt,
                                           "list" if q.get("view") == "list" else "", q.get("mode") == "confirm")
            if path == "/":
                shown = L.flash_pop(csrf)                  # the result of the button that sent you back here (the refresh fragment never takes it)
                return self._send(200, views.page("Factory", f'<div id="live">{body}</div>', path, csrf, wide=True, full=True, badges=badges, bare=True,
                                                  flash=shown[0] if shown else admin.FLASH.get(q.get("ok", "")), flash_kind=shown[1] if shown else "ok"))
            return self._send(200, body)
        if path == "/fragment/needs-tray":
            return self._send(200, board.needs_tray(L.needs_you(self), csrf))
        if path in ("/needs", "/fragment/needs"):
            rows = L.needs_you(self)
            body = floor.tray(rows, csrf, None, "/needs" + (f"?need={flt}" if flt else ""), flt, heading=False)
            if path == "/needs":
                shown = L.flash_pop(csrf)
                return self._send(200, views.page("Needs you", f'<p class="ph-only"><a href="/">← Factory</a></p><div id="live" data-src="/fragment/needs">{body}</div>', path, csrf, wide=True,
                                                  badges={"/tickets": len(rows)} if rows else None,
                                                  flash=shown[0] if shown else None, flash_kind=shown[1] if shown else "ok"))
            return self._send(200, body)
        if path in ("/tickets", "/ticket", "/fragment/ticket", "/fragment/detail"):
            return self._tickets(path, q, csrf, badges)
        route = admin.GET.get(path)
        if route:
            return route(self, q, csrf)
        db = self.app.ro_db()
        if db is None:
            return page("No data yet", '<p class="muted">The orchestrator has not created its database yet.</p>')
        try:
            if path == "/runimg":
                rid, kind, name = q.get("run", ""), q.get("kind", ""), q.get("name", "")
                png = dbm.run_image(db, int(rid), kind, name) if rid.isdigit() and kind in dbm.IMAGE_KINDS and dbm.IMAGE_NAME.fullmatch(name) else None
                return self._send(200, png, "image/png") if png else self._send(404, "no such image", "text/plain")
            if path == "/workerimg":
                job, name = q.get("job", ""), q.get("name", "")
                png = WK.artifact_png(db, int(job), name) if job.isdigit() and len(job) < 10 else None
                return self._send(200, png, "image/png") if png else self._send(404, "no such image", "text/plain")
            if path == "/workerreport":                     # a Playwright HTML report a worker sent: untrusted HTML that needs its scripts
                job, name = q.get("job", ""), q.get("name", "")
                html = jobs.report(db, int(job), name) if job.isdigit() and len(job) < 10 else None
                return self._send(200, sandboxed_report(html), "text/html; charset=utf-8", REPORT_HEADERS) if html else self._send(404, "no such report", "text/plain")
            if path == "/screenimg":
                png = screenboard.image(db, q.get("key", ""))
                return self._send(200, png, "image/png") if png else self._send(404, "no such image", "text/plain")
            if path == "/mockup":
                repo, pth = q.get("repo", ""), q.get("path", "")
                if not designfiles.record_ok({"repo": repo, "path": pth, "url": "", "pr": ""}):
                    return self._send(404, "no such image", "text/plain")
                png = dbm.mockup_image(db, repo, pth)
                return self._send(200, png, "image/png") if png else self._send(404, "no such image", "text/plain")
            if path == "/design-source":                     # a canvas the designer wrote, as a download only (agent-written HTML is never shown here)
                repo, pth = q.get("repo", ""), q.get("path", "")
                html = dbm.design_source(db, repo, pth) if designfiles.record_ok({"repo": repo, "path": pth, "url": "", "pr": ""}) and pth.endswith(".dc.html") else None
                if not html:
                    return self._send(404, "no such design", "text/plain")
                return self._send(200, html, "application/octet-stream", {
                    "Content-Disposition": 'attachment; filename="' + pth.rpartition("/")[2] + '"', "X-Content-Type-Options": "nosniff",
                    "Content-Security-Policy": "sandbox; default-src 'none'"})
            if path in ("/design-import", "/design-import/png"):
                imp = dbm.design_import(db, int(q["id"])) if q.get("id", "").isdigit() and len(q["id"]) < 10 else None
                if not imp or (path.endswith("/png") and not imp["png"]):
                    return self._send(404, "no such design", "text/plain")
                if path.endswith("/png"):
                    return self._send(200, imp["png"], "image/png")
                # Third-party HTML: an opaque origin (sandbox without allow-same-origin or allow-scripts), no script, no network, no forms.
                return self._send(200, imp["html"], "text/html; charset=utf-8", {
                    "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; "
                                               "form-action 'none'; base-uri 'none'; frame-ancestors 'none'"})
            if path == "/runs":
                n = max(0, int(q.get("page", "0") or 0)) if q.get("page", "0").isdigit() else 0
                rows = dbm.recent_runs(db, PAGE + 1, n * PAGE, q.get("status") or None, q.get("repo") or None)
                today = time.time() // 86400 * 86400
                summary = dbm.runs_summary(db, today, q.get("repo") or None)
                return page("Runs", views.runs_page(rows[:PAGE], q.get("status", ""), q.get("repo", ""), n, len(rows) > PAGE, summary))
            if path.startswith("/runs/"):
                rid = path[len("/runs/"):]
                run = dbm.get_run(db, int(rid)) if rid.isdigit() else None
                return page(f"Run #{int(rid)}", views.run_detail(run, dbm.design_files_for_runs(db, [int(rid)]).get(int(rid), []), dbm.run_images(db, int(rid)))) if run else self._send(404, "no such run", "text/plain")
            if path == "/tickets":
                rows = dbm.tickets(db)
                return page("Tickets", views.tickets_page(rows, dbm.step_counts(db, rows)))
            if path == "/ticket/doc":
                n, stage = q.get("n", ""), q.get("stage", "")
                if not views.REPO.match(q.get("repo", "")) or not n.isdigit() or q["repo"] not in self.app.cfg().repos:
                    return self._send(404, "no such ticket", "text/plain")
                if stage not in views.DOC_LABEL:
                    return self._send(404, "Unknown stage.", "text/plain")
                return page(f"{views.DOC_LABEL[stage]} · {q['repo']} #{int(n)}", self._doc(db, q["repo"], int(n), stage))
            if path == "/ticket/images":
                n = q.get("n", "")
                if not views.REPO.match(q.get("repo", "")) or not n.isdigit():
                    return self._send(404, "no such ticket", "text/plain")
                steps = dbm.steps_for_ticket(db, q["repo"], int(n))
                all_runs = [i for s in steps for i in s["run_ids"]]
                shots = {i: im for i in all_runs if (im := dbm.run_images(db, i))}
                return page(f"Images · #{int(n)}", views.ticket_images_page(q["repo"], int(n), dbm.design_files_for_runs(db, all_runs), shots))
            if path == "/prs":                                   # now a filter of the Tickets page
                return self._redirect("/tickets?stage=prs")
            if path == "/events":
                before = int(q["before"]) if q.get("before", "").isdigit() else None
                kind = q.get("kind") or "important"
                rows = dbm.recent_events(db, 101, None if kind == "all" else kind, before)
                return page("Events", views.events_page(rows[:100], kind, rows[99]["id"] if len(rows) > 100 else None))
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
    ap = argparse.ArgumentParser(description="Shikumi admin UI")
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
    backup.sweep(app.state_dir())
    srv = serve(app, host or "127.0.0.1", int(port))
    log.info("UI listening on %s (allowed hosts: %s)", args.listen, sorted(app.allowed_hosts))
    # The worker API runs as our child while [workers] is enabled; FACTORY_WORKERS_LISTEN overrides where it binds (Docker: 0.0.0.0:8788).
    workerapi = WorkerApiProcess(args.config, os.environ.get("FACTORY_WORKERS_LISTEN") or None)
    threading.Thread(target=workerapi.run, args=(app.cfg,), daemon=True).start()
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))       # so the finally below runs on `systemctl stop` / `docker stop`
    try:
        srv.serve_forever()
    finally:
        workerapi.stop()


if __name__ == "__main__":
    main()
