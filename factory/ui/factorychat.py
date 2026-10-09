"""The factory chat page (/chat): one shared conversation about the factory as a whole, its tickets and its settings.

The UI only writes: a message becomes a pending turn that the orchestrator's factory-chat lane answers (factory.factorychat). Nothing
in a reply is acted on. Every handler runs behind the session, Host and CSRF checks of the server."""
import logging
import sqlite3
import time

from .. import factorychat as F
from .. import pause
from ..sanitize import md_render
from . import labels as L
from . import views
from .views import ago, csrf_field, esc

log = logging.getLogger("factory.ui")


def _why_not(cfg, state_dir) -> str:
    """Why a message cannot be answered right now, or ''."""
    if not cfg.chat.factory_enabled:
        return "The factory chat is switched off."
    if cfg.dry_run:
        return "The factory is in dry-run, so the chat will not reply. Go live on the Factory page first."
    if pause.paused(state_dir):
        return "The factory is paused, so the chat will not reply until it resumes."
    return ""


def _turn_html(t: dict) -> str:
    if t["author"] == "agent":
        cut = '<p class="muted">Some tickets or details were left out of what it saw.</p>' if t["snapshot_cut"] else ""
        return (f'<div class="chat-turn chat-agent"><p class="muted">Factory · {esc(ago(t["created"]))}</p>'
                f'<div class="md">{md_render(t["body"])[0]}</div>{cut}</div>')
    note = "No reply: " if t["status"] in ("failed", "refused") else ""
    tail = (f'<p class="muted">{esc(note + t["detail"])}</p>' if note and t["detail"] else
            '<p class="muted">Waiting for the reply…</p>' if t["status"] in ("pending", "running") else "")
    return (f'<div class="chat-turn chat-person"><p class="muted">You · {esc(ago(t["created"]))}</p>'
            f'<p class="chat-text">{esc(t["body"])}</p>{tail}</div>')


def body(cfg, db, csrf: str, state_dir) -> str:
    """The conversation and the message form. It replaces itself while a reply is awaited (app.js chatTick polls it)."""
    rows = F.turns(db) if db is not None else []
    waiting = any(t["author"] == "person" and t["status"] in ("pending", "running") for t in rows)
    turns = "".join(_turn_html(t) for t in rows) or ('<p class="muted">Ask what a ticket is waiting for, why one is stuck, what is '
                                                      'running, or what a setting does.</p>')
    why = _why_not(cfg, state_dir)
    if not cfg.chat.factory_enabled:
        form = ('<p>The factory chat is off. Switch on <strong>Enable factory chat</strong> in '
                '<a href="/settings?section=chat">Settings, Ticket chat</a>.</p>')
    elif waiting:
        form = '<p class="muted">The factory is writing…</p>'
    else:
        form = (f'<form method="post" action="/chat/send" class="fc-reply">{csrf_field(csrf)}'
                f'<div class="field"><label for="fc-text">Your message</label>'
                f'<textarea id="fc-text" name="text" rows="3" maxlength="{F.MAX_TEXT}" required></textarea></div>'
                f'{f"<p class=muted>{esc(why)}</p>" if why else ""}<button>Send</button></form>')
    return (f'<div class="fc-chat" data-src="/fragment/factory-chat" data-pending="{1 if waiting else 0}">'
            f'<section class="card" aria-label="Conversation"><div class="chat-turns">{turns}</div>{form}</section></div>')


def get(h, q: dict, csrf: str) -> None:
    cfg = h.app.cfg()
    db = h.app.ro_db()
    try:
        html = body(cfg, db, csrf, h.app.state_dir())
    finally:
        if db is not None:
            db.close()
    shown = L.flash_pop(csrf)
    intro = ('<p class="muted">Ask about the factory: its tickets and its settings. It answers from a snapshot taken when you send, '
             'and it is read-only: it changes nothing, but says where you can. Everyone signed in shares this conversation.</p>')
    h._send(200, views.page("Chat", intro + html, "/chat", csrf, flash=shown[0] if shown else None, flash_kind=shown[1] if shown else "ok"))


def fragment_get(h, q: dict, csrf: str) -> None:
    db = h.app.ro_db()
    try:
        html = body(h.app.cfg(), db, csrf, h.app.state_dir())
    finally:
        if db is not None:
            db.close()
    h._send(200, html)


def send(h, form, csrf: str) -> None:
    cfg = h.app.cfg()
    text = (form.get("text") or "").replace("\r\n", "\n").strip()
    try:
        if (why := _why_not(cfg, h.app.state_dir())):
            raise L.Refused(why)
        if not text:
            raise L.Refused("Write a message first.")
        if len(text) > F.MAX_TEXT:
            raise L.Refused(f"The message is too long ({F.MAX_TEXT:,} characters at most).")
        db = sqlite3.connect(cfg.db_path, timeout=10)
        try:
            F.ensure_tables(db)
            if F.busy(db):
                raise L.Refused("Wait for the reply to the last message first.")
            if F.sent_since(db, time.time() - 3600) >= cfg.chat.factory_max_per_hour:
                raise L.Refused("That is enough messages for this hour. Try again later.")
            F.add_person(db, text)
        finally:
            db.close()
    except L.Refused as e:
        L.flash_set(csrf, str(e), "bad")
        return h._redirect("/chat")
    log.info("factory chat: message sent")
    h._redirect("/chat")


GET = {"/chat": get, "/fragment/factory-chat": fragment_get}
POST = {"/chat/send": send}
