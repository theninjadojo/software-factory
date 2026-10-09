"""A ticket's split in the admin UI: the proposal a person approves or dismisses, and the linked tickets (the umbrella and its parts,
or the umbrella a ticket is part of). The UI only writes the decision (through App.decide_split); the orchestrator creates the tickets.
The start action is looked up in labels.start_options, never taken from the browser as a label. The approver may drop items (but not
edit them): only their positions come from the browser, checked here and again in split.decide."""
import sqlite3

from .. import split as S
from .. import tracker
from . import labels as L
from .views import csrf_field, esc, ticket_link

TOO_FEW = "At least two tickets must remain. Dismiss the split instead."


def _state(db, repo: str, n: int) -> str:
    try:
        r = db.execute("SELECT state FROM local_tickets WHERE repo=? AND number=?", (repo, int(n))).fetchone() if tracker.is_local(n) else None
    except sqlite3.Error:
        r = None
    return r[0] if r else ""


def _item(db, k: dict, waiting: bool) -> str:
    """One kept item; while the proposal waits, with its text and a Drop box that belongs to the approval form."""
    state = _state(db, k["child_repo"], k["child_issue"]) if k["child_issue"] else ""
    out = (f'<li>{ticket_link(k["child_repo"], k["child_issue"]) if k["child_issue"] else esc("not created yet")}'
           f' <span class="muted">{esc(k["title"])} ({esc(k["repo"])}{", " + esc(state) if state else ""})</span>')
    if waiting:
        out += (f' <label><input type="checkbox" name="drop" value="{int(k["pos"])}" form="split-f"> Drop</label>'
                f'<details><summary>What it says</summary><p class="muted">{esc(k["body"])}</p></details>')
    return out + "</li>"


def card(cfg, db, repo: str, n: int, csrf: str, back: str) -> str:
    """Empty when the ticket has no split and is not part of one. Items are numbered among the kept ones; dropped ones are listed apart."""
    if db is None:
        return ""
    try:
        parent, part = S.for_ticket(db, repo, n), S.parent_of(db, repo, n)
        siblings = S.items(db, part[0]["id"]) if part else []
        kids = S.items(db, parent["id"]) if parent else []
    except sqlite3.Error:
        return ""
    out = ""
    if part:
        p, it = part
        out += (f'<p>Part of {ticket_link(p["repo"], p["issue"])}, item {S.kept_index(siblings, it["pos"])}'
                + (f', after {", ".join(str(S.kept_index(siblings, int(a))) for a in it["after"].split(",") if a)}' if it["after"] else "") + ".</p>")
    if parent:
        waiting = parent["status"] == "proposed"
        rows = "".join(_item(db, k, waiting) for k in S.kept(kids))
        gone = [esc(k["title"]) for k in kids if k["dropped"]]
        dropped = f'<p class="muted">Dropped when it was approved: <s>{"</s>, <s>".join(gone)}</s></p>' if gone else ""
        form = ""
        if waiting:
            opts = "".join(f'<option value="{esc(k)}">{esc(t)}</option>' for k, t, _ in L.start_options(cfg))
            hidden = f'{csrf_field(csrf)}<input type="hidden" name="repo" value="{esc(repo)}"><input type="hidden" name="n" value="{int(n)}">' \
                     f'<input type="hidden" name="id" value="{int(parent["id"])}"><input type="hidden" name="back" value="{esc(back)}">'
            form = (f'<form method="post" action="/tickets/split" class="field" id="split-f">{hidden}'
                    '<p class="muted">Tick Drop on any item you do not want; at least two must remain.</p>'
                    f'<label>Start every new ticket with<select name="start">{opts}</select></label>'
                    '<button name="decision" value="approve">Approve the split</button> '
                    '<button name="decision" value="dismiss">Dismiss</button></form>')
        head = {"proposed": "Proposed split", "approved": "Split approved (creating the tickets)", "done": "Split into"}[parent["status"]]
        out += f'<h4>{head}</h4><p class="muted">{esc(parent["reason"])}</p><ol>{rows}</ol>{dropped}{form}'
    return (f'<section class="sd-card" aria-labelledby="split-h"><div class="sd-cardhead"><h3 id="split-h">Linked tickets</h3></div>{out}</section>'
            if out else "")


def _drops(h, form, pid: int) -> list[int]:
    """The positions the approver dropped: digits only, each an item of the proposal, once, leaving at least MIN_ITEMS."""
    raw = form.getall("drop")
    if not raw:
        return []
    if len(raw) > S.MAX_ITEMS or any(not (v.isdigit() and len(v) <= 2) for v in raw) or len(set(raw)) != len(raw):
        raise L.Refused("That is not a valid request.")
    drop = [int(v) for v in raw]
    db = h.app.ro_db()
    try:
        positions = {r["pos"] for r in S.items(db, pid)} if db is not None else set()
    except sqlite3.Error:
        positions = set()
    finally:
        if db is not None:
            db.close()
    if not set(drop) <= positions:
        raise L.Refused("That is not a valid request.")
    if len(positions) - len(drop) < S.MIN_ITEMS:
        raise L.Refused(TOO_FEW)
    return drop


def post(h, form, csrf: str) -> None:
    cfg = h.app.cfg()
    try:
        repo, n = L._repo(cfg, form.get("repo", "")), L._number(form.get("n", ""))
        if not (form.get("id", "").isdigit() and len(form.get("id", "")) < 10) or form.get("decision") not in ("approve", "dismiss"):
            raise L.Refused("That is not a valid request.")
        chosen = next((o for o in L.start_options(cfg) if o[0] == (form.get("start") or "").strip()), None)
        if chosen is None:
            raise L.Refused("That start action is not available.")
        drop = _drops(h, form, int(form["id"])) if form["decision"] == "approve" else []
    except L.Refused as e:
        return L._done(h, form, csrf, str(e), "bad")
    if h.app.decide_split(int(form["id"]), repo, n, form["decision"] == "approve", chosen[2] or "", drop):
        return L._done(h, form, csrf, "Splitting the ticket. The factory creates the tickets at its next poll." if form["decision"] == "approve"
                       else "Dismissed. Nothing was created.")
    L._done(h, form, csrf, "Nothing changed: that proposal was already decided or is out of date.", "bad")
