"""Splitting a large ticket into smaller linked tickets.

The analyst may end its document with a `factory-split` block when the ticket is too large for one pull request, and so may the
architect when its plan is large (`split.architect`): its block also carries the plan's size, `{"areas": n, "items": m}`, and is kept
only when that reaches `split.architect_min_areas` or `split.architect_min_items` (see `sized`). The block is derived from untrusted
ticket text, so it is only data, validated against fixed sets and length caps:

* 2 to MAX_ITEMS items (more than that rejects the whole block, the list is never cut);
* per item a one-line title and a body (both passed through sanitize_markdown), a repository from the parent's project, and `after`,
  the positions of earlier items it waits for (earlier only, so there are no cycles);
* anything else, and a malformed block, means no proposal: nothing changes.

A proposal is stored (`split_proposals`, `split_items`) and nothing is created until a person approves it in the admin UI or the
chat buttons (`split:<id>`). On approval the children are created one at a time, each step recorded so a retry or a restart never
creates one twice. In the admin UI the approver may also drop items (flagged `dropped`, never deleted; an item that came after a
dropped one inherits what that one came after). Children carry no label unless the approver chose one start action; the factory database is the record of which
ticket belongs to which (so it works for local tickets too). A child names the earlier items of its own repository on a
`Blocked by #N` line (which the project manager honours), and `blockers` keeps a later child's build waiting for the items it
comes after. The parent stays open as an umbrella and `sweep` closes it once every child is closed. main.py decides when these run.

While a proposal waits, a person may merge some of its parts into one ticket (`merge`, undone by `unmerge`; only positions come from
the browser) and approve the rest as they are, or keep the ticket whole: the split is then not used, the parent is started with the
chosen action, and no split is proposed again for the same ticket text (`declined`)."""
import hashlib
import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass

from . import tracker
from .sanitize import sanitize_markdown

log = logging.getLogger("factory.split")
BLOCK = re.compile(r"^```factory-split[ \t]*\n(.*?)^```[ \t]*$", re.M | re.S)
MAX_ITEMS, MIN_ITEMS = 10, 2
MAX_TITLE, MAX_BODY, MAX_REASON = 120, 4000, 300
MAX_TRIES, SWEEP_LIMIT = 3, 20
MAX_SIZE = 1000
SOURCES = ("", "analyst", "architect", "review")
PROPOSED_LABEL, UMBRELLA_LABEL = "factory:split-proposed", "factory:umbrella"

PROPOSAL = "\n\n### Proposed split\n\n{reason}\n\n{items}\n\nNothing is created until a person approves it (in the factory UI or the factory's chat message)."
CHILD_HEAD = "Part of {parent} (item {pos} of {total}).\n\n"
DONE = "The factory split this ticket, as a person approved. It stays open and closes when every ticket below is closed.\n\n{items}"
CLOSED = "Every ticket this one was split into is closed, so the factory closed it."
KEPT = ("A person chose to work on this ticket as one, so the proposed split was not used. The factory does not propose a split again "
        "unless the ticket is edited.")


@dataclass(frozen=True)
class Item:
    title: str
    body: str
    repo: str
    after: tuple = ()


@dataclass(frozen=True)
class Proposal:
    reason: str
    items: tuple


def _line(v, limit: int) -> str:
    return " ".join(v.split())[:limit] if isinstance(v, str) else ""


def _repo(value, allowed) -> str | None:
    """A configured repository: its full name, or the directory name the agent saw when that names exactly one of them."""
    if not isinstance(value, str):
        return None
    if value in allowed:
        return value
    hit = [r for r in allowed if r.split("/")[-1] == value]
    return hit[0] if len(hit) == 1 else None


def parse(text: str, allowed) -> Proposal | None:
    """The validated last `factory-split` block, or None when there is none or anything in it is invalid. `allowed`: the repositories
    a child may be filed in."""
    blocks = BLOCK.findall(text or "")
    if not blocks:
        return None
    try:
        d = json.loads(blocks[-1])
    except ValueError:
        return None
    return validate(d, allowed)


