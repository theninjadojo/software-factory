"""Review videos in the admin UI: the switch on a ticket's page, the list of its PRs' videos, the player and the downloads.

The UI only writes the switch and queues rows (videos.set_enabled / videos.queue); the orchestrator makes the video. Every id and the
repository come from the database or the configured repositories, never a path from the browser: the file is videos.video_path(row).
Every route here is behind the admin login (server._handle) and the form posts check the CSRF token there."""
import sqlite3
import time
from urllib.parse import quote

from .. import videos, voices
from . import labels as L, views
from ..tracker import display
from .views import csrf_field, esc

BADGE = {"queued": ("Making…", "blue"), "making": ("Making…", "blue"), "ready": ("Ready", "good"), "skipped": ("Skipped", "warn"),
         "stale": ("Out of date", "warn"), "removed": ("Removed", "")}


def _link(path: str, vid: int) -> str:
    return f"{path}?id={int(vid)}"


def _row(cfg, v: dict) -> str:
    word, tone = BADGE.get(v["status"], (v["status"], ""))
    note = ""
    if v["status"] == "skipped":
        note = esc(videos.reasons(cfg.videos).get(v["reason"], "The video was not made."))
    elif v["status"] == "stale":
        note = "The pull request has new commits since this video was made."
    elif v["status"] == "removed":
        note = "This video is no longer available."
    acts = ""
    if v["status"] in ("ready", "stale"):
        acts = (f'<a href="{_link("/ticket/video", v["id"])}">Watch</a> · '
                f'<a href="{_link("/ticket/video/transcript", v["id"])}">Transcript</a>')
    length = esc(videos.clock(v["seconds"])) if v["status"] in ("ready", "stale") and v["seconds"] else ""
    return (f'<tr><td>{esc(v["pr_repo"])}#{int(v["pr_number"])}</td><td>{views.badge(word, tone)} <span class="muted">{note}</span></td>'
            f'<td>{length}</td><td>{acts}</td><td>{{again}}</td></tr>')


def fold(cfg, db, repo: str, issue: int, csrf: str, back: str, now: float) -> str:
    """The Review video card of a ticket's page; nothing when review videos are off in the config."""
    if not cfg.videos.enabled or db is None:
        return ""
    on = videos.enabled(db, repo, issue)
    voice, voice_id = videos.ticket_voice(db, repo, issue)
    hidden = f'{csrf_field(csrf)}<input type="hidden" name="repo" value="{esc(repo)}"><input type="hidden" name="n" value="{int(issue)}">' \
             f'<input type="hidden" name="back" value="{esc(back)}">'
    form = (f'<form method="post" action="/ticket/video/save" class="field">{hidden}'
            f'<label class="check"><input type="checkbox" name="on" value="1"{" checked" if on else ""} aria-describedby="rv-help"> '
            'Make a review video for each pull request</label>'
            '<p class="muted" id="rv-help">A short walkthrough of the screens and the diff, narrated from the automated review, with a '
            'transcript. Applies to open pull requests too. Making a video again counts toward the daily limit.</p>'
            + voice_fields(cfg, voice, voice_id) + '<button type="submit">Save</button></form>')
    rows = ""
    if on:
        try:
            vids = videos.for_ticket(db, repo, issue)
        except sqlite3.Error:
            vids = []
        for v in vids:
            again = ""
            if v["status"] in ("skipped", "stale", "removed"):
                again = (f'<form method="post" action="/ticket/video/again">{hidden}<input type="hidden" name="pr_repo" value="{esc(v["pr_repo"])}">'
                         f'<input type="hidden" name="pr" value="{int(v["pr_number"])}"><button class="secondary" type="submit">Make again</button></form>')
            rows += _row(cfg, v).replace("{again}", again)
        form += ('<table aria-live="polite"><thead><tr><th>PR</th><th>Status</th><th>Length</th><th></th><th></th></tr></thead><tbody>'
                 + rows + "</tbody></table>" if rows else '<p class="muted">No open pull requests yet.</p>')
    return views.fold("review-video", "Review video", "On" if on else "Off", form)


