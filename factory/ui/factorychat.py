"""The factory chat page (/chat): conversations about the factory as a whole, its tickets and its settings.

Each conversation (?c=<id>) is answered with only its own turns as context. Conversations are shared by everyone signed in; a
conversation can be deleted, or every other one at once, except while a message of it waits for a reply.

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


def _turn_html(t: dict, where: str = "chat") -> str:
    if t["author"] == "agent":
        cut = '<p class="muted">Some tickets or details were left out of what it saw.</p>' if t["snapshot_cut"] else ""
        watch = f'<p><a href="/videos">Open Videos</a></p>' if t.get("video_id") and where != "videos" else ""
        return (f'<div class="chat-turn chat-agent"><p class="muted">Factory · {esc(ago(t["created"]))}</p>'
                f'<div class="md">{md_render(t["body"])[0]}</div>{watch}{cut}</div>')
    note = "No reply: " if t["status"] in ("failed", "refused") else ""
    tail = (f'<p class="muted">{esc(note + t["detail"])}</p>' if note and t["detail"] else
            '<p class="muted">Waiting for the reply…</p>' if t["status"] in ("pending", "running") else "")
    return (f'<div class="chat-turn chat-person"><p class="muted">You · {esc(ago(t["created"]))}</p>'
            f'<p class="chat-text">{esc(t["body"])}</p>{tail}</div>')


def _proposal_html(cfg, t: dict, csrf: str, where: str = "chat") -> str:
    """A video proposal: what it covers, and Make video / Dismiss while it is open."""
    p = t["proposal"]
    if not p:
        return ""
    if t["proposal_state"] != "open" or not cfg.videos.enabled:
        queued = "Video queued. It is in the list below." if where == "videos" else "Video queued. It appears on the Videos page."
        done = {"confirmed": queued, "dismissed": "Proposal dismissed."}.get(t["proposal_state"] or "", "")
        return f'<p class="muted">{esc(done)}</p>' if done else ""
    from ..tracker import display
    tickets = "".join(f'<li>{esc(r)}{esc(display(n))} {esc(title)}</li>' for r, n, title in p["tickets"])
    files = "".join(f'<li class="mono">{esc(f)}</li>' for f in p.get("files") or [])
    demo = "".join(f'<li class="mono">{esc(f)}</li>' for f in p.get("demo") or [])
    hidden = (f'{csrf_field(csrf)}<input type="hidden" name="id" value="{int(t["id"])}">'
              f'<input type="hidden" name="c" value="{int(t["thread_id"] or 0)}">' + _in(where))
    return (f'<div class="chat-proposal" aria-label="Video proposal"><p><b>Proposed video: {esc(p["title"])}</b></p><p>{esc(p["brief"])}</p>'
            + (f'<p class="muted">About these tickets:</p><ul>{tickets}</ul>' if tickets else "")
            + (f'<p class="muted">About this code (default branch):</p><ul>{files}</ul>' if files else "")
            + (f'<p class="muted">Showing these tests running in the app, a screen after each step (a worker runs them first):</p>'
               f'<ul>{demo}</ul>' if demo else "")
            + f'<form method="post" action="/chat/video/make" class="field">{hidden}{RVID.voice_fields(cfg, "", "")}'
            f'<label class="check"><input type="checkbox" name="show_code" value="1"{" checked" if p.get("show_code") else ""}> '
            'Show code in the video</label><p class="muted">Off: the video shows only the app\'s screens.</p>'
            '<div class="sd-acts"><button>Make video</button></div></form>'
            f'<form method="post" action="/chat/video/dismiss" class="inline">{hidden}<button class="secondary">Dismiss</button></form></div>')


def _cid(q) -> int | None:
    c = str(q.get("c", ""))
    return int(c) if c.isdigit() and len(c) <= 12 else None


def _where(q) -> str:
    """Which page a conversation is shown on: the Videos page keeps a video request there, else the Chat page."""
    return "videos" if q.get("in") == "videos" else "chat"


def _in(where: str) -> str:
    return '<input type="hidden" name="in" value="videos">' if where == "videos" else ""


def _back(tid, where: str = "chat") -> str:
    page = "/videos" if where == "videos" else "/chat"
    return f"{page}?c={int(tid)}" if tid else page


def body(cfg, db, csrf: str, state_dir, tid: int | None = None, where: str = "chat") -> str:
    """One conversation (or, without `tid`, an empty new one) and the message form, on the Chat page or (where='videos') the Videos
    page. It replaces itself while a reply is awaited (app.js chatTick polls it)."""
    rows = F.turns(db, tid) if db is not None else []
    waiting = any(t["author"] == "person" and t["status"] in ("pending", "running") for t in rows)
    last_open = max((t["id"] for t in rows if t["proposal"] and t["proposal_state"] == "open"), default=None)
    turns = "".join(_turn_html(t, where) + (_proposal_html(cfg, t, csrf, where) if t["id"] == last_open or t["proposal_state"] in ("confirmed", "dismissed")
                                     else "") for t in rows) or ('<p class="muted">Ask what a ticket is waiting for, why one is stuck, what is '
                                                      'running, or what a setting does.</p>')
    why = _why_not(cfg, state_dir)
    if not cfg.chat.factory_enabled:
        form = ('<p>The factory chat is off. Switch on <strong>Enable factory chat</strong> in '
                '<a href="/settings?section=chat">Settings, Ticket chat</a>.</p>')
    elif waiting:
        form = '<p class="muted">The factory is writing…</p>'
    else:
        keep = (f'<input type="hidden" name="c" value="{int(tid)}">' if tid else "") + _in(where)
        form = (f'<form method="post" action="/chat/send" class="fc-reply">{csrf_field(csrf)}{keep}'
                f'<div class="field"><label for="fc-text">{"Your message" if tid else "Start a new conversation"}</label>'
                f'<textarea id="fc-text" name="text" rows="3" maxlength="{F.MAX_TEXT}" required></textarea></div>'
                f'{f"<p class=muted>{esc(why)}</p>" if why else ""}<button>Send</button></form>')
    src = (f"/fragment/factory-chat?c={int(tid)}" if tid else "/fragment/factory-chat?c=new") + ("&amp;in=videos" if where == "videos" else "")
    return (f'<div class="fc-chat" data-src="{src}" data-pending="{1 if waiting else 0}">'
            f'<section class="card" aria-label="Conversation"><div class="chat-turns">{turns}</div>{form}</section></div>')


def threads_html(threads: list, tid: int | None, csrf: str) -> str:
    """The list of conversations, with New conversation and Delete all others. On a phone it folds into a menu above the chat."""
    items = "".join(f'<li><a href="/chat?c={int(t["id"])}"{" class=active aria-current=page" if t["id"] == tid else ""}>'
                    f'<span class="fc-t">{esc(t["title"])}</span><span class="muted fc-when">{esc(ago(t["updated"]))}'
                    f'{" · waiting" if t["waiting"] else ""}</span></a></li>' for t in threads)
    others = [t for t in threads if t["id"] != tid and not t["waiting"]]
    clean = (f'<details class="fc-clean"><summary class="btn secondary">Delete other conversations…</summary>'
             f'<form method="post" action="/chat/delete-others" class="card">{csrf_field(csrf)}'
             f'<input type="hidden" name="c" value="{int(tid or 0)}"><p>Delete {len(others)} conversation{"" if len(others) == 1 else "s"}'
             f'{" (all but this one)" if tid else ""}? This cannot be undone. Videos they asked for stay on the Videos page.</p>'
             '<button>Delete them</button></form></details>' if others else "")
    label = next((t["title"] for t in threads if t["id"] == tid), "New conversation")
    ul = f'<ul>{items or "<li class=muted>No conversations yet.</li>"}</ul>'
    return (f'<aside class="fc-threads" aria-label="Conversations"><a class="btn" href="/chat?c=new">New conversation</a>'
            f'<div class="fc-list fc-wide">{ul}</div>'
            f'<details class="fc-list fc-narrow"><summary><span class="muted">Conversation:</span> {esc(label)}</summary>{ul}</details>{clean}</aside>')


def _delete_this(tid: int, csrf: str) -> str:
    return (f'<details class="fc-clean fc-del"><summary class="btn secondary">Delete this conversation…</summary>'
            f'<form method="post" action="/chat/delete" class="card">{csrf_field(csrf)}<input type="hidden" name="c" value="{int(tid)}">'
            '<p>Delete this conversation? This cannot be undone. Videos it asked for stay on the Videos page.</p>'
            '<button>Delete it</button></form></details>')


def get(h, q: dict, csrf: str) -> None:
    cfg = h.app.cfg()
    db = h.app.ro_db()
    try:
        ts = F.threads(db) if db is not None else []
        tid = _cid(q)
        if q.get("c") != "new" and (tid is None or not any(t["id"] == tid for t in ts)):
            tid = ts[0]["id"] if ts and q.get("c") is None else None   # /chat opens the latest; an unknown id opens a new one
        html = body(cfg, db, csrf, h.app.state_dir(), tid)
        busy_here = any(t["id"] == tid and t["waiting"] for t in ts)
    finally:
        if db is not None:
            db.close()
    shown = L.flash_pop(csrf)
    intro = ('<p class="muted">Ask about the factory: its tickets and its settings. It answers from a snapshot taken when you send, '
             'and it is read-only: it changes nothing, but says where you can. Each conversation only remembers its own messages; '
             'start a new one for a new topic. Everyone signed in sees the same conversations.</p>')
    if cfg.videos.enabled:
        intro += ('<p class="muted">Ask it for a video about some tickets (for example "a two-minute tour of this week\'s board changes"). '
                  'It proposes one and you decide whether to make it. Made videos are on <a href="/videos">Videos</a>.</p>')
    main = intro + html + (_delete_this(tid, csrf) if tid and not busy_here else "")
    page = f'<div class="fc-layout">{threads_html(ts, tid, csrf)}<div class="fc-main">{main}</div></div>'
    h._send(200, views.page("Chat", page, "/chat", csrf, flash=shown[0] if shown else None, flash_kind=shown[1] if shown else "ok"))


def fragment_get(h, q: dict, csrf: str) -> None:
    db = h.app.ro_db()
    try:
        html = body(h.app.cfg(), db, csrf, h.app.state_dir(), _cid(q), _where(q))
    finally:
        if db is not None:
            db.close()
    h._send(200, html)


def send(h, form, csrf: str) -> None:
    """A message to the conversation the form names, or the first message of a new one."""
    cfg = h.app.cfg()
    text = (form.get("text") or "").replace("\r\n", "\n").strip()
    tid = _cid(form)
    try:
        if (why := _why_not(cfg, h.app.state_dir())):
            raise L.Refused(why)
        if not text:
            raise L.Refused("Write a message first.")
        if len(text) > F.MAX_TEXT:
            raise L.Refused(f"The message is too long ({F.MAX_TEXT:,} characters at most).")
        tid = ask(cfg, text, tid)
    except L.Refused as e:
        L.flash_set(csrf, str(e), "bad")
        return h._redirect(_back(tid, _where(form)) if tid else "/videos" if _where(form) == "videos" else "/chat?c=new")
    log.info("factory chat: message sent")
    h._redirect(_back(tid, _where(form)))


def ask(cfg, text: str, tid: int | None) -> int:
    """Add a person's message to conversation `tid`, or to a new one when it is None (or was deleted). Returns the conversation's
    id. Raises L.Refused with why not."""
    db = sqlite3.connect(cfg.db_path, timeout=10)
    try:
        F.ensure_tables(db)
        if tid is not None and F.thread(db, tid) is None:
            tid = None
        if tid is not None and F.busy(db, tid):
            raise L.Refused("Wait for the reply to the last message first.")
        if F.sent_since(db, time.time() - 3600) >= cfg.chat.factory_max_per_hour:
            raise L.Refused("That is enough messages for this hour. Try again later.")
        if tid is None:
            tid = F.new_thread(db, text)
        F.add_person(db, text, tid)
        return tid
    finally:
        db.close()


VIDEO_ASK = "Propose a video: "


def ask_video(h, form, csrf: str) -> None:
    """The Videos page's box: the description starts a new conversation asking the chat to propose that video."""
    cfg = h.app.cfg()
    text = (form.get("text") or "").replace("\r\n", "\n").strip()
    try:
        if not cfg.videos.enabled:
            raise L.Refused("Videos are switched off. Turn them on in Settings, Review videos.")
        if (why := _why_not(cfg, h.app.state_dir())):
            raise L.Refused(why)
        if not text:
            raise L.Refused("Describe the video first.")
        if len(VIDEO_ASK + text) > F.MAX_TEXT:
            raise L.Refused(f"The description is too long ({F.MAX_TEXT - len(VIDEO_ASK):,} characters at most).")
        tid = ask(cfg, VIDEO_ASK + text, None)
    except L.Refused as e:
        L.flash_set(csrf, str(e), "bad")
        return h._redirect("/videos")
    log.info("factory chat: video asked for from the Videos page")
    h._redirect(_back(tid, "videos"))