def validate(d, allowed) -> Proposal | None:
    """One decoded split ({"reason", "items"}), checked as `parse` checks a block; the ticket review proposes splits in this shape."""
    raw = d.get("items") if isinstance(d, dict) else None
    if not isinstance(raw, list) or not MIN_ITEMS <= len(raw) <= MAX_ITEMS:
        return None
    items = []
    for pos, r in enumerate(raw):
        if not isinstance(r, dict):
            return None
        title = sanitize_markdown(_line(r.get("title"), MAX_TITLE), MAX_TITLE + 20)
        body, repo, after = r.get("body"), _repo(r.get("repo"), allowed), r.get("after", [])
        if (not title or not isinstance(body, str) or not body.strip() or repo is None or not isinstance(after, list)
                or any(not isinstance(a, int) or isinstance(a, bool) or not 0 <= a < pos for a in after)):
            return None
        items.append(Item(title, sanitize_markdown(body.strip()[:MAX_BODY], MAX_BODY + 50), repo, tuple(sorted(set(after)))))
    return Proposal(sanitize_markdown(_line(d.get("reason"), MAX_REASON), MAX_REASON + 20), tuple(items))


def extract(text: str, allowed) -> tuple[str, Proposal | None]:
    """The text without any `factory-split` block (valid or not, so agent JSON never reaches the ticket), and the validated proposal."""
    return BLOCK.sub("", text or "").rstrip() + "\n", parse(text, allowed)


def size(text: str) -> tuple[int, int] | None:
    """(areas, plan items) the architect gave in its last `factory-split` block; None when it gave none or anything is off."""
    blocks = BLOCK.findall(text or "")
    try:
        d = json.loads(blocks[-1]) if blocks else None
    except ValueError:
        return None
    got = d.get("size") if isinstance(d, dict) else None
    vals = (got.get("areas"), got.get("items")) if isinstance(got, dict) else (None, None)
    if any(not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= MAX_SIZE for v in vals):
        return None
    return vals


def sized(text: str, allowed, min_areas: int, min_items: int) -> tuple[str, Proposal | None, tuple[int, int] | None]:
    """The architect's document without its block, the proposal only when the plan it reports is large enough (at least min_areas
    areas or min_items plan items; a block without a valid size is no proposal), and that size."""
    out, p = extract(text, allowed)
    got = size(text)
    if p is None or got is None or (got[0] < min_areas and got[1] < min_items):
        return out, None, got
    return out, p, got


def section(p: Proposal, plan_size: tuple[int, int] | None = None) -> str:
    """A readable list for the stage comment, built from the validated values only (plan_size: the architect's areas and items)."""
    lines = [f"{i + 1}. **{it.title}** ({it.repo})" + (f", after {', '.join(str(a + 1) for a in it.after)}" if it.after else "")
             for i, it in enumerate(p.items)]
    reason = p.reason or "The ticket is larger than one pull request."
    if plan_size:
        reason += f" (The plan touches {plan_size[0]} areas and has {plan_size[1]} plan items.)"
    return PROPOSAL.format(reason=reason, items="\n".join(lines))


def fingerprint(issue: dict) -> str:
    """Of the parent's title and body: a proposal made for other text is stale."""
    return hashlib.sha256(f"{issue.get('title') or ''}\0{issue.get('body') or ''}".encode()).hexdigest()


