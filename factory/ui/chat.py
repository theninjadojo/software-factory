"""The ticket chat in the admin UI: a person's messages, the stored conversation, and confirming a proposal.

The UI only writes: a message becomes a pending turn that the orchestrator's chat lane answers (factory.chat). A reply's proposal
("send the ticket back to the <stage> stage") does nothing until a signed-in admin presses Confirm, which queues the existing
`redirect:<stage>` approval; the proposal is re-read from the database, never from the form. Every handler runs behind the
session, Host and CSRF checks of the server."""
import logging
import sqlite3
import time
from urllib.parse import quote

from .. import chat as C
from .. import pause, tracker
from ..sanitize import md_render
from . import labels as L
from . import localtickets as LT
from .views import ago, csrf_field, esc

log = logging.getLogger("factory.ui")
HOUR = 3600


def _ticket(h, form) -> tuple[str, int]:
    cfg = h.app.cfg()
    repo, n = L._repo(cfg, form.get("repo", "")), L._number(form.get("n", ""))
    if tracker.is_local(n):
        LT._target(h, form)                                  # raises Refused when there is no such local ticket
    elif not cfg.github_issues_enabled:
        raise L.Refused("GitHub issues are switched off, so this ticket is hidden.")
    return repo, n


def _write(h, fn):
    """A short write to the factory's database, like a queued approval."""
    db = sqlite3.connect(h.app.cfg().db_path, timeout=10)
    try:
        C.ensure_tables(db)
        return fn(db)
    finally:
        db.close()


def _why_not(cfg, state_dir) -> str:
    """Why a message cannot be answered right now, or ''."""
    if not cfg.chat.enabled:
        return "Ticket chat is switched off (set enabled = true under [chat])."
    if cfg.dry_run:
        return "The factory is in dry-run, so no reply will run."
    if pause.paused(state_dir):
        return "The factory is paused, so no reply will run."
    return ""


def _turn_html(t: dict) -> str:
    if t["author"] == "agent":
        return (f'<div class="chat-turn chat-agent"><p class="muted">Assistant · {esc(ago(t["created"]))}</p>'
                f'<div class="md">{md_render(t["body"])[0]}</div></div>')
    note = {"failed": "No reply: ", "refused": "No reply: "}.get(t["status"], "")
    tail = (f'<p class="muted">{esc(note + t["detail"])}</p>' if note and t["detail"] else
            '<p class="muted">Waiting for a reply…</p>' if t["status"] in ("pending", "running") else "")
    return (f'<div class="chat-turn chat-person"><p class="muted">You · {esc(ago(t["created"]))}</p>'
            f'<p class="chat-text">{esc(t["body"])}</p>{tail}</div>')


def _proposal_html(t: dict, hidden: str, back: str) -> str:
    p = t["proposal"]
    if not p or t["proposal_state"] != "open":
        done = {"confirmed": "Sent back (confirmed).", "dismissed": "Proposal dismissed."}.get(t["proposal_state"] or "", "")
        return f'<p class="muted">{esc(done)}</p>' if done and p else ""
    b = f'<input type="hidden" name="back" value="{esc(back)}"><input type="hidden" name="id" value="{int(t["id"])}">'
    return (f'<div class="chat-proposal"><p>Proposal: send this ticket back to the <b>{esc(p["stage"])}</b> stage. The steps from there on '
            f'run again.{(" " + esc(p["reason"])) if p.get("reason") else ""}</p><div class="sd-acts">'
            f'<form method="post" action="/tickets/chat/confirm" class="inline">{hidden}{b}<button>Send back to {esc(p["stage"])}</button></form>'
            f'<form method="post" action="/tickets/chat/dismiss" class="inline">{hidden}{b}<button class="secondary">Dismiss</button></form>'
            "</div></div>")


def card(cfg, db, repo: str, n: int, csrf: str, back: str) -> str:
    """The chat panel of a ticket: the stored conversation, the newest open proposal, and the message form. Empty when the chat is off
    and there is nothing to show."""
    rows = C.turns(db, repo, n) if db is not None else []
    if not cfg.chat.enabled and not rows:
        return ""
    hidden = f'{csrf_field(csrf)}<input type="hidden" name="repo" value="{esc(repo)}"><input type="hidden" name="n" value="{int(n)}">'
    waiting = any(t["author"] == "person" and t["status"] in ("pending", "running") for t in rows)
    last_open = max((t["id"] for t in rows if t["proposal"] and t["proposal_state"] == "open"), default=None)
    turns = "".join(_turn_html(t) + (_proposal_html(t, hidden, back) if t["author"] == "agent" and t["id"] == last_open else "")
                    for t in rows) or '<p class="muted">Nothing yet. Ask about this ticket: replies come in seconds and change nothing.</p>'
    form = ('<p class="muted">Waiting for the reply…</p>' if waiting else
            f'<form method="post" action="/tickets/chat/send" class="field">{hidden}<input type="hidden" name="back" value="{esc(back)}">'
            f'<label>Message<textarea name="text" rows="3" required maxlength="{C.MAX_TEXT}"></textarea></label>'
            "<button>Send</button></form>") if cfg.chat.enabled else '<p class="muted">Ticket chat is switched off.</p>'
    return (f'<section class="sd-card sd-chat" aria-labelledby="chat-h" data-src="/fragment/chat?repo={esc(quote(repo, safe=""))}&amp;n={int(n)}"'
            f' data-pending="{1 if waiting else 0}"><div class="sd-cardhead"><h3 id="chat-h">Chat about this ticket</h3>'
            '<span class="muted">Kept in the factory only. Later stages see it as context.</span></div>'
            f'<div class="chat-turns">{turns}</div>{form}</section>')


