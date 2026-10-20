"""The JSON API under /api/v1/, for the command-line client (cli/). It runs in the UI process and does what the pages do with the
same functions (labels.start_ticket and the others), so a check or a message is never written twice.

Authentication is a bearer token (apitokens.py), never the session cookie: a request that carries only a cookie is refused, so a
web page cannot use a signed-in browser to call the API, and the CSRF field the forms need has no part here. A write also needs a
JSON body (application/json), which a page on another site cannot send without the browser asking first. The Host check of every
request still applies (server.py). Settings are not part of the API.

handle() returns (status, JSON object); server.py sends it. Errors: {"error": message safe to show, "code": a stable word,
"fields": {name: message}} (fields only for a value that cannot be used)."""
import logging
import re
import time

from .. import db as dbm
from .. import pause, plan
from .. import questions as Q
from .. import tracker, version
from . import apijson as J
from . import board, localtickets as LT, plancard as PC
from . import labels as L

log = logging.getLogger("factory.ui")

API = 1
PREFIX = "/api/v1/"
TICKET = re.compile(r"tickets/([\w.-]{1,100})/([\w.-]{1,100})/(\d{1,9})(?:/([a-z-]{1,20})(?:/([a-z]{1,20}))?)?")
MERGE = re.compile(r"prs/([\w.-]{1,100})/([\w.-]{1,100})/(\d{1,9})/merge")
RUN = re.compile(r"runs/(\d{1,9})")
GETS = {"version", "meta", "status", "tickets", "needs", "runs", "events"}
POSTS = {"pause", "resume", "tickets", "answer-all"}
TICKET_POSTS = {"start", "send-back", "answer", "close", "reopen", "comment", "edit", "priority"}
RUN_STATUSES = ("running", "passed", "pr", "stage", "failed", "rejected", "no-change", "rate-limited", "interrupted", "cancelled")
PAGE = 50
NO_DB = "The orchestrator has not created its database yet."


class Error(Exception):
    def __init__(self, status: int, code: str, message: str, fields: dict | None = None):
        super().__init__(message)
        self.status, self.code, self.fields = status, code, fields or {}


def _err(e: Exception) -> tuple[int, dict]:
    if isinstance(e, Error):
        out = {"error": str(e), "code": e.code}
        if e.fields:
            out["fields"] = e.fields
        return e.status, out
    if isinstance(e, L.BadTicket):                       # not a configured repository or not a number: there is no such ticket
        return 404, {"error": str(e), "code": "not_found"}
    if isinstance(e, L.Refused):
        return e.status, {"error": str(e), "code": e.code}
    return 409, {"error": str(e), "code": "refused"}    # questions.Refused: the answers do not match the ticket's questions


def _db(h):
    db = h.app.ro_db()
    if db is None:
        raise Error(503, "no_database", NO_DB)
    return db


def _text(data: dict, key: str, default: str = "", limit: int = 20000) -> str:
    v = data.get(key, default)
    if v is None:
        return default
    if not isinstance(v, str) or len(v) > limit:
        raise Error(422, "invalid", f"{key} must be text.", {key: "Must be text."})
    return v


def _choice(q: dict, key: str, allowed) -> str:
    v = q.get(key, "")
    if v and v not in allowed:
        raise Error(422, "invalid", f"{key} must be one of: {', '.join(allowed)}.", {key: "Not one of the choices."})
    return v


def _ok(msg: str, **extra) -> tuple[int, dict]:
    return 200, {"ok": True, "message": msg, **extra}


# ------------------------------------------------------------------ reads
def get_version(h, q, token) -> dict:
    return {"api": API, "factory": version.current(), "token": {"name": token["name"], "scope": token["scope"]}}


def get_meta(h, q, token) -> dict:
    """What the client needs to offer choices: repositories, projects, stages, start options, filters, priorities."""
    cfg = h.app.cfg()
    return {"repos": list(cfg.repos), "projects": [{"value": v, "label": lab} for v, lab in board.project_options(cfg)],
            "roles": [r.name for r in cfg.roles], "start_options": [{"value": v, "text": t} for v, t, _ in L.start_options(cfg)],
            "filters": [{"value": k, "label": lab} for k, lab, _ in board.FILTERS], "stations": [{"value": k, "label": lab} for k, lab in board.STATIONS],
            "priorities": [*plan.PRIORITIES, "pm"], "local_tickets": cfg.local_enabled, "github_issues": cfg.github_issues_enabled,
            "github_token": L.has_token(h), "review": cfg.review.enabled, "conflicts": cfg.conflicts.enabled}