def ensure_tables(db) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS split_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, repo TEXT NOT NULL, issue INTEGER NOT NULL, run_id INTEGER,
            reason TEXT NOT NULL DEFAULT '', snapshot TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'proposed',   -- proposed, approved, done, dismissed, keeping, kept, stale, failed
            source TEXT NOT NULL DEFAULT '',           -- who proposed it: analyst, architect, review ('' = before this was kept)
            start_action TEXT NOT NULL DEFAULT '',     -- the one label the approver chose for every child ('' = none)
            fails INTEGER NOT NULL DEFAULT 0, detail TEXT NOT NULL DEFAULT '', umbrella_closed REAL,
            created REAL NOT NULL, updated REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS split_proposals_ticket ON split_proposals (repo, issue, status);
        CREATE TABLE IF NOT EXISTS split_items (
            proposal_id INTEGER NOT NULL, pos INTEGER NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, repo TEXT NOT NULL,
            after TEXT NOT NULL DEFAULT '',            -- comma-separated earlier positions
            status TEXT NOT NULL DEFAULT 'new',        -- new, creating, created
            child_repo TEXT, child_issue INTEGER, PRIMARY KEY (proposal_id, pos));
        CREATE INDEX IF NOT EXISTS split_items_child ON split_items (child_repo, child_issue);
    """)
    if "dropped" not in {r[1] for r in db.execute("PRAGMA table_info(split_items)")}:   # 1 = the approver left it out; older databases get it here
        db.execute("ALTER TABLE split_items ADD COLUMN dropped INTEGER NOT NULL DEFAULT 0")
    if "merged_into" not in {r[1] for r in db.execute("PRAGMA table_info(split_items)")}:   # the ticket a merged part became (pos)
        db.execute("ALTER TABLE split_items ADD COLUMN merged_into INTEGER")
    cols = {r[1] for r in db.execute("PRAGMA table_info(split_proposals)")}
    if "source" not in cols:
        db.execute("ALTER TABLE split_proposals ADD COLUMN source TEXT NOT NULL DEFAULT ''")
    if "merges" not in cols:                                # JSON lists of positions a person merged while it waits
        db.execute("ALTER TABLE split_proposals ADD COLUMN merges TEXT NOT NULL DEFAULT '[]'")
    db.commit()


def store(db, repo: str, issue: dict, p: Proposal, run_id=None, source: str = "") -> int:
    """Keep a new proposal for a ticket; an earlier one still waiting for a person is superseded. source: one of SOURCES."""
    now = time.time()
    db.execute("UPDATE split_proposals SET status='stale', updated=? WHERE repo=? AND issue=? AND status='proposed'", (now, repo, issue["number"]))
    pid = db.execute("INSERT INTO split_proposals (repo, issue, run_id, reason, snapshot, source, created, updated) VALUES (?,?,?,?,?,?,?,?)",
                     (repo, issue["number"], run_id, p.reason, fingerprint(issue), source if source in SOURCES else "", now, now)).lastrowid
    db.executemany("INSERT INTO split_items (proposal_id, pos, title, body, repo, after) VALUES (?,?,?,?,?,?)",
                   [(pid, i, it.title, it.body, it.repo, ",".join(map(str, it.after))) for i, it in enumerate(p.items)])
    db.commit()
    return pid


PCOLS = ("id", "repo", "issue", "reason", "snapshot", "status", "start_action", "fails", "detail", "umbrella_closed", "source", "merges")


def get(db, pid: int) -> dict | None:
    r = db.execute(f"SELECT {', '.join(PCOLS)} FROM split_proposals WHERE id=?", (int(pid),)).fetchone()
    return dict(zip(PCOLS, r)) if r else None


def items(db, pid: int) -> list[dict]:
    return [dict(zip(("pos", "title", "body", "repo", "after", "status", "child_repo", "child_issue", "dropped", "merged_into"), r)) for r in db.execute(
        "SELECT pos, title, body, repo, after, status, child_repo, child_issue, dropped, merged_into FROM split_items WHERE proposal_id=? ORDER BY pos",
        (int(pid),))]


def kept(rows: list[dict]) -> list[dict]:
    """The items that become tickets (the approver did not drop them), in order."""
    return [r for r in rows if not r["dropped"]]


def kept_index(rows: list[dict], pos: int) -> int:
    """An item's number among the kept items, counting from 1 (what its ticket says)."""
    return next((i for i, r in enumerate(kept(rows), 1) if r["pos"] == pos), pos + 1)


def for_ticket(db, repo: str, issue: int, statuses=("proposed", "approved", "done")) -> dict | None:
    """The newest proposal of a ticket in one of the statuses."""
    marks = ",".join("?" * len(statuses))
    r = db.execute(f"SELECT id FROM split_proposals WHERE repo=? AND issue=? AND status IN ({marks}) ORDER BY id DESC LIMIT 1",
                   (repo, int(issue), *statuses)).fetchone()
    return get(db, r[0]) if r else None


