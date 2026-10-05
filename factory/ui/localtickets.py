"""Local tickets on the Tickets page: their description, comments and labels, and the forms that edit, comment on, reopen them.

A local ticket has no GitHub page, so everything a person does with one happens here. Reading uses the read-only database
connection the page already holds (the tables may not exist yet); writes go through tracker.LocalTracker as the admin UI's actor.
Titles, bodies and comments are untrusted text (an import copies them from GitHub): they are escaped, and bodies go through
md_render, which never renders raw HTML."""
import logging
import sqlite3

from .. import db as dbm
from .. import tracker
from ..sanitize import md_render
from . import labels as L
from .views import ago, badge, csrf_field, esc

log = logging.getLogger("factory.ui")
AUTHOR = {tracker.FACTORY_AUTHOR: "Factory", tracker.UI_ACTOR: "You (admin UI)", tracker.IMPORTED: "Imported from GitHub"}


# ---------------------------------------------------------------- reading
def rows(db, limit: int = 300) -> list[dict]:
    """Every local ticket, most recently changed first, as ticket_rows seeds ({} fields it fills in)."""
    try:
        got = db.execute("SELECT repo, number, title, updated FROM local_tickets ORDER BY updated DESC LIMIT ?", (limit,)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [{"repo": r, "issue": n, "title": t, "detail": "", "decided_at": u} for r, n, t, u in got]


def info(db, repo: str, number: int) -> dict | None:
    """{title, state} of one local ticket, or None."""
    try:
        r = db.execute("SELECT title, state FROM local_tickets WHERE repo=? AND number=?", (repo, number)).fetchone()
    except sqlite3.OperationalError:
        return None
    return {"title": r[0], "state": r[1]} if r else None


def ticket(db, repo: str, number: int) -> dict | None:
    """The whole ticket: the GitHub-shaped issue (for the start actions) plus its comments, or None."""
    try:
        t = tracker.LocalTracker.__new__(tracker.LocalTracker)     # read-only: never ensure_tables on this connection
        t.db, t.actor = db, tracker.UI_ACTOR
        issue = t.issue(repo, number)
        issue["comment_list"] = t.comments(repo, number)
        issue["attachment_list"] = t.attachments(repo, number)
        issue["created"] = db.execute("SELECT created FROM local_tickets WHERE repo=? AND number=?", (repo, number)).fetchone()[0]
    except (LookupError, sqlite3.OperationalError):
        return None
    return issue


# ---------------------------------------------------------------- the card on the ticket's page
def _hidden(repo: str, n: int, csrf: str, back: str) -> str:
    return (f'{csrf_field(csrf)}<input type="hidden" name="repo" value="{esc(repo)}"><input type="hidden" name="n" value="{int(n)}">'
            f'<input type="hidden" name="back" value="{esc(back)}">')


def attach_help(cfg) -> str:
    max_bytes, max_files, _ = tracker.attach_limits(cfg)
    return (f'png, jpg, gif, pdf, txt, md, log, json or csv; up to {max_files} files of {max_bytes // (1024 * 1024)} MB. '
            'Agents working on the ticket can read them, so they are sent to the model provider.')


def attachments_html(cfg, issue: dict, repo: str, n: int, hidden: str) -> str:
    """The files on the ticket (downloads only, nothing is shown inline), a delete form with a confirmation, and the upload form."""
    items = "".join(
        f'<li><a href="/tickets/local/attachment?repo={esc(repo)}&amp;n={n}&amp;id={int(a["id"])}">{esc(a["name"])}</a> '
        f'<span class="muted">{max(1, int(a["size"]) // 1024)} KB</span> '
        f'<details class="disclose"><summary aria-label="Remove {esc(a["name"])}">Remove</summary>'
        f'<form method="post" action="/tickets/local/attachment/delete" class="inline">{hidden}'
        f'<input type="hidden" name="id" value="{int(a["id"])}"><p>Remove “{esc(a["name"])}” from this ticket? It cannot be undone.</p>'
        '<button class="secondary">Remove attachment</button></form></details></li>'
        for a in issue.get("attachment_list", []))
    return (f'<h4>Attachments</h4>{f"<ul class=lt-attachments>{items}</ul>" if items else "<p class=muted>No attachments.</p>"}'
            f'<form method="post" action="/tickets/local/attach" enctype="multipart/form-data" class="field">{hidden}'
            f'<label>Attach a file<input type="file" name="file" multiple required accept=".png,.jpg,.jpeg,.gif,.pdf,.txt,.md,.log,.json,.csv"></label>'
            f'<p class="muted">{esc(attach_help(cfg))}</p><button>Attach</button></form>')


def card(cfg, issue: dict, repo: str, csrf: str, back: str, decision: dict | None, approved: bool) -> str:
    n = int(issue["number"])
    hidden = _hidden(repo, n, csrf, back)
    status, acts = L.actions_for(cfg, issue, decision, approved)
    closed = issue["state"] == "closed"
    who = AUTHOR.get(issue["user"]["login"], issue["user"]["login"])
    labels = " ".join(L.chip(x, cfg) for x in L._names(issue)) or '<span class="muted">No labels.</span>'
    body = md_render(issue["body"])[0] if issue["body"].strip() else '<p class="muted">No description.</p>'
    comments = "".join(
        f'<li class="lt-c"><p class="muted lt-by"><b>{esc(AUTHOR.get(c["user"]["login"], c["user"]["login"]))}</b> · '
        f'{esc(ago(L._epoch(c["created_at"])))}</p><div class="doc">{md_render(c["body"])[0]}</div></li>'
        for c in issue["comment_list"])
    if closed:
        state = (f'<form method="post" action="/tickets/local/state" class="inline">{hidden}<input type="hidden" name="state" value="open">'
                 '<button class="secondary">Reopen</button></form>')
    else:                      # a busy ticket can be closed too: the factory stops its work once it sees the ticket closed
        note = '<span class="muted">The factory stops its work on it.</span> ' if status in L.BUSY else ""
        state = (f'<form method="post" action="/tickets/close" class="inline">{hidden}{note}'
                 '<button class="secondary">Close ticket</button></form>')
    start = L.action_forms(repo, n, acts, csrf, back) if acts and not closed else ""
    return (f'<section class="sd-card lt-card" aria-labelledby="lt-h"><div class="sd-cardhead"><h3 id="lt-h">Local ticket</h3>'
            f'{badge(issue["state"], "" if closed else "good")}</div>'
            f'<p class="muted">Opened by {esc(who)} {esc(ago(issue["created"]))}. Kept in the factory\'s own database, not on GitHub.</p>'
            f'<div class="doc lt-body">{body}</div><p class="lt-labels">{labels}</p>'
            f'<div class="sd-acts">{start}{state}</div>'
            f'<details class="disclose"><summary class="btn secondary">Edit title and description</summary>'
            f'<form method="post" action="/tickets/local/edit" class="field">{hidden}'
            f'<label>Title<input name="title" required maxlength="{L.MAX_TITLE}" value="{esc(issue["title"])}"></label>'
            f'<label>Description<textarea name="body" rows="6" maxlength="{L.MAX_BODY}">{esc(issue["body"])}</textarea></label>'
            '<button>Save</button></form></details>'
            f'{attachments_html(cfg, issue, repo, n, hidden)}'
            f'<h4>Comments</h4>{f"<ol class=lt-comments>{comments}</ol>" if comments else "<p class=muted>No comments yet.</p>"}'
            f'<form method="post" action="/tickets/local/comment" class="field">{hidden}'
            f'<label>Add a comment<textarea name="body" rows="3" required maxlength="{L.MAX_BODY}"></textarea></label>'
            '<button>Comment</button></form></section>')


# ---------------------------------------------------------------- actions
def _target(h, form) -> tuple[str, int]:
    cfg = h.app.cfg()
    repo, n = L._repo(cfg, form.get("repo", "")), L._number(form.get("n", ""))
    db = h.app.ro_db()
    try:
        if not tracker.is_local(n) or db is None or info(db, repo, n) is None:
            raise L.Refused("There is no such local ticket.")
    finally:
        if db is not None:
            db.close()
    return repo, n


def _store(h) -> tracker.LocalTracker:
    return tracker.LocalTracker(dbm.local(h.app.cfg().db_path), tracker.UI_ACTOR)


def comment(h, form, csrf: str) -> None:
    body = (form.get("body") or "").replace("\r\n", "\n").strip()
    try:
        repo, n = _target(h, form)
        if not body:
            raise L.Refused("Write a comment first.")
        if len(body) > L.MAX_BODY:
            raise L.Refused(f"The comment is too long ({L.MAX_BODY:,} characters at most).")
    except L.Refused as e:
        return L._done(h, form, csrf, str(e), "bad")
    _store(h).add_comment(repo, n, tracker.UI_ACTOR, body)
    log.info("tickets: comment on local %s %s", repo, tracker.display(n))
    L._done(h, form, csrf, "Comment added.")


def edit(h, form, csrf: str) -> None:
    title, body = " ".join((form.get("title") or "").split()), (form.get("body") or "").replace("\r\n", "\n").strip()
    try:
        repo, n = _target(h, form)
        if not title:
            raise L.Refused("Enter a title.")
        if len(title) > L.MAX_TITLE or len(body) > L.MAX_BODY:
            raise L.Refused("The title or the description is too long.")
    except L.Refused as e:
        return L._done(h, form, csrf, str(e), "bad")
    _store(h).edit(repo, n, title, body)
    log.info("tickets: edited local %s %s", repo, tracker.display(n))
    L._done(h, form, csrf, "Saved.")


def set_state(h, form, csrf: str) -> None:
    """Reopen a closed local ticket (closing goes through /tickets/close, which checks the ticket is not busy)."""
    try:
        repo, n = _target(h, form)
        if form.get("state") != "open":
            raise L.Refused("Use Close ticket to close it.")
    except L.Refused as e:
        return L._done(h, form, csrf, str(e), "bad")
    _store(h).set_state(repo, n, "open")
    log.info("tickets: reopened local %s %s", repo, tracker.display(n))
    L._done(h, form, csrf, f"Reopened {tracker.display(n)}.")


def create(cfg, repo: str, title: str, body: str, labels: list[str], files=()) -> int:
    """A new local ticket from the New ticket form; a start label is applied as the admin UI, so the factory trusts it.
    The files (already vetted) are stored with it in one transaction."""
    t = tracker.LocalTracker(dbm.local(cfg.db_path), tracker.UI_ACTOR)
    return t.create(repo, title, body, author=tracker.UI_ACTOR, labels=tuple(labels), files=tuple(files), limits=tracker.attach_limits(cfg))



def vet(cfg, files) -> list[tuple[str, bytes]]:
    """The uploaded (name, bytes) pairs, checked against the type and size rules and the per-ticket limits before anything is stored.
    Raises L.Refused with a message safe to show."""
    max_bytes, max_files, max_total = tracker.attach_limits(cfg)
    if len(files) > max_files:
        raise L.Refused(f"A ticket can have {max_files} attachments at most.")
    try:
        for name, data in files:
            tracker.vet_attachment(name, data, max_bytes)
    except ValueError as e:
        raise L.Refused(str(e))
    if sum(len(d) for _, d in files) > max_total:
        raise L.Refused(f"The attachments of a ticket can be {max_total // (1024 * 1024)} MB at most together.")
    return list(files)


def attach(h, form, csrf: str, files=()) -> None:
    """POST /tickets/local/attach (multipart): add files to an existing local ticket."""
    cfg = h.app.cfg()
    try:
        repo, n = _target(h, form)
        if not files:
            raise L.Refused("Choose a file to attach.")
        files = vet(cfg, files)
        store = _store(h)
        try:
            for name, data in files:
                store.add_attachment(repo, n, name, data, tracker.UI_ACTOR, tracker.attach_limits(cfg))
        except ValueError as e:
            raise L.Refused(str(e))
    except L.Refused as e:
        return L._done(h, form, csrf, str(e), "bad")
    log.info("tickets: %d attachment(s) added to local %s %s", len(files), repo, tracker.display(n))
    L._done(h, form, csrf, "Attached." if len(files) == 1 else f"Attached {len(files)} files.")


def attachment_delete(h, form, csrf: str) -> None:
    try:
        repo, n = _target(h, form)
        aid = L._number(form.get("id", ""))
    except L.Refused as e:
        return L._done(h, form, csrf, str(e), "bad")
    if _store(h).delete_attachment(repo, n, aid):
        log.info("tickets: attachment removed from local %s %s", repo, tracker.display(n))
        return L._done(h, form, csrf, "Attachment removed.")
    L._done(h, form, csrf, "That attachment does not exist.", "bad")


def download(h, q: dict, csrf: str) -> None:
    """GET /tickets/local/attachment: always a download, never shown by the page. The name and type are the ones we stored
    (from our allowlist), the CSP and nosniff headers apply as everywhere, so an uploaded file can never run on the admin origin."""
    try:
        repo, n, aid = L._repo(h.app.cfg(), q.get("repo", "")), L._number(q.get("n", "")), L._number(q.get("id", ""))
    except L.Refused:
        return h._send(404, "not found", "text/plain")
    db = h.app.ro_db()
    try:
        t = tracker.LocalTracker.__new__(tracker.LocalTracker)      # read-only: never ensure_tables on this connection
        t.db, t.actor = db, tracker.UI_ACTOR
        got = t.attachment(repo, n, aid) if tracker.is_local(n) and db is not None else None
    except sqlite3.OperationalError:
        got = None
    finally:
        if db is not None:
            db.close()
    if got is None:
        return h._send(404, "not found", "text/plain")
    meta, data = got
    h._send(200, data, meta["mime"], {"Content-Disposition": f'attachment; filename="{meta["name"]}"'})