def get_status(h, q, token) -> dict:
    """The Floor in numbers: mode, pause, health, what runs and waits, the tiles and the Claude plan's use."""
    import json
    from . import floor
    cfg, now = h.app.cfg(), time.time()
    d = h.app.overview()
    needs = L.needs_cached()
    if needs is None:               # no recent read: ask GitHub (kept for a few seconds) rather than count the database's own record of
        needs = L.needs_you(h)      # who is waiting, which can be out of date (answered or closed on GitHub). None: it could not be read
    rows = []
    db = h.app.ro_db()
    if db is not None:
        try:
            rows = board.ticket_rows(db, needs, L.titles_cached(), now, cfg.github_issues_enabled)
        finally:
            db.close()
    t = board.tile_numbers(d, rows, needs, now)
    try:
        usage = {k: v for k, v in json.loads(d["status"]["claude_usage"]["value"]).items() if k != "token"}   # not even its fingerprint
    except (KeyError, ValueError, TypeError):
        usage = None
    last = d["status"].get("last_poll_ok", {}).get("value")
    return {"version": version.current(), "live": d["cfg"]["live"], "paused": d["paused"] or None, "poll_seconds": d["cfg"]["poll_seconds"],
            "healthy": not floor._stale(d, now), "last_poll": float(last) if last else None, "max_parallel": d["cfg"]["max_parallel"],
            "running": [J.run_json(r) for r in d["running"]], "queued": [{k: x.get(k) for k in ("repo", "issue", "why")} for x in d["queued"]],
            "tiles": {**{k: v for k, v in t.items() if k != "failed"}, "failed": len(t["failed"])},
            "counts": board.counts(rows), "usage": usage, "needs_known": needs is not None}


def list_tickets(h, q, token) -> dict:
    cfg, now = h.app.cfg(), time.time()
    _choice(q, "stage", [k for k, _, _ in board.FILTERS])
    _choice(q, "sort", ("latest", "oldest", "priority"))
    _choice(q, "at", list(board.LABEL))
    db = _db(h)
    try:
        needs = L.needs_you(h) if q.get("fresh") == "1" else L.needs_cached()
        rows = board.ticket_rows(db, needs, L.titles_cached(), now, cfg.github_issues_enabled)
    finally:
        db.close()
    project = board.chosen_project(cfg, q.get("project", ""))
    if q.get("project") and not project:
        raise Error(422, "invalid", "That project is not configured.", {"project": "Not a configured project."})
    rows = board.in_project(rows, cfg, project)
    flt, at, text, order = board.list_filters(q, rows)
    shown = board.pick(rows, flt, at, text, order)
    limit = min(int(q["limit"]), 300) if q.get("limit", "").isdigit() and int(q["limit"]) > 0 else 300
    return {"stage": flt, "project": project, "counts": board.counts(rows), "total": len(shown),
            "tickets": [J.row_json(r) for r in shown[:limit]]}


def get_ticket(h, repo: str, n: int, q) -> dict:
    """One ticket in full. Its questions are read from GitHub unless cached=1 (then the last read, which may be a little old)."""
    cfg, now = h.app.cfg(), time.time()
    repo, n = L._repo(cfg, repo), int(n)
    gh_on = cfg.github_issues_enabled
    needs = L.needs_cached() if q.get("cached") == "1" else L.needs_you(h)
    db = _db(h)
    try:
        rows = board.ticket_rows(db, needs, L.titles_cached(), now, gh_on)
        sel = next((r for r in rows if r["repo"] == repo and r["issue"] == n), None)
        if sel is None and not gh_on and not tracker.is_local(n):
            raise Error(404, "not_found", "This ticket is hidden: it is a GitHub issue and Work from GitHub issues is off.")
        sel = sel or board.row_for(db, repo, n, needs, L.titles_cached(), now)
        if tracker.is_local(n) and LT.info(db, repo, n) is None:
            raise Error(404, "not_found", "There is no such local ticket.")
        _, docs, events, _ = board.enrich(db, sel)
        sel["queued"] = (repo, n) in L._approved(h)
        local = LT.ticket(db, repo, n) if tracker.is_local(n) else None
    finally:
        db.close()
    return J.detail_json(sel, docs, events, cfg, L.has_token(h), local)


