"""A ticket's split in the admin UI: the proposal a person approves or dismisses, and the linked tickets (the umbrella and its parts,
or the umbrella a ticket is part of). The UI only writes the decision (through App.decide_split); the orchestrator creates the tickets.
The start action is looked up in labels.start_options, never taken from the browser as a label. The approver may drop items, merge
some into one ticket (and give it a title), or keep the ticket whole: only positions and a merged ticket's title come from the browser,
checked here and again in split.py."""
import sqlite3

from .. import split as S
from .. import tracker
from . import labels as L, views
from .views import csrf_field, esc, ticket_link

TOO_FEW = "At least two tickets must remain. Choose Work on it as one ticket instead."
INVALID = "That is not a valid request."
SOURCE = {"analyst": "Proposed by the analyst.", "architect": "Proposed by the architect: its plan is large, so it suggests building it in phases.",
          "review": "Proposed by the ticket review."}


def _state(db, repo: str, n: int) -> str:
    try:
        r = db.execute("SELECT state FROM local_tickets WHERE repo=? AND number=?", (repo, int(n))).fetchone() if tracker.is_local(n) else None
    except sqlite3.Error:
        r = None
    return r[0] if r else ""


def _item(db, k: dict, rows: list[dict]) -> str:
    """One kept item of an approved split, the items it comes after, and the parts merged into it."""
    state = _state(db, k["child_repo"], k["child_issue"]) if k["child_issue"] else ""
    after = ", ".join(str(S.kept_index(rows, int(a))) for a in k["after"].split(",") if a)
    parts = [r["title"] for r in rows if r["merged_into"] == k["pos"]]
    return (f'<li>{ticket_link(k["child_repo"], k["child_issue"]) if k["child_issue"] else esc("not created yet")}'
            f' <span class="muted">{esc(k["title"])} ({esc(k["repo"])}{", after " + esc(after) if after else ""}'
            f'{", " + esc(state) if state else ""})</span>'
            + (f'<br><span class="badge warn">Merged from {len(parts)} parts</span>' if parts else "") + "</li>")


def _part(i: int, t: dict, by: dict, hidden: str) -> str:
    """One ticket a waiting proposal would make, numbered as it would be created. A single part has a Drop box; a merged one has
    its title to edit and a Split it again button. Every part has a Merge box. The boxes and the title belong to the approval form."""
    head, merged = t["members"][0], len(t["members"]) > 1
    after = ", ".join(str(a + 1) for a in t["after"])
    pick = (f'<label class="check"><input type="checkbox" name="pick" value="{int(head)}" form="split-f"> Merge</label>')
    if merged:
        title = (f'<label class="sp-title">Title of the merged ticket<input name="title-{int(head)}" form="split-f" maxlength="{S.MAX_TITLE}"'
                 f' value="{esc(t["title"])}"></label>')
        extra = (f'<span class="badge warn">Merged from {len(t["members"])} parts</span>'
                 f'<form method="post" action="/tickets/split" class="inline">{hidden}'
                 f'<button class="link" name="unmerge" value="{int(head)}">Split it again</button></form>')
        what = "".join(f'<li>{esc(by[m]["title"])}</li>' for m in t["members"])
        what = f'<ul class="muted">{what}</ul>'
    else:
        title = f'<b>{esc(t["title"])}</b>'
        extra = f'<label class="check"><input type="checkbox" name="drop" value="{int(head)}" form="split-f"> Drop</label>'
        what = f'<p class="muted">{esc(t["body"])}</p>'
    return (f'<li class="sp-part"><div class="sp-head"><span class="sp-n" aria-hidden="true">{i + 1}</span>{title}</div>'
            f'<div class="sp-meta muted">{esc(t["repo"])}, {"after " + esc(after) if after else "can start first"}</div>'
            f'<div class="sp-acts">{pick}{extra}</div><details><summary>What it says</summary>{what}</details></li>')