def parent_of(db, repo: str, issue: int) -> tuple[dict, dict] | None:
    """(proposal, item) of the split a ticket was created by; None when it is not a child."""
    r = db.execute("SELECT proposal_id, pos FROM split_items WHERE child_repo=? AND child_issue=?", (repo, int(issue))).fetchone()
    p = get(db, r[0]) if r else None
    return (p, next(i for i in items(db, p["id"]) if i["pos"] == r[1])) if p else None


def rewire(rows: list[dict], drop) -> dict[int, str]:
    """The `after` of every kept item once the dropped ones are gone: an item that came after a dropped one comes after what that one
    came after (through any chain of dropped items). Positions are kept, so `after` still points only at earlier items."""
    gone, eff, out = set(drop), {}, {}
    for r in rows:
        deps = set()
        for a in (int(x) for x in r["after"].split(",") if x):
            deps |= eff.get(a, set()) if a in gone else {a}
        eff[r["pos"]] = deps
        if r["pos"] not in gone:
            out[r["pos"]] = ",".join(map(str, sorted(deps)))
    return out


def merges(p: dict) -> list[list[int]]:
    """The groups of positions a person merged while the proposal waits, each sorted."""
    try:
        d = json.loads(p.get("merges") or "[]")
    except ValueError:
        return []
    return [sorted(g) for g in d if isinstance(g, list) and len(g) > 1
            and all(isinstance(x, int) and not isinstance(x, bool) for x in g)] if isinstance(d, list) else []


def _joined(titles: list[str]) -> str:
    """One title for merged parts: "A, b and c" (a word in capitals keeps them)."""
    t = [titles[0]] + [x[:1].lower() + x[1:] if x[1:2].islower() else x for x in titles[1:]]
    return (t[0] if len(t) == 1 else ", ".join(t[:-1]) + " and " + t[-1])[:MAX_TITLE]


def plan(rows: list[dict], drop=(), merged=()) -> list[dict]:
    """The tickets an approval makes, in the order they are created: {"members": positions, "title", "body", "repo", "after": indexes
    of earlier tickets}. A dropped item is left out (what came after it comes after what it came after); merged items become one
    ticket that comes after everything its parts came after. ValueError when merged parts would wait for each other."""
    by, after = {r["pos"]: r for r in rows}, rewire(rows, drop)
    head = {p: p for p in after}
    for g in merged:
        for p in g:
            if p in head:
                head[p] = min(g)
    members: dict[int, list[int]] = {}
    for p in sorted(after):
        members.setdefault(head[p], []).append(p)
    deps = {h: {head[int(a)] for p in ms for a in after[p].split(",") if a} - {h} for h, ms in members.items()}
    order: list[int] = []
    while len(order) < len(members):
        nxt = next((h for h in sorted(members) if h not in order and deps[h] <= set(order)), None)
        if nxt is None:
            raise ValueError("merged parts would wait for each other")
        order.append(nxt)
    idx = {h: i for i, h in enumerate(order)}
    out = []
    for h in order:
        ms = members[h]
        first = by[ms[0]]
        out.append({"members": ms, "repo": first["repo"], "after": sorted(idx[d] for d in deps[h]),
                    "title": first["title"] if len(ms) == 1 else _joined([by[p]["title"] for p in ms]),
                    "body": first["body"] if len(ms) == 1 else "\n\n".join(f"### {by[p]['title']}\n\n{by[p]['body']}" for p in ms)})
    return out


def _waiting(db, pid: int, repo: str, issue: int) -> dict | None:
    p = get(db, pid)
    return p if p and p["repo"] == repo and p["issue"] == int(issue) and p["status"] == "proposed" else None


def _between(rows: list[dict], merged: list, picked: set) -> list[int]:
    """The tickets (numbered as the card shows them) that have to happen between the picked ones, so they cannot be merged."""
    tickets = plan(rows, (), merged)
    sel = {i for i, t in enumerate(tickets) if picked & set(t["members"])}
    reach = []
    for t in tickets:                                   # tickets are in order, so every `after` is already reached
        r = set(t["after"])
        for a in t["after"]:
            r |= reach[a]
        reach.append(r)
    return [i + 1 for i in range(len(tickets)) if i not in sel and reach[i] & sel and any(i in reach[s] for s in sel)]


