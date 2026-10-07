"""A ticket's split in the admin UI: the proposal a person approves or dismisses, and the linked tickets (the umbrella and its parts,
or the umbrella a ticket is part of). The UI only writes the decision (through App.decide_split); the orchestrator creates the tickets.
The start action is looked up in labels.start_options, never taken from the browser as a label."""
import sqlite3

from .. import split as S
from .. import tracker
from . import labels as L
from .views import csrf_field, esc, ticket_link


def _state(db, repo: str, n: int) -> str:
    try:
        r = db.execute("SELECT state FROM local_tickets WHERE repo=? AND number=?", (repo, int(n))).fetchone() if tracker.is_local(n) else None
    except sqlite3.Error:
        r = None
    return r[0] if r else ""


def card(cfg, db, repo: str, n: int, csrf: str, back: str) -> str:
    """Empty when the ticket has no split and is not part of one."""
    if db is None:
        return ""
    try:
        parent, part = S.for_ticket(db, repo, n), S.parent_of(db, repo, n)
    except sqlite3.Error:
        return ""
    out = ""
    if part:
        p, it = part
        out += (f'<p>Part of {ticket_link(p["repo"], p["issue"])}, item {it["pos"] + 1}'
                + (f', after {", ".join(str(int(a) + 1) for a in it["after"].split(",") if a)}' if it["after"] else "") + ".</p>")
    if parent:
        kids = S.items(db, parent["id"])
        rows = "".join(f'<li>{ticket_link(k["child_repo"], k["child_issue"]) if k["child_issue"] else esc("not created yet")}'
                       f' <span class="muted">{esc(k["title"])} ({esc(k["repo"])})'
                       + (f", {esc(_state(db, k['child_repo'], k['child_issue']))}" if k["child_issue"] and _state(db, k["child_repo"], k["child_issue"]) else "")
                       + "</span></li>" for k in kids)
        form = ""
        if parent["status"] == "proposed":
            opts = "".join(f'<option value="{esc(k)}">{esc(t)}</option>' for k, t, _ in L.start_options(cfg))
            hidden = f'{csrf_field(csrf)}<input type="hidden" name="repo" value="{esc(repo)}"><input type="hidden" name="n" value="{int(n)}">' \
                     f'<input type="hidden" name="id" value="{int(parent["id"])}"><input type="hidden" name="back" value="{esc(back)}">'
            form = (f'<form method="post" action="/tickets/split" class="field">{hidden}'
                    f'<label>Start every new ticket with<select name="start">{opts}</select></label>'
                    '<button name="decision" value="approve">Approve the split</button> '
                    '<button name="decision" value="dismiss">Dismiss</button></form>')
        head = {"proposed": "Proposed split", "approved": "Split approved (creating the tickets)", "done": "Split into"}[parent["status"]]
        out += f'<h4>{head}</h4><p class="muted">{esc(parent["reason"])}</p><ol>{rows}</ol>{form}'
    return (f'<section class="sd-card" aria-labelledby="split-h"><div class="sd-cardhead"><h3 id="split-h">Linked tickets</h3></div>{out}</section>'
            if out else "")


def post(h, form, csrf: str) -> None:
    cfg = h.app.cfg()
    try:
        repo, n = L._repo(cfg, form.get("repo", "")), L._number(form.get("n", ""))
        if not (form.get("id", "").isdigit() and len(form.get("id", "")) < 10) or form.get("decision") not in ("approve", "dismiss"):
            raise L.Refused("That is not a valid request.")
        chosen = next((o for o in L.start_options(cfg) if o[0] == (form.get("start") or "").strip()), None)
        if chosen is None:
            raise L.Refused("That start action is not available.")
    except L.Refused as e:
        return L._done(h, form, csrf, str(e), "bad")
    if h.app.decide_split(int(form["id"]), repo, n, form["decision"] == "approve", chosen[2] or ""):
        return L._done(h, form, csrf, "Splitting the ticket. The factory creates the tickets at its next poll." if form["decision"] == "approve"
                       else "Dismissed. Nothing was created.")
    L._done(h, form, csrf, "Nothing changed: that proposal was already decided or is out of date.", "bad")