def card(cfg, db, repo: str, n: int, csrf: str, back: str) -> str:
    """Empty when the ticket has no split and is not part of one. Items are numbered among the kept ones; dropped ones are listed apart."""
    if db is None:
        return ""
    try:
        parent, part = S.for_ticket(db, repo, n, ("proposed", "approved", "done", "keeping", "kept")), S.parent_of(db, repo, n)
        siblings = S.items(db, part[0]["id"]) if part else []
        kids = S.items(db, parent["id"]) if parent else []
    except sqlite3.Error:
        return ""
    out = ""
    if part:
        p, it = part
        out += (f'<p>Part of {ticket_link(p["repo"], p["issue"])}, item {S.kept_index(siblings, it["pos"])}'
                + (f', after {", ".join(str(S.kept_index(siblings, int(a))) for a in it["after"].split(",") if a)}' if it["after"] else "") + ".</p>")
    if parent and parent["status"] == "proposed":
        out += _waiting(cfg, parent, kids, repo, n, csrf, back)
    elif parent and parent["status"] in ("keeping", "kept"):
        listed = "".join(f'<li>{esc(k["title"])}</li>' for k in kids)
        out += ('<h4>Kept as one ticket' + (" (the factory starts it at its next poll)" if parent["status"] == "keeping" else "") + '</h4>'
                f'<p class="muted">The proposed split was not used. No split is proposed again unless the ticket is edited.</p>'
                f'<details><summary>The {len(kids)} proposed parts</summary><ol class="muted">{listed}</ol></details>')
    elif parent:
        rows = "".join(_item(db, k, kids) for k in S.kept(kids))
        gone = [esc(k["title"]) for k in kids if k["dropped"] and k["merged_into"] is None]
        dropped = f'<p class="muted">Dropped when it was approved: <s>{"</s>, <s>".join(gone)}</s></p>' if gone else ""
        head = {"approved": "Split approved (creating the tickets)", "done": "Split into"}[parent["status"]]
        out += f'<h4>{head}</h4><p class="muted">{esc(parent["reason"])}</p><ol>{rows}</ol>{dropped}'
    return (f'<section class="sd-card" aria-labelledby="split-h"><div class="sd-cardhead"><h3 id="split-h">Linked tickets</h3></div>{out}</section>'
            if out else "")


def _waiting(cfg, parent: dict, kids: list[dict], repo: str, n: int, csrf: str, back: str) -> str:
    """A proposal waiting for a person: the tickets it would make (with the person's merges), and the decision form."""
    try:
        tickets = S.plan(kids, (), S.merges(parent))
    except ValueError:                                  # merges stored by an older version that no longer fit: show the proposal
        tickets = S.plan(kids)
    by = {k["pos"]: k for k in kids}
    hidden = (f'{csrf_field(csrf)}<input type="hidden" name="repo" value="{esc(repo)}"><input type="hidden" name="n" value="{int(n)}">'
              f'<input type="hidden" name="id" value="{int(parent["id"])}"><input type="hidden" name="back" value="{esc(back)}">')
    parts = "".join(_part(i, t, by, hidden) for i, t in enumerate(tickets))
    opts = "".join(f'<option value="{esc(k)}">{esc(t)}</option>' for k, t, _ in L.start_options(cfg))
    merged = len(tickets) < len(kids)
    form = (f'<form method="post" action="/tickets/split" id="split-f" class="sp-form">{hidden}'
            '<button type="submit" disabled hidden aria-hidden="true"></button>'          # Enter in a title field submits nothing
            '<div class="sp-row"><button class="secondary" name="decision" value="merge">Merge ticked</button>'
            '<p class="muted">Tick Merge on two or more parts to make them one ticket, or Drop on a part you do not want. Each new '
            'ticket is blocked by the ones it comes after.</p></div>'
            f'<div class="sp-row"><label>Start with<select name="start">{opts}</select></label>'
            f'<button name="decision" value="approve">Create {len(tickets)} tickets</button>'
            '<button class="secondary" name="decision" value="keep">Work on it as one ticket</button>'
            '<button class="secondary" name="decision" value="dismiss">Dismiss</button></div></form>')
    source = f'<p class="muted">{esc(SOURCE[parent["source"]])}</p>' if parent.get("source") in SOURCE else ""
    note = f'<p class="muted">With your merges: {len(kids)} proposed parts make {len(tickets)} tickets.</p>' if merged else ""
    return (f'<h4>Proposed split</h4>{source}<p class="muted">{esc(parent["reason"])}</p>{note}'
            f'<ol class="sp-parts">{parts}</ol>{form}')