def merge(db, pid: int, repo: str, issue: int, positions) -> str:
    """Merge parts of a waiting proposal into one ticket. A part of an earlier merge brings that whole merge along. '' when done,
    else why not (safe to show)."""
    p = _waiting(db, pid, repo, issue)
    if p is None:
        return "Nothing changed: that proposal was already decided or is out of date."
    rows = items(db, pid)
    known = {r["pos"]: r for r in rows}
    if (len(positions) < 2 or any(not isinstance(x, int) or isinstance(x, bool) for x in positions)
            or len(set(positions)) != len(positions) or not set(positions) <= set(known)):
        return "Tick two or more parts to merge them."
    old, picked = merges(p), set(positions)
    new = set(picked)
    for g in old:
        if new & set(g):
            new |= set(g)
    rest = [g for g in old if not new & set(g)] + [sorted(new)]
    if len({known[x]["repo"] for x in new}) > 1:
        return "Only parts for the same repository can be merged."
    try:
        left = len(plan(rows, (), rest))
    except ValueError:
        between = _between(rows, old, picked)
        return (f"Part {', '.join(map(str, between))} has to happen between them. Tick it too, or merge other parts." if between
                else "Those parts would wait for each other.")
    if left < MIN_ITEMS:
        return "That is every part. Choose Work on it as one ticket instead."
    db.execute("UPDATE split_proposals SET merges=?, updated=? WHERE id=?", (json.dumps(rest), time.time(), int(pid)))
    db.commit()
    return ""


def unmerge(db, pid: int, repo: str, issue: int, head: int) -> bool:
    """Undo the merge whose first position is `head`, while the proposal waits."""
    p = _waiting(db, pid, repo, issue)
    old = merges(p) if p else []
    rest = [g for g in old if g[0] != head]
    if len(rest) == len(old):
        return False
    db.execute("UPDATE split_proposals SET merges=?, updated=? WHERE id=?", (json.dumps(rest), time.time(), int(pid)))
    db.commit()
    return True


def declined(db, repo: str, issue: dict) -> bool:
    """A person chose to keep this ticket whole, as its text still is: no split is proposed again."""
    return any(r[0] == fingerprint(issue) for r in db.execute(
        "SELECT snapshot FROM split_proposals WHERE repo=? AND issue=? AND status IN ('keeping','kept')", (repo, int(issue["number"]))))


def _regroup(db, pid: int, rows: list[dict], tickets: list[dict], drop, titles: dict) -> None:
    """Write the approved tickets as the proposal's items, in creation order; the dropped and the merged parts follow, flagged (a
    merged part names the ticket it became). Nothing has been created yet, so the positions can be renumbered."""
    into = {m: i for i, t in enumerate(tickets) for m in t["members"] if len(t["members"]) > 1}
    new = []
    for i, t in enumerate(tickets):
        title = t["title"]
        if len(t["members"]) > 1 and (own := _line(titles.get(t["members"][0]), MAX_TITLE)):
            title = sanitize_markdown(own, MAX_TITLE + 20)
        new.append((int(pid), i, title, t["body"], t["repo"], ",".join(map(str, t["after"])), 0, None))
    for r in rows:
        if r["pos"] in drop or r["pos"] in into:
            new.append((int(pid), len(new), r["title"], r["body"], r["repo"], "", 1, into.get(r["pos"])))
    db.execute("DELETE FROM split_items WHERE proposal_id=?", (int(pid),))
    db.executemany("INSERT INTO split_items (proposal_id, pos, title, body, repo, after, dropped, merged_into) VALUES (?,?,?,?,?,?,?,?)", new)


