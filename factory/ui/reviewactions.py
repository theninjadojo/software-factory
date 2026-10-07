"""The review follow-ups card of a ticket: what the code reviewer proposed, and approving or dismissing it.

The agent only proposes (see factory/reviewactions.py). Here a signed-in person picks proposals; the UI writes the decision and the
orchestrator applies it (it holds the GitHub token). Every id the browser sends is checked against the database for this ticket.
Every handler runs behind the session, Host and CSRF checks of the server."""
import sqlite3
import time

from .. import reviewactions as RA
from .. import tracker
from . import labels as L
from .chat import _ticket
from .views import csrf_field, esc

KIND = {"label": "Add the label", "send-back": "Send back to the stage"}
STATE = {"proposed": "", "queued": "waiting for the factory's next poll", "applied": "done", "failed": "failed"}


def card(db, repo: str, n: int, csrf: str, back: str) -> str:
    """Empty when the reviewer proposed nothing for the ticket."""
    rows = RA.pending(db, repo, n) if db is not None else []
    if not rows:
        return ""
    hidden = f'{csrf_field(csrf)}<input type="hidden" name="repo" value="{esc(repo)}"><input type="hidden" name="n" value="{int(n)}">' \
             f'<input type="hidden" name="back" value="{esc(back)}">'
    body = "".join(
        "<li>" + (f'<label><input type="checkbox" name="id" value="{int(r["id"])}"> ' if r["status"] == "proposed" else "<label>")
        + f'{esc(KIND[r["kind"]])} <b>{esc(r["value"])}</b>' + (f': {esc(r["reason"])}' if r["reason"] else "") + "</label>"
        + (f' <span class="muted">{esc(STATE[r["status"]])}{(" · " + esc(r["detail"])) if r["detail"] else ""}</span>' if STATE[r["status"]] or r["detail"] else "")
        + "</li>" for r in rows)
    acts = ""
    if any(r["status"] == "proposed" for r in rows):
        acts = ('<div class="sd-acts"><button type="submit">Apply selected</button> '
                '<button type="submit" class="secondary" formaction="/ticket/review-actions/reject">Dismiss selected</button></div>')
    return ('<section class="sd-card" aria-labelledby="ra-h"><div class="sd-cardhead"><h3 id="ra-h">Review follow-ups</h3>'
            '<span class="muted">Proposed by the code reviewer. Nothing happens until you apply it.</span></div>'
            f'<form method="post" action="/ticket/review-actions/apply">{hidden}<ul>{body}</ul>{acts}</form></section>')


def _decide(h, form, csrf: str, accept: bool) -> None:
    try:
        repo, n = _ticket(h, form)
        ids = [int(v) for v in form.getall("id") if v.isdigit() and len(v) <= 9]
        if not ids:
            raise L.Refused("Select at least one follow-up.")
        if len(ids) > tracker.MAX_BULK:
            raise L.Refused(f"Select at most {tracker.MAX_BULK} at a time. Nothing was changed.")
        db = sqlite3.connect(h.app.cfg().db_path, timeout=10)
        try:
            done = RA.decide(db, repo, n, ids, accept, time.time())
        finally:
            db.close()
    except L.Refused as e:
        return L._done(h, form, csrf, str(e), "bad")
    if not done:
        return L._done(h, form, csrf, "Nothing changed: those follow-ups were already decided.", "bad")
    L._done(h, form, csrf, f"Queued {done}: the factory applies it at its next poll." if accept else f"Dismissed {done}.")


def apply_post(h, form, csrf: str) -> None:
    _decide(h, form, csrf, True)


def reject_post(h, form, csrf: str) -> None:
    _decide(h, form, csrf, False)