def fold(cfg, db, repo: str, n: int, csrf: str, back: str) -> str:
    """The card folded on the ticket page; open, and saying so, while a proposed split waits for a person's decision."""
    body = card(cfg, db, repo, n, csrf, back)
    waiting = 'id="split-f"' in body
    return views.fold("linked", "Linked tickets", "A proposed split is waiting for your decision" if waiting else "Split from or into other tickets",
                      body, tone="fd-ask" if waiting else "", open_=waiting)


def _positions(form, name: str) -> list[int]:
    """Positions ticked in the form: digits only, each once, at most MAX_ITEMS."""
    raw = form.getall(name)
    if len(raw) > S.MAX_ITEMS or any(not (v.isdigit() and len(v) <= 2) for v in raw) or len(set(raw)) != len(raw):
        raise L.Refused(INVALID)
    return [int(v) for v in raw]


def _titles(form) -> dict[int, str]:
    """Merged tickets' titles, by their first position (split.decide makes them one clean line)."""
    out = {}
    for key in ("title-%d" % i for i in range(S.MAX_ITEMS)):
        v = form.get(key)
        if v is not None:
            if len(v) > 4 * S.MAX_TITLE:
                raise L.Refused(INVALID)
            out[int(key[6:])] = v
    return out


def _drops(h, form, pid: int) -> list[int]:
    """The positions the approver dropped: digits only, each an item of the proposal, once, leaving at least MIN_ITEMS."""
    drop = _positions(form, "drop")
    if not drop:
        return []
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
        if not (form.get("id", "").isdigit() and len(form.get("id", "")) < 10):
            raise L.Refused(INVALID)
        pid, decision, head = int(form["id"]), form.get("decision"), form.get("unmerge")
        if head is not None:                            # Split it again: undo one merge
            if not (head.isdigit() and len(head) <= 2):
                raise L.Refused(INVALID)
            why = h.app.merge_split(pid, repo, n, None, int(head))
            return L._done(h, form, csrf, why or "Split again into its parts.", "bad" if why else "ok")
        if decision == "merge":
            why = h.app.merge_split(pid, repo, n, _positions(form, "pick"))
            return L._done(h, form, csrf, why or "Merged into one ticket. Check its title, then create the tickets.", "bad" if why else "ok")
        if decision not in ("approve", "dismiss", "keep"):
            raise L.Refused(INVALID)
        chosen = next((o for o in L.start_options(cfg) if o[0] == (form.get("start") or "").strip()), None)
        if chosen is None:
            raise L.Refused("That start action is not available.")
        drop = _drops(h, form, pid) if decision == "approve" else []
        titles = _titles(form) if decision == "approve" else {}
    except L.Refused as e:
        return L._done(h, form, csrf, str(e), "bad")
    if h.app.decide_split(pid, repo, n, decision == "approve", chosen[2] or "", drop, titles, decision == "keep"):
        return L._done(h, form, csrf, {"approve": "Splitting the ticket. The factory creates the tickets at its next poll.",
                                       "keep": "Working on it as one ticket. The factory "
                                               + ("starts it" if chosen[2] else "takes the split label off") + " at its next poll.",
                                       "dismiss": "Dismissed. Nothing was created."}[decision])
    L._done(h, form, csrf, "Nothing changed: that proposal was already decided or is out of date.", "bad")