def fragment_get(h, q: dict, csrf: str) -> None:
    try:
        repo, n = L._repo(h.app.cfg(), q.get("repo", "")), L._number(q.get("n", ""))
    except L.Refused:
        return h._send(404, "no such ticket", "text/plain")
    db = h.app.ro_db()
    try:
        html = card(h.app.cfg(), db, repo, n, csrf, f"/ticket?repo={quote(repo, safe='')}&n={n}")
    finally:
        if db is not None:
            db.close()
    h._send(200, html)


def send(h, form, csrf: str) -> None:
    cfg = h.app.cfg()
    text = (form.get("text") or "").replace("\r\n", "\n").strip()
    try:
        repo, n = _ticket(h, form)
        if (why := _why_not(cfg, h.app.state_dir())):
            raise L.Refused(why)
        if not text:
            raise L.Refused("Write a message first.")
        if len(text) > C.MAX_TEXT:
            raise L.Refused(f"The message is too long ({C.MAX_TEXT:,} characters at most).")

        def add(db):
            if C.busy(db, repo, n):
                raise L.Refused("Wait for the reply to your last message.")
            if C.sent_since(db, repo, n, time.time() - HOUR) >= cfg.chat.max_per_hour:
                raise L.Refused(f"That is {cfg.chat.max_per_hour} messages on this ticket in the last hour. Try again later.")
            return C.add_person(db, repo, n, text)
        _write(h, add)
    except L.Refused as e:
        return L._done(h, form, csrf, str(e), "bad")
    log.info("tickets: chat message on %s#%d", repo, n)
    L._done(h, form, csrf, "Sent. The reply appears here in a moment.")


def _proposal(db, repo: str, n: int, form) -> dict:
    """The open proposal of the agent turn the form names, read from the database: {id, stage}. Raises L.Refused."""
    tid = form.get("id", "")
    if not tid.isdigit() or len(tid) > 12:
        raise L.Refused("That proposal is not valid.")
    row = db.execute("SELECT repo, issue, proposal, proposal_state FROM ticket_chat WHERE id=? AND author='agent'", (int(tid),)).fetchone()
    p = C._load(row[2]) if row else None
    if row is None or (row[0], row[1]) != (repo, n) or p is None or row[3] != "open":
        raise L.Refused("That proposal is no longer open.")
    return {"id": int(tid), "stage": p.get("stage")}


def confirm(h, form, csrf: str) -> None:
    """Queue the proposed send-back, as the approval the orchestrator already handles (it re-checks that the stage is configured and
    the ticket is open, and waits for the ticket's own job to end)."""
    cfg = h.app.cfg()
    try:
        repo, n = _ticket(h, form)

        def take(db):
            p = _proposal(db, repo, n, form)
            if p["stage"] not in [r.name for r in cfg.roles]:
                raise L.Refused("That stage is no longer configured.")
            if (repo, n) in L._approved(h):
                raise L.Refused("Another decision is already queued for this ticket. Try again once the factory has handled it.")
            if not C.set_proposal_state(db, p["id"], "open", "confirmed"):
                raise L.Refused("That proposal is no longer open.")
            return p
        p = _write(h, take)
        try:
            h.app.add_approval(repo, n, f"redirect:{p['stage']}")
        except Exception:
            _write(h, lambda db: C.set_proposal_state(db, p["id"], "confirmed", "open"))       # nothing was queued: let it be tried again
            raise
    except L.Refused as e:
        return L._done(h, form, csrf, str(e), "bad")
    log.info("tickets: chat proposal confirmed, %s#%d sent back to %s", repo, n, p["stage"])
    L._done(h, form, csrf, f"Queued: the ticket goes back to the {p['stage']} stage at the factory's next poll.")


def dismiss(h, form, csrf: str) -> None:
    try:
        repo, n = _ticket(h, form)

        def drop(db):
            p = _proposal(db, repo, n, form)
            C.set_proposal_state(db, p["id"], "open", "dismissed")
        _write(h, drop)
    except L.Refused as e:
        return L._done(h, form, csrf, str(e), "bad")
    L._done(h, form, csrf, "Proposal dismissed.")