def delete(h, form, csrf: str) -> None:
    tid = _cid(form)
    db = sqlite3.connect(h.app.cfg().db_path, timeout=10)
    try:
        F.ensure_tables(db)
        gone = tid is not None and F.delete_thread(db, tid)
    finally:
        db.close()
    if not gone:
        L.flash_set(csrf, "That conversation is waiting for a reply, or is already gone.", "bad")
        return h._redirect(_back(tid))
    log.info("factory chat: conversation deleted")
    L.flash_set(csrf, "Conversation deleted.")
    h._redirect("/chat")


def delete_others(h, form, csrf: str) -> None:
    tid = _cid(form) or None
    db = sqlite3.connect(h.app.cfg().db_path, timeout=10)
    try:
        F.ensure_tables(db)
        n = F.delete_idle_threads(db, keep=tid)
    finally:
        db.close()
    log.info("factory chat: %s conversations deleted", n)
    L.flash_set(csrf, f"Deleted {n} conversation{'' if n == 1 else 's'}.")
    h._redirect(_back(tid))


def _open_proposal(db, form) -> dict:
    """The open video proposal of the agent turn the form names, read from the database. Raises L.Refused."""
    tid = form.get("id", "")
    if not tid.isdigit() or len(tid) > 12:
        raise L.Refused("That proposal is not valid.")
    F.ensure_tables(db)
    row = db.execute("SELECT proposal, proposal_state, thread_id FROM factory_chat WHERE id=? AND author='agent'", (int(tid),)).fetchone()
    p = F._load(row[0]) if row else None
    if p is None or row[1] != "open" or p.get("action") != "video":
        raise L.Refused("That proposal is no longer open.")
    return {"id": int(tid), "thread": row[2], **p}