def decide(db, pid: int, repo: str, issue: int, accept: bool, start_action: str = "", allowed_starts=(), drop=(), titles=None,
           keep: bool = False) -> bool:
    """A person's decision. Only a proposal of this very ticket that still waits; the start action must be one the caller allows.
    drop: positions the approver leaves out (approve only); each must be an item of the proposal, once, not merged, and at least
    MIN_ITEMS tickets must remain. The dropped items are flagged, not deleted, and the kept items' `after` is rewired in the same
    transaction; with merged parts the items are rewritten as `_regroup` says (titles: a merged ticket's own title, by its first
    position). keep: the ticket is worked on as one (the start action goes on the ticket itself). False when nothing changed."""
    p = _waiting(db, pid, repo, issue)
    if p is None:
        return False
    if start_action and start_action not in allowed_starts:
        return False
    accept = accept and not keep
    drop = list(drop) if accept else []
    rows = items(db, pid)
    groups = merges(p) if accept else []
    if (any(not isinstance(d, int) or isinstance(d, bool) for d in drop) or len(set(drop)) != len(drop)
            or not set(drop) <= {r["pos"] for r in rows} or len(rows) - len(drop) < MIN_ITEMS
            or set(drop) & {x for g in groups for x in g}):
        return False
    try:
        tickets = plan(rows, drop, groups) if groups else None
    except ValueError:
        return False
    if tickets is not None and len(tickets) < MIN_ITEMS:
        return False
    status = "keeping" if keep else "approved" if accept else "dismissed"
    try:
        cur = db.execute("UPDATE split_proposals SET status=?, start_action=?, updated=? WHERE id=? AND status='proposed'",
                         (status, start_action if accept or keep else "", time.time(), int(pid)))
        if cur.rowcount != 1:
            db.rollback()
            return False
        if tickets is not None:
            _regroup(db, pid, rows, tickets, set(drop), titles or {})
        elif drop:
            db.executemany("UPDATE split_items SET dropped=1 WHERE proposal_id=? AND pos=?", [(int(pid), d) for d in drop])
            db.executemany("UPDATE split_items SET after=? WHERE proposal_id=? AND pos=?",
                           [(after, int(pid), pos) for pos, after in rewire(rows, drop).items()])
        db.commit()
    except sqlite3.Error:
        db.rollback()
        raise
    return True


def _set(db, pid: int, status: str, detail: str = "", fails: int | None = None) -> None:
    db.execute("UPDATE split_proposals SET status=?, detail=?, fails=COALESCE(?, fails), updated=? WHERE id=?",
               (status, detail[:300], fails, time.time(), int(pid)))
    db.commit()


def _shown(repo: str, num: int) -> str:
    return tracker.ref(repo, num)


def _create(gh, conn, p: dict, it: dict, made: dict, index: int, total: int, github_ok: bool) -> tuple[int, str]:
    """The child ticket of one item (number `index` of the `total` kept items): local when the parent is local, otherwise a GitHub
    issue. Returns (number, id on GitHub or '')."""
    marker = f"<!-- factory:split={p['id']}.{it['pos']} -->"
    after = [made[a] for a in map(int, filter(None, it["after"].split(",")))]
    body = CHILD_HEAD.format(parent=_shown(p["repo"], p["issue"]), pos=index, total=total) + order_lines(it["repo"], after) + it["body"] + "\n\n" + marker
    if tracker.is_local(p["issue"]):
        return tracker.LocalTracker(conn).create(it["repo"], it["title"], body, author=tracker.FACTORY_ACTOR), ""
    if not github_ok:
        raise RuntimeError("GitHub issues are switched off")
    made_issue = gh.create_followup(it["repo"], it["title"], body)
    return int(made_issue["number"]), made_issue.get("id")


def order_lines(repo: str, after: list[tuple[str, int]]) -> str:
    """What a child waits for: `Blocked by #N` (or `L-N`) for earlier tickets of its own repository, which the project manager reads
    (pm.explicit_blockers), and a plain `Starts after` for those of other repositories. Built from recorded numbers only."""
    same = [tracker.display(n) for r, n in after if r == repo]
    other = [_shown(r, n) for r, n in after if r != repo]
    return ((f"Blocked by {', '.join(same)}\n\n" if same else "")
            + (f"Starts after: {', '.join(other)}.\n\n" if other else ""))


def _found_local(conn, it: dict, pid: int) -> int | None:
    """A local child created just before a crash (its row was still 'creating')."""
    r = conn.execute("SELECT number FROM local_tickets WHERE repo=? AND body LIKE ?", (it["repo"], f"%factory:split={pid}.{it['pos']} -->%")).fetchone()
    return r[0] if r else None