def get_doc(h, repo: str, n: int, stage: str) -> dict:
    cfg = h.app.cfg()
    repo = L._repo(cfg, repo)
    if stage not in J.ROLES:
        raise Error(404, "not_found", "There is no such stage document.")
    db = _db(h)
    try:
        doc, text, source, cut = L.stage_doc_text(h, db, repo, int(n), stage)
    finally:
        db.close()
    if not doc and not text:
        raise Error(404, "not_found", f"The {stage} has not written a document for this ticket.")
    return J.doc_json(repo, n, stage, doc, text, source, cut)


def get_needs(h, q, token) -> dict:
    rows = L.needs_you(h)
    if rows is None:
        why = (L.NO_TOKEN if not L.has_token(h) else "GitHub did not answer, or no repository is configured. Try again in a minute.")
        return {"available": False, "reason": why, "tickets": []}
    return {"available": True, "reason": "", "tickets": [J.needs_row_json(r) for r in rows]}


def list_runs(h, q, token) -> dict:
    status = _choice(q, "status", RUN_STATUSES) or None
    repo = q.get("repo") or None
    if repo and repo not in h.app.cfg().repos:
        raise Error(422, "invalid", "That repository is not configured.", {"repo": "Not a configured repository."})
    page = int(q["page"]) if q.get("page", "").isdigit() and 1 <= int(q["page"]) <= 10000 else 1
    db = _db(h)
    try:
        rows = dbm.recent_runs(db, PAGE + 1, (page - 1) * PAGE, status, repo)
        summary = dbm.runs_summary(db, time.time() // 86400 * 86400, repo)
    finally:
        db.close()
    return {"page": page, "more": len(rows) > PAGE, "today": summary, "runs": [J.run_json(r) for r in rows[:PAGE]]}


def get_run(h, rid: int) -> dict:
    db = _db(h)
    try:
        run = dbm.get_run(db, rid)
    finally:
        db.close()
    if run is None:
        raise Error(404, "not_found", "There is no such run.")
    return J.run_json(run, full=True)


def list_events(h, q, token) -> dict:
    kind = q.get("kind") or "important"
    if not re.fullmatch(r"[a-z][a-z:_-]{0,30}", kind):
        raise Error(422, "invalid", "That kind of event is not valid.", {"kind": "Not a kind of event."})
    before = int(q["before"]) if q.get("before", "").isdigit() else None
    db = _db(h)
    try:
        rows = dbm.recent_events(db, 101, None if kind == "all" else kind, before)
    finally:
        db.close()
    return {"kind": kind, "events": [J.event_json(e) for e in rows[:100]], "before": rows[99]["id"] if len(rows) > 100 else None}


# ------------------------------------------------------------------ writes
def _picks(answers) -> dict:
    """{question id: {"option": id} | {"other": text}} -> questions.record's picks."""
    if not isinstance(answers, dict) or len(answers) > 50:
        raise Error(422, "invalid", "answers must map question ids to an option or your own words.", {"answers": "Not valid."})
    picks = {}
    for qid, a in answers.items():
        if not isinstance(a, dict) or len(a) != 1:
            raise Error(422, "invalid", f"The answer to {qid} must be {{\"option\": id}} or {{\"other\": text}}.", {"answers": "Not valid."})
        (kind, value), = a.items()
        if kind not in ("option", "other") or not isinstance(value, str) or not value.strip() or len(value) > Q.MAX_OTHER:
            raise Error(422, "invalid", f"The answer to {qid} must be {{\"option\": id}} or {{\"other\": text}}.", {"answers": "Not valid."})
        picks[str(qid)] = (kind, value.strip())
    return picks


def ticket_post(h, repo: str, n: int, action: str, data: dict) -> tuple[int, dict]:
    if action == "start":
        return _ok(L.start_ticket(h, repo, n, _text(data, "action", limit=40)))
    if action == "send-back":
        return _ok(L.send_back_ticket(h, repo, n, _text(data, "stage", limit=40)))
    if action == "answer":
        accept = data.get("accept_all") is True
        done, msg = L.answer_ticket(h, repo, n, _text(data, "stage", limit=20), None if accept else _picks(data.get("answers") or {}),
                                    accept, source="factory API")
        return _ok(msg, done=done)
    if action == "close":
        return _ok(L.close_ticket(h, repo, n))
    if action == "reopen":
        return _ok(LT.reopen(h, repo, n))
    if action == "comment":
        return _ok(LT.add_comment(h, repo, n, _text(data, "body")))
    if action == "edit":
        repo, n = LT.target(h, repo, n)
        db = _db(h)
        try:
            now = LT.ticket(db, repo, n) or {}
        finally:
            db.close()
        return _ok(LT.edit_ticket(h, repo, n, _text(data, "title", now.get("title") or "", 1000), _text(data, "body", now.get("body") or "")))
    if action == "priority":
        return _ok(PC.set_priority(h, repo, n, _text(data, "priority", limit=20)))
    raise Error(404, "not_found", "No such action.")


def create(h, data: dict) -> tuple[int, dict]:
    where = _text(data, "where", limit=10)
    if where not in ("", "local", "github"):
        raise Error(422, "invalid", "where is local or github.", {"where": "Not local or github."})
    repo = _text(data, "repo", limit=200)
    n, msg = L.create_ticket(h, repo, _text(data, "title", limit=1000), _text(data, "body"), _text(data, "start", limit=40), where)
    return 201, {"ok": True, "message": msg, "repo": repo, "issue": n, "ref": tracker.display(n)}


def answer_all(h, data: dict) -> tuple[int, dict]:
    refs = data.get("tickets")
    if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs):
        raise Error(422, "invalid", "tickets must be a list of owner/name#number.", {"tickets": "Not a list of tickets."})
    ok, failed = L.answer_all_tickets(h, refs, source="factory API")
    return 200, {"ok": bool(ok), "done": ok, "failed": failed,
                 "message": f"Recommendations accepted on {len(ok)} ticket{'s' if len(ok) != 1 else ''}." + (f" Could not do: {', '.join(failed)}." if failed else "")}