def voice_fields(cfg, voice: str, voice_id: str) -> str:
    """The voice choice. ElevenLabs is offered only with a saved key; it receives the narration text."""
    vc = cfg.videos
    has_key = bool(voices.read_key(vc.elevenlabs_key_file))
    default = "ElevenLabs" if vc.voice == "elevenlabs" else "Piper"
    opts = [("", f"Default ({default})"), ("piper", "Piper (offline)")] + ([("elevenlabs", "ElevenLabs")] if has_key or voice == "elevenlabs" else [])
    sel = "".join(f'<option value="{v}"{" selected" if v == voice else ""}>{esc(t)}</option>' for v, t in opts)
    out = f'<label>Voice <select name="voice">{sel}</select></label>'
    if has_key:
        out += (f'<label>ElevenLabs voice id <input name="voice_id" value="{esc(voice_id)}" pattern="[A-Za-z0-9]{{8,40}}" '
                f'placeholder="{esc(vc.elevenlabs_voice_id or "voice id")}" aria-describedby="rv-voice-help"></label>'
                '<p class="muted" id="rv-voice-help">ElevenLabs receives the narration text of each video (file names and the review\'s '
                'summary); Piper never leaves this machine. Leave the id empty for the configured voice.</p>')
    else:
        out += '<p class="muted">To use an ElevenLabs voice, save an ElevenLabs key on the Credentials page.</p>'
    return out


def save_post(h, form, csrf: str) -> None:
    cfg = h.app.cfg()
    try:
        repo, n = L._repo(cfg, form.get("repo", "")), L._number(form.get("n", ""))
    except L.Refused as e:
        return L._done(h, form, csrf, str(e), "bad")
    if not cfg.videos.enabled:
        return L._done(h, form, csrf, "Review videos are not turned on in the configuration.", "bad")
    voice, voice_id = form.get("voice", ""), form.get("voice_id", "").strip()
    if voice == "elevenlabs" and not (voice_id or cfg.videos.elevenlabs_voice_id):
        return L._done(h, form, csrf, "Give an ElevenLabs voice id, or set videos.elevenlabs_voice_id in the configuration.", "bad")
    db = sqlite3.connect(cfg.db_path, timeout=10)
    try:
        queued = videos.set_enabled(db, repo, n, form.get("on") == "1", "admin", time.time(), voice, voice_id)
    except ValueError:
        return L._done(h, form, csrf, "That is not a voice the factory knows (Piper, or ElevenLabs with a voice id of 8 to 40 letters and digits).", "bad")
    finally:
        db.close()
    L._done(h, form, csrf, f"Saved. Videos will be made for {queued} open pull request{'' if queued == 1 else 's'}." if form.get("on") == "1" else "Saved.")


def again_post(h, form, csrf: str) -> None:
    cfg = h.app.cfg()
    try:
        repo, n = L._repo(cfg, form.get("repo", "")), L._number(form.get("n", ""))
        pr = L._number(form.get("pr", ""))
    except L.Refused as e:
        return L._done(h, form, csrf, str(e), "bad")
    db = sqlite3.connect(cfg.db_path, timeout=10)
    try:
        # only a PR the ticket has (its own build PRs), never one named by the browser alone
        mine = {(r, int(x)) for r, x in videos.build_prs(db, repo, n)}
        queued = (form.get("pr_repo", ""), pr) in mine and cfg.videos.enabled and videos.queue(db, repo, n, form["pr_repo"], pr, time.time(), again=True)
    finally:
        db.close()
    L._done(h, form, csrf, "Queued. The video is made at the next poll." if queued else "Nothing queued: a video is already being made, or this is not a pull request of the ticket.",
            "ok" if queued else "bad")


def _video(h, q: dict, want_file: bool):
    """The ready (or out-of-date) video row named by ?id=, with its file; None after answering 404."""
    cfg = h.app.cfg()
    db = h.app.ro_db()
    row = videos.get(db, q.get("id", "")) if db is not None and cfg.videos.enabled else None
    if db is not None:
        db.close()
    if row is None or row["status"] not in ("ready", "stale"):
        h._send(404, "This video is no longer available.", "text/plain")
        return None
    path = videos.video_path(cfg, row)
    if want_file and (path.is_symlink() or not path.is_file()):
        h._send(404, "This video is no longer available.", "text/plain")
        return None
    return cfg, row, path


def player_csp(headers: dict) -> str:
    """The site's policy plus media from this origin, as a directive of its own (appended without a separator it would join the
    last directive's source list, and the player would be refused)."""
    return headers["Content-Security-Policy"].rstrip("; ") + "; media-src 'self'"


def player_get(h, q: dict, csrf: str) -> None:
    got = _video(h, q, False)
    if got is None:
        return
    cfg, row, _ = got
    from . import server
    vid = int(row["id"])
    lines = "".join(f'<li><span class="mono muted">{esc(videos.clock(t))}</span> {esc(s)}</li>' for t, s in videos.transcript(row))
    stale = ('<div class="card"><p>The pull request has new commits since this video was made.</p></div>' if row["status"] == "stale" else "")
    head = esc(row["title"]) if row.get("kind") == "brief" else f'{esc(row["pr_repo"])}#{int(row["pr_number"])}'
    body = (f'<h2>{head}</h2>{stale}'
            f'<video controls preload="metadata" width="100%" src="{_link("/ticket/video/file", vid)}">The video could not be played. Download it instead.</video>'
            f'<p class="muted">{esc(videos.clock(row["seconds"]))} · {int(row["bytes"]) // 1024} KB</p>'
            f'<p><a href="{_link("/ticket/video/file", vid)}&amp;dl=1">Download video</a> · '
            f'<a href="{_link("/ticket/video/transcript", vid)}">Download transcript</a></p>'
            f'<section aria-label="Transcript"><h2>Transcript</h2>' + (f"<ol>{lines}</ol>" if lines else '<p class="muted">No transcript was saved for this video.</p>')
            + "</section>")
    h._send(200, views.page("Video" if row.get("kind") == "brief" else "Review video", body,
                            "/videos" if row.get("kind") == "brief" else "/ticket/video", csrf, nav=NAV), extra={"Content-Security-Policy": player_csp(server.HEADERS)})


