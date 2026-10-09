"""Splitting a large ticket into smaller linked tickets.

The analyst may end its document with a `factory-split` block when the ticket is too large for one pull request. The block is
derived from untrusted ticket text, so it is only data, validated against fixed sets and length caps:

* 2 to MAX_ITEMS items (more than that rejects the whole block, the list is never cut);
* per item a one-line title and a body (both passed through sanitize_markdown), a repository from the parent's project, and `after`,
  the positions of earlier items it waits for (earlier only, so there are no cycles);
* anything else, and a malformed block, means no proposal: nothing changes.

A proposal is stored (`split_proposals`, `split_items`) and nothing is created until a person approves it in the admin UI or the
chat buttons (`split:<id>`). On approval the children are created one at a time, each step recorded so a retry or a restart never
creates one twice. Children carry no label unless the approver chose one start action; the factory database is the record of which
ticket belongs to which (so it works for local tickets too), and `blockers` keeps a later child's build waiting for the items it
comes after. The parent stays open as an umbrella and `sweep` closes it once every child is closed. main.py decides when these run."""
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
PROPOSED_LABEL, UMBRELLA_LABEL = "factory:split-proposed", "factory:umbrella"

PROPOSAL = "\n\n### Proposed split\n\n{reason}\n\n{items}\n\nNothing is created until a person approves it (in the factory UI or the factory's chat message)."
CHILD_HEAD = "Part of {parent} (item {pos} of {total}).\n\n"
DONE = "The factory split this ticket, as a person approved. It stays open and closes when every ticket below is closed.\n\n{items}"
CLOSED = "Every ticket this one was split into is closed, so the factory closed it."


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


def section(p: Proposal) -> str:
    """A readable list for the stage comment, built from the validated values only."""
    lines = [f"{i + 1}. **{it.title}** ({it.repo})" + (f", after {', '.join(str(a + 1) for a in it.after)}" if it.after else "")
             for i, it in enumerate(p.items)]
    return PROPOSAL.format(reason=p.reason or "The ticket is larger than one pull request.", items="\n".join(lines))


def fingerprint(issue: dict) -> str:
    """Of the parent's title and body: a proposal made for other text is stale."""
    return hashlib.sha256(f"{issue.get('title') or ''}\0{issue.get('body') or ''}".encode()).hexdigest()


def ensure_tables(db) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS split_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, repo TEXT NOT NULL, issue INTEGER NOT NULL, run_id INTEGER,
            reason TEXT NOT NULL DEFAULT '', snapshot TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'proposed',   -- proposed, approved, done, dismissed, stale, failed
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
    db.commit()


def store(db, repo: str, issue: dict, p: Proposal, run_id=None) -> int:
    """Keep a new proposal for a ticket; an earlier one still waiting for a person is superseded."""
    now = time.time()
    db.execute("UPDATE split_proposals SET status='stale', updated=? WHERE repo=? AND issue=? AND status='proposed'", (now, repo, issue["number"]))
    pid = db.execute("INSERT INTO split_proposals (repo, issue, run_id, reason, snapshot, created, updated) VALUES (?,?,?,?,?,?,?)",
                     (repo, issue["number"], run_id, p.reason, fingerprint(issue), now, now)).lastrowid
    db.executemany("INSERT INTO split_items (proposal_id, pos, title, body, repo, after) VALUES (?,?,?,?,?,?)",
                   [(pid, i, it.title, it.body, it.repo, ",".join(map(str, it.after))) for i, it in enumerate(p.items)])
    db.commit()
    return pid


PCOLS = ("id", "repo", "issue", "reason", "snapshot", "status", "start_action", "fails", "detail", "umbrella_closed")


def get(db, pid: int) -> dict | None:
    r = db.execute(f"SELECT {', '.join(PCOLS)} FROM split_proposals WHERE id=?", (int(pid),)).fetchone()
    return dict(zip(PCOLS, r)) if r else None


def items(db, pid: int) -> list[dict]:
    return [dict(zip(("pos", "title", "body", "repo", "after", "status", "child_repo", "child_issue"), r)) for r in db.execute(
        "SELECT pos, title, body, repo, after, status, child_repo, child_issue FROM split_items WHERE proposal_id=? ORDER BY pos", (int(pid),))]


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


def decide(db, pid: int, repo: str, issue: int, accept: bool, start_action: str = "", allowed_starts=()) -> bool:
    """A person's decision. Only a proposal of this very ticket that still waits; the start action must be one the caller allows.
    False when nothing changed."""
    p = get(db, pid)
    if p is None or p["repo"] != repo or p["issue"] != int(issue) or p["status"] != "proposed":
        return False
    if start_action and start_action not in allowed_starts:
        return False
    cur = db.execute("UPDATE split_proposals SET status=?, start_action=?, updated=? WHERE id=? AND status='proposed'",
                     ("approved" if accept else "dismissed", start_action if accept else "", time.time(), int(pid)))
    db.commit()
    return cur.rowcount == 1


def _set(db, pid: int, status: str, detail: str = "", fails: int | None = None) -> None:
    db.execute("UPDATE split_proposals SET status=?, detail=?, fails=COALESCE(?, fails), updated=? WHERE id=?",
               (status, detail[:300], fails, time.time(), int(pid)))
    db.commit()


def _shown(repo: str, num: int) -> str:
    return tracker.ref(repo, num)


def _create(gh, conn, p: dict, it: dict, made: dict, total: int, github_ok: bool) -> tuple[int, str]:
    """The child ticket of one item: local when the parent is local, otherwise a GitHub issue. Returns (number, id on GitHub or '')."""
    marker = f"<!-- factory:split={p['id']}.{it['pos']} -->"
    after = [made[a] for a in map(int, filter(None, it["after"].split(",")))]
    body = (CHILD_HEAD.format(parent=_shown(p["repo"], p["issue"]), pos=it["pos"] + 1, total=total)
            + (f"Starts after: {', '.join(_shown(*a) for a in after)}.\n\n" if after else "") + it["body"] + "\n\n" + marker)
    if tracker.is_local(p["issue"]):
        return tracker.LocalTracker(conn).create(it["repo"], it["title"], body, author=tracker.FACTORY_ACTOR), ""
    if not github_ok:
        raise RuntimeError("GitHub issues are switched off")
    made_issue = gh.create_followup(it["repo"], it["title"], body)
    return int(made_issue["number"]), made_issue.get("id")


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
    rows = items(conn, pid)
    if p["snapshot"] != fingerprint(parent) and not any(r["status"] != "new" for r in rows):
        return _set(conn, pid, "stale", "the ticket was edited after the split was proposed")
    made = {}
    for it in rows:
        if it["status"] == "created":
            made[it["pos"]] = (it["child_repo"], it["child_issue"])
            continue
        if it["status"] == "creating":                   # a crash between creating the ticket and recording it
            found = _found_local(conn, it, pid) if tracker.is_local(num) else None
            if found is None:
                return _set(conn, pid, "failed", f"item {it['pos'] + 1} may already exist; a person should check before it is created again")
            number, gh_id = found, ""
        else:
            conn.execute("UPDATE split_items SET status='creating' WHERE proposal_id=? AND pos=?", (pid, it["pos"]))
            conn.commit()
            try:
                number, gh_id = _create(gh, conn, p, it, made, len(rows), github_ok)
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


def process(cfg, gh, conn, emit, start_labels, trigger_labels=()) -> None:
    """Create the children of every approved proposal (called every poll). A proposal that keeps failing is marked failed."""
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
        p, kids = get(conn, pid), items(conn, pid)
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