# ------------------------------------------------------------------ routing
def handle(h, method: str, path: str, q: dict, data: dict, token: dict) -> tuple[int, dict]:
    """One API request. method is GET or POST (HEAD arrives as GET); data is the JSON body of a POST ({} for a GET)."""
    rest = path[len(PREFIX):] if path.startswith(PREFIX) else None
    if rest is None:
        return 404, {"error": "No such API path. This factory speaks /api/v1/.", "code": "not_found"}
    t, m, r = TICKET.fullmatch(rest), MERGE.fullmatch(rest), RUN.fullmatch(rest)
    known_get = rest in GETS or bool(r) or bool(t and (t.group(4) is None or (t.group(4) == "docs" and t.group(5))))
    known_post = rest in POSTS or bool(m) or bool(t and t.group(4) in TICKET_POSTS and t.group(5) is None)
    if not (known_get or known_post):
        return 404, {"error": "No such API path.", "code": "not_found"}
    if (method == "GET" and not known_get) or (method == "POST" and not known_post):
        return 405, {"error": f"{method} is not allowed here.", "code": "method"}
    if method == "POST" and token.get("scope") != "write":
        return 403, {"error": "This token can only read. Make a write token to change anything.", "code": "read_only"}
    try:
        if method == "GET":
            if rest in GETS:
                fn = {"version": get_version, "meta": get_meta, "status": get_status, "tickets": list_tickets, "needs": get_needs,
                      "runs": list_runs, "events": list_events}[rest]
                return 200, fn(h, q, token)
            if r:
                return 200, get_run(h, int(r.group(1)))
            repo, n = f"{t.group(1)}/{t.group(2)}", int(t.group(3))
            return 200, (get_doc(h, repo, n, t.group(5)) if t.group(4) == "docs" else get_ticket(h, repo, n, q))
        log.info("api: POST %s by token %s", path, token.get("name"))
        if rest == "pause":
            pause.pause_manual(h.app.state_dir())
            return _ok("Paused. Work that is running finishes; nothing new starts until you resume.")
        if rest == "resume":
            pause.resume(h.app.state_dir())
            return _ok("Resumed. The factory takes new work at its next poll.")
        if rest == "tickets":
            return create(h, data)
        if rest == "answer-all":
            return answer_all(h, data)
        if m:
            return _ok(L.merge_pull(h, f"{m.group(1)}/{m.group(2)}", m.group(3)))
        return ticket_post(h, f"{t.group(1)}/{t.group(2)}", int(t.group(3)), t.group(4), data)
    except (Error, L.Refused, Q.Refused) as e:
        return _err(e)