def file_get(h, q: dict, csrf: str) -> None:
    got = _video(h, q, True)
    if got:
        _, row, path = got
        h._send_media(path, "video/mp4", f"review-{int(row['id'])}.mp4", q.get("dl") != "1")


def transcript_get(h, q: dict, csrf: str) -> None:
    got = _video(h, q, False)
    if got:
        _, row, _ = got
        text = videos.transcript_text(row) or "No transcript was saved for this video.\n"
        h._send(200, text, "text/plain; charset=utf-8", {"Content-Disposition": f'attachment; filename="review-{int(row["id"])}.txt"'})


NAV = views.NAV[:4] + [("/videos", "Videos")] + views.NAV[4:]


def list_get(h, q: dict, csrf: str) -> None:
    """The Videos page: the videos asked for in the factory chat, newest first."""
    cfg = h.app.cfg()
    shown = L.flash_pop(csrf)
    if not cfg.videos.enabled:
        body = '<p>Videos are switched off. Turn them on in <a href="/settings">Settings</a>, Review videos.</p>'
        return h._send(200, views.page("Videos", body, "/videos", csrf, nav=NAV))
    db = h.app.ro_db()
    try:
        rows = videos.briefs(db) if db is not None else []
    finally:
        if db is not None:
            db.close()
    out = ""
    for v in rows:
        word, tone = BADGE.get(v["status"], (v["status"], ""))
        note = esc(videos.reasons(cfg.videos).get(v["reason"], "The video was not made.")) if v["status"] == "skipped" else (
            "This video is no longer available." if v["status"] == "removed" else "")
        ready = v["status"] in ("ready", "stale")
        acts = (f'<a href="{_link("/ticket/video", v["id"])}">Watch</a> · <a href="{_link("/ticket/video/transcript", v["id"])}">Transcript</a>'
                if ready else "")
        again = (f'<form method="post" action="/videos/again" class="inline">{csrf_field(csrf)}<input type="hidden" name="id" value="{int(v["id"])}">'
                 '<button class="secondary">Make again</button></form>' if v["status"] in ("ready", "stale", "skipped", "removed") else "")
        tickets = ", ".join(f"{r}{display(n)}" for r, n, _ in videos.sources_of(v))
        out += (f'<tr><td data-l="Video"><b>{esc(v["title"])}</b><br><span class="muted">{esc(tickets)}</span></td>'
                f'<td data-l="Status">{views.badge(word, tone)} <span class="muted">{note}</span></td>'
                f'<td data-l="Length">{esc(videos.clock(v["seconds"])) if ready and v["seconds"] else ""}</td>'
                f'<td data-l="Made">{esc(views.ago(v["created"]))}</td><td class="actions">{acts} {again}</td></tr>')
    intro = ('<p class="muted">Videos asked for in the <a href="/chat">Chat</a>: ask it for a video about some tickets, then confirm its '
             'proposal. Review videos of a ticket\'s pull requests are on the ticket\'s page.</p>')
    table = (f'<table class="stack" aria-live="polite"><thead><tr><th>Video</th><th>Status</th><th>Length</th><th>Made</th><th></th></tr></thead><tbody>{out}</tbody></table>'
             if out else '<p class="muted">No videos yet.</p>')
    h._send(200, views.page("Videos", intro + table, "/videos", csrf, nav=NAV, flash=shown[0] if shown else None,
                            flash_kind=shown[1] if shown else "ok"))


def again_brief_post(h, form, csrf: str) -> None:
    cfg = h.app.cfg()
    vid = form.get("id", "")
    queued = None
    if cfg.videos.enabled and vid.isdigit() and len(vid) <= 12:
        db = sqlite3.connect(cfg.db_path, timeout=10)
        try:
            queued = videos.again_brief(db, int(vid), time.time())
        finally:
            db.close()
    L.flash_set(csrf, "Queued. The video is made at the next poll." if queued else "Nothing queued: this video is already being made.",
                "ok" if queued else "bad")
    h._redirect("/videos")
