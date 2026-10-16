"""The factory chat page (/chat): one shared conversation about the factory as a whole, its tickets and its settings.

The UI only writes: a message becomes a pending turn that the orchestrator's factory-chat lane answers (factory.factorychat). Nothing
in a reply is acted on, except a video proposal a person confirms here: it is re-read from the database, never from the form, and
queued with videos.queue_brief. Every handler runs behind the session, Host and CSRF checks of the server."""
import logging
import sqlite3
import time

from .. import factorychat as F
from .. import pause, videos
from ..sanitize import md_render
from . import labels as L
from . import reviewvideo as RVID
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
        watch = f'<p><a href="/videos">Open Videos</a></p>' if t.get("video_id") else ""
        return (f'<div class="chat-turn chat-agent"><p class="muted">Factory · {esc(ago(t["created"]))}</p>'
                f'<div class="md">{md_render(t["body"])[0]}</div>{watch}{cut}</div>')
    note = "No reply: " if t["status"] in ("failed", "refused") else ""
    tail = (f'<p class="muted">{esc(note + t["detail"])}</p>' if note and t["detail"] else
            '<p class="muted">Waiting for the reply…</p>' if t["status"] in ("pending", "running") else "")
    return (f'<div class="chat-turn chat-person"><p class="muted">You · {esc(ago(t["created"]))}</p>'
            f'<p class="chat-text">{esc(t["body"])}</p>{tail}</div>')


def _proposal_html(cfg, t: dict, csrf: str) -> str:
    """A video proposal: what it covers, and Make video / Dismiss while it is open."""
    p = t["proposal"]
    if not p:
        return ""
    if t["proposal_state"] != "open" or not cfg.videos.enabled:
        done = {"confirmed": "Video queued. It appears on the Videos page.", "dismissed": "Proposal dismissed."}.get(t["proposal_state"] or "", "")
        return f'<p class="muted">{esc(done)}</p>' if done else ""
    from ..tracker import display
    tickets = "".join(f'<li>{esc(r)}{esc(display(n))} {esc(title)}</li>' for r, n, title in p["tickets"])
    hidden = f'{csrf_field(csrf)}<input type="hidden" name="id" value="{int(t["id"])}">'
    return (f'<div class="chat-proposal" aria-label="Video proposal"><p><b>Proposed video: {esc(p["title"])}</b></p><p>{esc(p["brief"])}</p>'
            f'<p class="muted">Made from these tickets\' pull requests and screenshots:</p><ul>{tickets}</ul>'
            f'<form method="post" action="/chat/video/make" class="field">{hidden}{RVID.voice_fields(cfg, "", "")}'
            '<div class="sd-acts"><button>Make video</button></div></form>'
            f'<form method="post" action="/chat/video/dismiss" class="inline">{hidden}<button class="secondary">Dismiss</button></form></div>')


def body(cfg, db, csrf: str, state_dir) -> str:
    """The conversation and the message form. It replaces itself while a reply is awaited (app.js chatTick polls it)."""
    rows = F.turns(db) if db is not None else []
    waiting = any(t["author"] == "person" and t["status"] in ("pending", "running") for t in rows)
    last_open = max((t["id"] for t in rows if t["proposal"] and t["proposal_state"] == "open"), default=None)
    turns = "".join(_turn_html(t) + (_proposal_html(cfg, t, csrf) if t["id"] == last_open or t["proposal_state"] in ("confirmed", "dismissed")
                                     else "") for t in rows) or ('<p class="muted">Ask what a ticket is waiting for, why one is stuck, what is '
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
    if cfg.videos.enabled:
        intro += ('<p class="muted">Ask it for a video about some tickets (for example "a two-minute tour of this week\'s board changes"). '
                  'It proposes one and you decide whether to make it. Made videos are on <a href="/videos">Videos</a>.</p>')
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


def _open_proposal(db, form) -> dict:
    """The open video proposal of the agent turn the form names, read from the database. Raises L.Refused."""
    tid = form.get("id", "")
    if not tid.isdigit() or len(tid) > 12:
        raise L.Refused("That proposal is not valid.")
    F.ensure_tables(db)
    row = db.execute("SELECT proposal, proposal_state FROM factory_chat WHERE id=? AND author='agent'", (int(tid),)).fetchone()
    p = F._load(row[0]) if row else None
    if p is None or row[1] != "open" or p.get("action") != "video":
        raise L.Refused("That proposal is no longer open.")
    return {"id": int(tid), **p}


def make_video(h, form, csrf: str) -> None:
    """Queue the proposed video once (open -> confirmed); the orchestrator makes it with the review video worker."""
    cfg = h.app.cfg()
    voice, voice_id = form.get("voice", ""), form.get("voice_id", "").strip()
    try:
        if not cfg.videos.enabled:
            raise L.Refused("Videos are switched off. Turn them on in Settings, Review videos.")
        if voice == "elevenlabs" and not (voice_id or cfg.videos.elevenlabs_voice_id):
            raise L.Refused("Give an ElevenLabs voice id, or set one in Settings, Review videos.")
        db = sqlite3.connect(cfg.db_path, timeout=10)
        try:
            p = _open_proposal(db, form)
            if not F.set_proposal_state(db, p["id"], "open", "confirmed"):
                raise L.Refused("That proposal is no longer open.")
            try:
                videos.queue_brief(db, p["title"], p["brief"], [tuple(x) for x in p["tickets"]], time.time(), voice, voice_id)
            except ValueError:
                F.set_proposal_state(db, p["id"], "confirmed", "open")          # nothing was queued: let it be tried again
                raise L.Refused("That is not a voice the factory knows (Piper, or ElevenLabs with a voice id of 8 to 40 letters and digits).")
        finally:
            db.close()
    except L.Refused as e:
        L.flash_set(csrf, str(e), "bad")
        return h._redirect("/chat")
    log.info("factory chat: video proposal confirmed")
    L.flash_set(csrf, "Queued. The video is made at the next poll and appears on the Videos page.")
    h._redirect("/chat")


def dismiss_video(h, form, csrf: str) -> None:
    db = sqlite3.connect(h.app.cfg().db_path, timeout=10)
    try:
        p = _open_proposal(db, form)
        F.set_proposal_state(db, p["id"], "open", "dismissed")
    except L.Refused as e:
        L.flash_set(csrf, str(e), "bad")
        return h._redirect("/chat")
    finally:
        db.close()
    L.flash_set(csrf, "Proposal dismissed.")
    h._redirect("/chat")


GET = {"/chat": get, "/fragment/factory-chat": fragment_get}
POST = {"/chat/send": send, "/chat/video/make": make_video, "/chat/video/dismiss": dismiss_video}