def apply_one(github_ok: bool, gh, conn, p: dict, start_labels, emit, trigger_labels=()) -> None:
    """Create the children of an approved proposal, one recorded step each, then link and label the parent. Safe to call again."""
    repo, num, pid = p["repo"], p["issue"], p["id"]
    parent = gh.get_issue(repo, num)
    if parent.get("state") != "open" or ("pull_request" in parent):
        return _set(conn, pid, "stale", "the ticket was closed")
    rows = kept(items(conn, pid))                       # dropped items never become tickets
    if p["snapshot"] != fingerprint(parent) and not any(r["status"] != "new" for r in rows):
        return _set(conn, pid, "stale", "the ticket was edited after the split was proposed")
    made = {}
    for index, it in enumerate(rows, 1):
        if it["status"] == "created":
            made[it["pos"]] = (it["child_repo"], it["child_issue"])
            continue
        if it["status"] == "creating":                   # a crash between creating the ticket and recording it
            found = _found_local(conn, it, pid) if tracker.is_local(num) else None
            if found is None:
                return _set(conn, pid, "failed", f"item {index} may already exist; a person should check before it is created again")
            number, gh_id = found, ""
        else:
            conn.execute("UPDATE split_items SET status='creating' WHERE proposal_id=? AND pos=?", (pid, it["pos"]))
            conn.commit()
            try:
                number, gh_id = _create(gh, conn, p, it, made, index, len(rows), github_ok)
            except Exception:
                if tracker.is_local(num):                # a local ticket is stored whole or not at all, so it is safe to try again
                    conn.execute("UPDATE split_items SET status='new' WHERE proposal_id=? AND pos=?", (pid, it["pos"]))
                    conn.commit()
                raise                                    # on GitHub the ticket may exist: the item stays 'creating' and a person checks
        conn.execute("UPDATE split_items SET status='created', child_repo=?, child_issue=? WHERE proposal_id=? AND pos=?",
                     (it["repo"], number, pid, it["pos"]))
        conn.commit()
        made[it["pos"]] = (it["repo"], number)
        if gh_id and it["repo"] == repo:
            try:
                gh.add_sub_issue(repo, num, gh_id)       # best effort, as for a follow-up
            except Exception:
                log.warning("could not link %s as a sub-issue of %s#%s", number, repo, num)
        if p["start_action"] and p["start_action"] in start_labels:
            try:
                gh.add_labels(it["repo"], number, [p["start_action"]])
            except Exception:
                log.warning("could not start %s#%s", it["repo"], number)
    gh.comment(repo, num, sanitize_markdown(DONE.format(items="\n".join(f"- [ ] {_shown(*made[i])}" for i in sorted(made)))))
    for label in (*trigger_labels, PROPOSED_LABEL):
        gh.remove_label(repo, num, label)
    try:
        gh.create_label(repo, UMBRELLA_LABEL, "5319e7", "Split into smaller tickets; closes when they are closed")
    except Exception:
        log.warning("could not create the label %s in %s", UMBRELLA_LABEL, repo)
    gh.add_labels(repo, num, [UMBRELLA_LABEL])
    _set(conn, pid, "done", f"{len(made)} tickets created")
    emit("decision", f"split into {len(made)} tickets, as a person approved", repo, num)


def keep_one(gh, conn, p: dict, start_labels, emit) -> None:
    """The ticket is worked on as one: say so on it, take the proposed-split label off and start it with the chosen action."""
    repo, num = p["repo"], p["issue"]
    if gh.get_issue(repo, num).get("state") != "open":
        return _set(conn, p["id"], "stale", "the ticket was closed")
    gh.comment(repo, num, KEPT)
    gh.remove_label(repo, num, PROPOSED_LABEL)
    if p["start_action"] and p["start_action"] in start_labels:
        gh.add_labels(repo, num, [p["start_action"]])
    _set(conn, p["id"], "kept", "worked on as one ticket")
    emit("decision", "kept as one ticket instead of splitting, as a person chose", repo, num)