def make_video(h, form, csrf: str) -> None:
    """Queue the proposed video once (open -> confirmed); the orchestrator makes it with the review video worker."""
    cfg = h.app.cfg()
    voice, voice_id, music = form.get("voice", ""), form.get("voice_id", "").strip(), form.get("music", "")
    try:
        if not cfg.videos.enabled:
            raise L.Refused("Videos are switched off. Turn them on in Settings, Review videos.")
        if voice == "elevenlabs" and not (voice_id or cfg.videos.elevenlabs_voice_id):
            raise L.Refused("Give an ElevenLabs voice id, or set one in Settings, Review videos.")
        if not videos.music_ok(music):
            raise L.Refused("That is not a music choice the factory knows (off, calm, bright or focused).")
        db = sqlite3.connect(cfg.db_path, timeout=10)
        try:
            p = _open_proposal(db, form)
            if not F.set_proposal_state(db, p["id"], "open", "confirmed"):
                raise L.Refused("That proposal is no longer open.")
            try:
                vid = videos.queue_brief(db, p["title"], p["brief"], [tuple(x) for x in p["tickets"]], time.time(), voice, voice_id,
                                         p.get("files") or [], p.get("demo") or [], form.get("show_code") == "1",
                                         music=music)
                F.link_video(db, p["id"], vid)
            except ValueError:
                F.set_proposal_state(db, p["id"], "confirmed", "open")          # nothing was queued: let it be tried again
                raise L.Refused("That is not a voice the factory knows (Piper, or ElevenLabs with a voice id of 8 to 40 letters and digits).")
        finally:
            db.close()
    except L.Refused as e:
        L.flash_set(csrf, str(e), "bad")
        return h._redirect(_back(_cid(form), _where(form)))
    log.info("factory chat: video proposal confirmed")
    L.flash_set(csrf, "Queued. The video is made at the next poll and appears in the list below." if _where(form) == "videos"
                else "Queued. The video is made at the next poll and appears on the Videos page.")
    h._redirect(_back(_cid(form), _where(form)))


def dismiss_video(h, form, csrf: str) -> None:
    db = sqlite3.connect(h.app.cfg().db_path, timeout=10)
    try:
        p = _open_proposal(db, form)
        F.set_proposal_state(db, p["id"], "open", "dismissed")
    except L.Refused as e:
        L.flash_set(csrf, str(e), "bad")
        return h._redirect(_back(_cid(form), _where(form)))
    finally:
        db.close()
    L.flash_set(csrf, "Proposal dismissed.")
    h._redirect(_back(_cid(form), _where(form)))


GET = {"/chat": get, "/fragment/factory-chat": fragment_get}
POST = {"/chat/send": send, "/chat/delete": delete, "/chat/delete-others": delete_others, "/videos/ask": ask_video, "/chat/video/make": make_video, "/chat/video/dismiss": dismiss_video}