def process(cfg, gh, conn, emit, start_labels, trigger_labels=()) -> None:
    """Create the children of every approved proposal, and start the tickets a person kept whole (called every poll). A proposal
    that keeps failing is marked failed."""
    for (pid,) in conn.execute("SELECT id FROM split_proposals WHERE status='keeping' ORDER BY id").fetchall():
        p = get(conn, pid)
        try:
            keep_one(gh, conn, p, start_labels, emit)
        except Exception:
            log.exception("could not keep %s#%s whole", p["repo"], p["issue"])
            fails = p["fails"] + 1
            _set(conn, pid, "failed" if fails >= MAX_TRIES else "keeping", "could not start the ticket" if fails >= MAX_TRIES else "", fails)
    for (pid,) in conn.execute("SELECT id FROM split_proposals WHERE status='approved' ORDER BY id").fetchall():
        p = get(conn, pid)
        try:
            apply_one(cfg.github_issues_enabled, gh, conn, p, start_labels, emit, trigger_labels)
        except Exception:
            log.exception("could not split %s#%s", p["repo"], p["issue"])
            fails = p["fails"] + 1
            _set(conn, pid, "failed" if fails >= MAX_TRIES else "approved", "could not create the tickets" if fails >= MAX_TRIES else "", fails)


def blockers(conn, gh, repo: str, num: int, is_open, cache: dict) -> list[str]:
    """How the open tickets this child waits for are named. An item waits for the items it comes after (any repository), whether or
    not the project manager is on."""
    out = []
    for pid, pos in conn.execute("SELECT i.proposal_id, i.pos FROM split_items i JOIN split_proposals p ON p.id=i.proposal_id "
                                 "WHERE i.child_repo=? AND i.child_issue=? AND p.status IN ('approved','done')", (repo, int(num))):
        after = {int(a) for a in conn.execute("SELECT after FROM split_items WHERE proposal_id=? AND pos=?", (pid, pos)).fetchone()[0].split(",") if a}
        for r, n in conn.execute("SELECT child_repo, child_issue FROM split_items WHERE proposal_id=? AND child_issue IS NOT NULL "
                                 f"AND pos IN ({','.join('?' * len(after)) or 'NULL'})", (pid, *sorted(after))).fetchall():
            if is_open(gh, r, n, cache):
                out.append(_shown(r, n))
    return out


def _closed(gh, repo: str, num: int, cache: dict) -> bool:
    """Closed for certain: a ticket that cannot be read is not counted as closed."""
    if (repo, num) not in cache:
        try:
            cache[(repo, num)] = gh.get_issue(repo, num).get("state") == "closed"
        except Exception:
            log.warning("could not read %s#%s for the umbrella check", repo, num)
            cache[(repo, num)] = False
    return cache[(repo, num)]


def sweep(gh, conn, emit, github_due: bool = True) -> None:
    """Close the parent of a split once every child is closed. Once only: a parent a person reopens is left alone."""
    cache: dict = {}
    for (pid,) in conn.execute("SELECT id FROM split_proposals WHERE status='done' AND umbrella_closed IS NULL ORDER BY id LIMIT ?", (SWEEP_LIMIT,)).fetchall():
        p, kids = get(conn, pid), kept(items(conn, pid))
        parts = [(k["child_repo"], k["child_issue"]) for k in kids]
        if not parts or any(n is None for _, n in parts) or (not github_due and any(not tracker.is_local(n) for _, n in parts + [(p["repo"], p["issue"])])):
            continue
        try:
            if not all(_closed(gh, r, n, cache) for r, n in parts):
                continue
            conn.execute("UPDATE split_proposals SET umbrella_closed=? WHERE id=?", (time.time(), pid))
            conn.commit()
            if gh.get_issue(p["repo"], p["issue"]).get("state") == "open":
                gh.comment(p["repo"], p["issue"], CLOSED)
                gh.update_issue(p["repo"], p["issue"], state="closed")
                emit("decision", "every ticket of the split is closed: closed the umbrella", p["repo"], p["issue"])
        except Exception:
            log.exception("could not close the umbrella %s#%s", p["repo"], p["issue"])
