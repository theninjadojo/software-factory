"""The ticket review: recommend what to do with a repository's open and in-progress tickets.

A person asks for a review of one repository in the admin UI. The orchestrator gathers the repository's open tickets (local and
GitHub; finished work, a ticket whose pull request is open, is left out), and a read-only agent ends its document with a
`factory-ticket-review` block. It may recommend four things: close a ticket that is already built, merge a duplicate into the
ticket to keep, split a ticket that is too large, or re-run a failed one. That block is untrusted agent output (derived
from ticket text anyone may write), so it is only data: each entry is validated against fixed sets (a ticket of the snapshot, a
verdict of built / duplicate / split / rerun, evidence that is a repository path or a commit hash, a canonical ticket of the
snapshot, split items checked as split.py checks the analyst's, a re-run only of a ticket that failed) and anything else is
dropped. The result is a list of proposals. Nothing is changed by them.

A person picks proposals in the UI. Only a picked proposal is applied, by the orchestrator, one resumable step at a time: re-read the
ticket (one edited since the review is marked stale, not closed), post a fixed comment built from validated values, take off the
trigger labels, close it. A duplicate is merged: the ticket to keep gets a comment holding the duplicate's sanitized description
and the addresses of its images or the names of its files (never their content), then the duplicate is closed with a pointer to
it. A split is only proposed on the ticket (its Linked tickets card), where a person approves it as any other split. A re-run
applies the Auto label, as the ticket's own Auto button does. A proposal a person rejected is not proposed again."""
import json
import logging
import re
import sqlite3
import time
from dataclasses import dataclass

from . import split
from . import tracker
from . import questions as Q
from .sanitize import sanitize_markdown

log = logging.getLogger("factory.ticketreview")
BLOCK = re.compile(r"^```factory-ticket-review[ \t]*\n(.*?)^```[ \t]*$", re.M | re.S)
PATH = re.compile(r"^[A-Za-z0-9_][\w.\-/]{0,199}$")
SOURCES = ("local", "github", "local,github")
SCOPES = ("all", "new", "progress")       # open and in progress / not started only / in progress only
VERDICTS = ("built", "duplicate", "split", "rerun")
DONE_LABEL, FAILED_LABEL = "factory:pr-open", "factory:failed"
IMAGE = re.compile(r"!\[[^\]\n]*\]\((https://[^)\s]{1,500})\)|<img\b[^>]*?\bsrc=[\"'](https://[^\"'\s]{1,500})[\"']", re.I)
MAX_MERGED = 6000
MAX_REASON, MAX_EVIDENCE, MAX_TRIES, MAX_PAGES = 200, 5, 5, 20
STALE_RUNNING = 7200                    # a review still "running" after this long was lost with a restart
BUILT_COMMENT = ("Closed by the factory's ticket review, approved in the admin UI: the work looks already built.\n\n"
                 "**Evidence:** {evidence}\n\n**Why:** {reason}\n\nIt can be reopened if this was a mistake.")
MERGE_COMMENT = ("Merged in from {source} by the factory's ticket review, approved in the admin UI. {source} is closed as a duplicate "
                 "of this ticket; what it asked for follows, so the work here covers it.\n\n{title}{body}{files}")
DUPLICATE_COMMENT = ("Closed by the factory's ticket review, approved in the admin UI, as a duplicate of {target}.\n\n"
                     "**Why:** {reason}\n\nIt can be reopened if this was a mistake.")


@dataclass(frozen=True)
class Proposal:
    issue: int
    verdict: str                # built | duplicate (merged on apply) | split | rerun
    target: int | None          # the ticket to keep, for a duplicate
    evidence: tuple             # built: paths and hashes; split: its split.Item values
    reason: str


def ensure_tables(db: sqlite3.Connection) -> None:
    db.execute(
        """CREATE TABLE IF NOT EXISTS ticket_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT, repo TEXT NOT NULL, sources TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('requested','running','done','failed')),
            detail TEXT NOT NULL DEFAULT '', tickets INTEGER NOT NULL DEFAULT 0, run_id INTEGER,
            created REAL NOT NULL, finished REAL, scope TEXT NOT NULL DEFAULT 'all')""")
    if "scope" not in {r[1] for r in db.execute("PRAGMA table_info(ticket_reviews)")}:
        db.execute("ALTER TABLE ticket_reviews ADD COLUMN scope TEXT NOT NULL DEFAULT 'all'")
    db.execute("CREATE INDEX IF NOT EXISTS ticket_reviews_repo ON ticket_reviews (repo, id DESC)")
    old = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='review_proposals'").fetchone()
    if old and "'rerun'" not in old[0]:                 # made before split and re-run: widen the verdict check, keep every row
        db.execute("ALTER TABLE review_proposals RENAME TO review_proposals_v1")
    db.execute(
        """CREATE TABLE IF NOT EXISTS review_proposals (
            id INTEGER PRIMARY KEY AUTOINCREMENT, review_id INTEGER NOT NULL, repo TEXT NOT NULL, issue INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT '', verdict TEXT NOT NULL CHECK (verdict IN ('built','duplicate','split','rerun')), target INTEGER,
            evidence TEXT NOT NULL DEFAULT '[]', reason TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
            snapshot_updated TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL CHECK (status IN ('proposed','queued','applied','rejected','stale','failed')),
            step INTEGER NOT NULL DEFAULT 0, fails INTEGER NOT NULL DEFAULT 0, detail TEXT NOT NULL DEFAULT '',
            decided REAL, merged INTEGER NOT NULL DEFAULT 0, warn TEXT NOT NULL DEFAULT '', UNIQUE (review_id, issue))""")
    if old and "'rerun'" not in old[0]:
        cols = ("id, review_id, repo, issue, title, verdict, target, evidence, reason, note, snapshot_updated, status, step, fails, "
                "detail, decided")
        db.execute(f"INSERT INTO review_proposals ({cols}) SELECT {cols} FROM review_proposals_v1")
        db.execute("DROP TABLE review_proposals_v1")
        db.execute("DROP INDEX IF EXISTS review_proposals_repo")
    if "warn" not in {r[1] for r in db.execute("PRAGMA table_info(review_proposals)")}:
        db.execute("ALTER TABLE review_proposals ADD COLUMN warn TEXT NOT NULL DEFAULT ''")
    db.execute("CREATE INDEX IF NOT EXISTS review_proposals_repo ON review_proposals (repo, status)")
    db.commit()


# ---------------------------------------------------------------- validating the agent's block
def _num(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def _entry(d, nums: set, allowed=(), rerunnable=frozenset()) -> Proposal:
    if not isinstance(d, dict):
        raise ValueError("not an object")
    n = d.get("issue")
    if not _num(n) or n not in nums:
        raise ValueError("not a ticket of the snapshot")
    verdict = d.get("verdict")
    reason = d.get("reason", "")
    reason = " ".join(reason.split())[:MAX_REASON] if isinstance(reason, str) else ""
    if verdict == "built":
        ev = d.get("evidence")
        if not isinstance(ev, list) or not 1 <= len(ev) <= MAX_EVIDENCE:
            raise ValueError("a built ticket needs 1 to 5 pieces of evidence")
        if not all(isinstance(e, str) and PATH.match(e) and ".." not in e.split("/") for e in ev):
            raise ValueError("evidence must be a repository path or a commit hash")
        return Proposal(n, "built", None, tuple(dict.fromkeys(ev)), reason)
    if verdict in ("duplicate", "merge"):
        of = d.get("of")
        if not _num(of) or of not in nums or of == n:
            raise ValueError("a duplicate must name another ticket of the snapshot")
        return Proposal(n, "duplicate", of, (), reason)
    if verdict == "split":
        p = split.validate(d, allowed)
        if p is None:
            raise ValueError("a split needs 2 to 10 valid items")
        return Proposal(n, "split", None, p.items, reason or p.reason)
    if verdict == "rerun":
        if n not in rerunnable:
            raise ValueError("only a ticket that failed can be re-run")
        return Proposal(n, "rerun", None, (), reason)
    raise ValueError("bad verdict")


def parse(text: str, nums: set, allowed=(), rerunnable=frozenset()) -> list[Proposal] | None:
    """The validated proposals of the last `factory-ticket-review` block (the first entry per ticket wins). An invalid entry is
    dropped. A chain of duplicates (A of B, B of C) points at the last ticket; a cycle, or a duplicate of a ticket that is itself
    proposed as built, is dropped. allowed: the repositories a split's items may be filed in; rerunnable: the tickets that failed.
    None when there is no block or its JSON is malformed."""
    blocks = BLOCK.findall(text or "")
    if not blocks:
        return None
    try:
        data = json.loads(blocks[-1])
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("tickets"), list):
        return None
    found: dict[int, Proposal] = {}
    for d in data["tickets"][:500]:
        try:
            p = _entry(d, nums, allowed, rerunnable)
        except ValueError as e:
            log.info("ticket review: entry dropped (%s)", e)
            continue
        found.setdefault(p.issue, p)
    out = []
    for p in found.values():
        if p.verdict == "duplicate":
            seen, t = {p.issue}, p.target
            while t in found and found[t].verdict == "duplicate" and t not in seen:
                seen.add(t)
                t = found[t].target
            if t in seen or (t in found and found[t].verdict == "built"):
                continue                                    # a cycle, or the ticket to keep is itself proposed for closing
            p = Proposal(p.issue, "duplicate", t, (), p.reason)
        out.append(p)
    return out


# ---------------------------------------------------------------- the snapshot
def _names(issue: dict) -> set:
    return {x.get("name", "") for x in issue.get("labels", [])}


def progress(issue: dict) -> int:
    """How far the factory got with a ticket, from its labels: 0 not started, 1 in progress (a stage ran or is queued, or it
    failed), 2 running now or its pull request is open."""
    names = _names(issue)
    if any(n.startswith("factory:working") for n in names) or DONE_LABEL in names:
        return 2
    return 1 if any(n.startswith(("factory:", "stage:")) for n in names) else 0


def finished(issue: dict) -> bool:
    """Its work is done: the factory's pull request is open (and the ticket did not fail since)."""
    names = _names(issue)
    return DONE_LABEL in names and FAILED_LABEL not in names


def merged(db, repo: str, n: int) -> bool:
    """Every pull request the factory opened for the ticket is merged (ci.py records a merged one as summary 'merged')."""
    try:
        got = [r[0] for r in db.execute("SELECT summary FROM prs WHERE issue_repo=? AND issue_num=?", (repo, int(n)))]
    except sqlite3.OperationalError:
        return False
    return bool(got) and all(x == "merged" for x in got)


def busy(issue: dict, triggers) -> bool:
    """Being built: running now, queued (a trigger label) or waiting for a person's answers."""
    names = _names(issue)
    return any(n.startswith("factory:working") for n in names) or bool(names & set(triggers))


def failed(items: list[dict]) -> frozenset:
    """The tickets a review may propose to re-run: they failed and nothing runs or waits on them now."""
    return frozenset(i["number"] for i in items if FAILED_LABEL in _names(i) and progress(i) < 2)


def snapshot(cfg, gh, db, repo: str, sources: str, scope: str = "all") -> tuple[list[dict], list[str]]:
    """(open tickets as GitHub-shaped dicts, notes about what was left out). Local tickets first, then GitHub's. Pull requests,
    GitHub issues that were moved into the local tracker, finished work, tickets outside the scope (not started only, in progress
    only) and anything past review.max_tickets are left out."""
    items, notes = [], []
    want = sources.split(",")
    if "local" in want:
        if cfg.local_enabled:
            t = tracker.LocalTracker(db)
            items += [t.issue(repo, n) for (n,) in db.execute(
                "SELECT number FROM local_tickets WHERE repo=? AND state='open' ORDER BY number", (repo,)).fetchall()]
        else:
            notes.append("local tickets skipped (the local tracker is off)")
    if "github" in want:
        if not cfg.github_issues_enabled:
            notes.append("GitHub skipped (GitHub issues are off)")
        else:
            moved = {r[0] for r in db.execute("SELECT gh_number FROM local_imports WHERE repo=?", (repo,))}
            try:
                for page in range(1, MAX_PAGES + 1):
                    got, more = gh.issues(repo, "open", None, page)
                    items += [i for i in got if i["number"] not in moved
                              and tracker.MOVED_LABEL not in {x.get("name") for x in i.get("labels", [])}]
                    if not more:
                        break
            except OSError:
                notes.append("GitHub skipped (it could not be reached)")
    done = {i["number"] for i in items if finished(i) or merged(db, repo, i["number"])}
    if done:
        notes.append(f"{len(done)} finished ticket(s) left out")
        items = [i for i in items if i["number"] not in done]
    if scope == "new":
        items = [i for i in items if progress(i) == 0]
    elif scope == "progress":
        items = [i for i in items if progress(i) > 0]
    cap = cfg.ticket_review.max_tickets
    if len(items) > cap:
        notes.append(f"only the first {cap} of {len(items)} tickets were reviewed")
        items = items[:cap]
    return items, notes


def blocker_notes(items: list[dict]) -> dict[int, str]:
    """ticket -> 'named as a blocker by ...', from the `Blocked by #N` lines of the snapshot's bodies."""
    from . import pm                                    # pm imports db, which imports this module
    by: dict[int, list[int]] = {}
    for i in items:
        for n in pm.explicit_blockers(i.get("body") or ""):
            by.setdefault(n, []).append(i["number"])
    return {n: "Named as a blocker by " + ", ".join(tracker.display(x) for x in sorted(who)[:5]) for n, who in by.items()}


# ---------------------------------------------------------------- requests, reviews and proposals
def request(db, repo: str, sources: str, now: float, scope: str = "all") -> int | None:
    """Ask the orchestrator to review a repository at its next poll. None when one is already waiting or running for it."""
    ensure_tables(db)
    if db.execute("SELECT 1 FROM ticket_reviews WHERE repo=? AND status IN ('requested','running')", (repo,)).fetchone():
        return None
    cur = db.execute("INSERT INTO ticket_reviews (repo, sources, status, created, scope) VALUES (?,?,'requested',?,?)",
                     (repo, sources, now, scope if scope in SCOPES else "all"))
    db.commit()
    return cur.lastrowid


def requested(db) -> list[dict]:
    return [{"id": i, "repo": r, "sources": s, "scope": sc} for i, r, s, sc in db.execute(
        "SELECT id, repo, sources, scope FROM ticket_reviews WHERE status='requested' ORDER BY id")]


def set_status(db, rid: int, status: str, detail: str = "", tickets: int | None = None, run_id=None) -> None:
    db.execute("UPDATE ticket_reviews SET status=?, detail=?, tickets=COALESCE(?, tickets), run_id=COALESCE(?, run_id), finished=? WHERE id=?",
               (status, detail[:500], tickets, run_id, time.time() if status in ("done", "failed") else None, rid))
    db.commit()


def expire(db, now: float) -> None:
    db.execute("UPDATE ticket_reviews SET status='failed', detail='lost in a restart', finished=? WHERE status='running' AND created<?",
               (now, now - STALE_RUNNING))
    db.commit()


def store(db, rid: int, repo: str, found: list[Proposal], items: list[dict], triggers=()) -> int:
    """Keep the proposals a person has not rejected before; an older proposal still waiting is superseded. Returns how many were kept.
    A merge of two tickets that are both being built (triggers: the labels that queue work) carries a warning a person confirms."""
    by = {i["number"]: i for i in items}
    notes = blocker_notes(items)
    found = orient(found, by)
    rejected = {(r[0], r[1], r[2]) for r in db.execute(
        "SELECT issue, verdict, COALESCE(target, 0) FROM review_proposals WHERE repo=? AND status='rejected'", (repo,))}
    db.execute("UPDATE review_proposals SET status='stale', detail='superseded by a newer review' WHERE repo=? AND status='proposed'", (repo,))
    kept = 0
    for p in found:
        if (p.issue, p.verdict, p.target or 0) in rejected:
            continue
        i = by[p.issue]
        warn = (f"Both are being built: merging stops the work on {tracker.display(p.issue)}"
                if p.verdict == "duplicate" and p.target in by and busy(i, triggers) and busy(by[p.target], triggers) else "")
        db.execute("INSERT INTO review_proposals (review_id, repo, issue, title, verdict, target, evidence, reason, note, snapshot_updated, "
                   "status, warn) VALUES (?,?,?,?,?,?,?,?,?,?,'proposed',?)",
                   (rid, repo, p.issue, (i.get("title") or "")[:200], p.verdict, p.target, json.dumps(_evidence(p)),
                    sanitize_markdown(p.reason, MAX_REASON), notes.get(p.issue, ""), i.get("updated_at", ""), warn))
        kept += 1
    db.commit()
    return kept


def _evidence(p: Proposal) -> list:
    if p.verdict == "split":
        return [{"title": it.title, "body": it.body, "repo": it.repo, "after": list(it.after)} for it in p.evidence]
    return list(p.evidence)


def orient(found: list[Proposal], by: dict) -> list[Proposal]:
    """A merge keeps the ticket the factory got further with: when the one proposed for closing is further along than the one to
    keep, they swap (unless the other already has a proposal of its own)."""
    taken, out = {p.issue for p in found}, []
    for p in found:
        if (p.verdict == "duplicate" and p.issue in by and p.target in by and p.target not in taken
                and progress(by[p.issue]) > progress(by[p.target])):
            taken.discard(p.issue)
            taken.add(p.target)
            p = Proposal(p.target, "duplicate", p.issue, (), p.reason)
        out.append(p)
    return out


REVIEW_COLS = ("id", "repo", "sources", "scope", "status", "detail", "tickets", "run_id", "created", "finished")


def latest(db, repo: str) -> dict | None:
    try:
        r = db.execute(f"SELECT {', '.join(REVIEW_COLS)} FROM ticket_reviews WHERE repo=? ORDER BY id DESC LIMIT 1", (repo,)).fetchone()
    except sqlite3.OperationalError:
        return None
    return dict(zip(REVIEW_COLS, r)) if r else None


def review(db, rid: int) -> dict | None:
    try:
        r = db.execute(f"SELECT {', '.join(REVIEW_COLS)} FROM ticket_reviews WHERE id=?", (int(rid),)).fetchone()
    except sqlite3.OperationalError:
        return None
    return dict(zip(REVIEW_COLS, r)) if r else None


def reviews(db, repos, limit: int = 20) -> list[dict]:
    """The latest reviews of these repositories, newest first, each with how many recommendations it made."""
    repos = list(repos)
    if not repos:
        return []
    try:
        rows = db.execute(f"SELECT {', '.join('r.' + c for c in REVIEW_COLS)}, (SELECT COUNT(*) FROM review_proposals p WHERE p.review_id=r.id) "
                          f"FROM ticket_reviews r WHERE r.repo IN ({','.join('?' * len(repos))}) ORDER BY r.id DESC LIMIT ?",
                          (*repos, limit)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(zip(REVIEW_COLS + ("found",), r)) for r in rows]


def write_up(db, run_id) -> str:
    """The agent's document of a review run, without the machine-read block at its end. Untrusted: render it escaped."""
    if not run_id:
        return ""
    try:
        r = db.execute("SELECT output FROM runs WHERE id=?", (int(run_id),)).fetchone()
    except sqlite3.OperationalError:
        return ""
    return BLOCK.sub("", r[0] or "").strip() if r else ""


VIEWS = {"waiting": ("proposed", "queued", "failed"), "applied": ("applied",), "dismissed": ("rejected",), "outdated": ("stale",)}


def _where(repo: str, rid) -> tuple[str, tuple]:
    return ("repo=? AND review_id=?", (repo, int(rid))) if rid else ("repo=?", (repo,))


def counts(db, repo: str, rid=None) -> dict:
    """{view: how many proposals it shows}, of one review or of every review of the repository."""
    where, args = _where(repo, rid)
    try:
        got = dict(db.execute(f"SELECT status, COUNT(*) FROM review_proposals WHERE {where} GROUP BY status", args).fetchall())
    except sqlite3.OperationalError:
        got = {}
    return {v: sum(got.get(s, 0) for s in sts) for v, sts in VIEWS.items()}


def proposals(db, repo: str, view: str = "waiting", rid=None) -> list[dict]:
    """One view's proposals, newest review first: waiting for a person (or being applied, or failed), applied, dismissed, or out
    of date (a newer review replaced it, or the ticket changed). Of one review when rid is given."""
    sts = VIEWS.get(view, VIEWS["waiting"])
    where, args = _where(repo, rid)
    try:
        rows = db.execute("SELECT id, issue, title, verdict, target, evidence, reason, note, status, detail, warn FROM review_proposals "
                          f"WHERE {where} AND status IN ({','.join('?' * len(sts))}) ORDER BY review_id DESC, issue LIMIT 200",
                          (*args, *sts)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [{"id": a, "issue": b, "title": c, "verdict": d, "target": e, "evidence": json.loads(f), "reason": g, "note": h, "status": s, "detail": x,
             "warn": w} for a, b, c, d, e, f, g, h, s, x, w in rows]


def needs_confirm(db, ids: list[int]) -> set[int]:
    """Of the picked proposals, the ones whose warning a person must confirm before they are applied."""
    if not ids:
        return set()
    try:
        return {r[0] for r in db.execute(f"SELECT id FROM review_proposals WHERE warn != '' AND status='proposed' AND id IN ({','.join('?' * len(ids))})", ids)}
    except sqlite3.OperationalError:
        return set()


def decide(db, ids: list[int], accept: bool, now: float) -> int:
    """Queue (accept) or reject proposals a person picked. Only proposals still waiting count, and at most MAX_BULK. Returns how many."""
    ensure_tables(db)
    n = 0
    for pid in ids[:tracker.MAX_BULK]:
        cur = db.execute("UPDATE review_proposals SET status=?, decided=? WHERE id=? AND status='proposed'",
                         ("queued" if accept else "rejected", now, int(pid)))
        n += cur.rowcount
    db.commit()
    return n


# ---------------------------------------------------------------- applying what a person picked
def trigger_labels(cfg) -> tuple:
    """Every label that makes the factory start work on a ticket, and the one that holds it waiting for answers."""
    return (cfg.trigger_label, cfg.auto_label, *(r.label for r in cfg.roles), *([cfg.review.label] if cfg.review.enabled else []),
            *([cfg.conflicts.label] if cfg.conflicts.enabled else []), Q.NEEDS_ANSWERS)


def comment_text(repo: str, row: dict) -> str:
    """Built from validated values only: evidence is path / hash shaped, the reason a sanitized line, the target a ticket number."""
    reason = row["reason"] or "(no reason given)"
    if row["verdict"] == "built":
        text = BUILT_COMMENT.format(evidence=", ".join(f"`{e}`" for e in json.loads(row["evidence"])), reason=reason)
    else:
        text = DUPLICATE_COMMENT.format(target=tracker.ref(repo, row["target"]), reason=reason)
    return sanitize_markdown(text, 4000)


def merge_text(gh, repo: str, issue: dict) -> str:
    """The comment that carries a duplicate into the ticket to keep: its title and sanitized description, then the addresses of the
    images in it (sanitizing removes the images themselves) and, for a local ticket, the names of its files. Never their content."""
    body = issue.get("body") or ""
    urls = list(dict.fromkeys(a or b for a, b in IMAGE.findall(body)))[:10]
    files = [f"- {u}" for u in urls]
    if tracker.is_local(int(issue["number"])):
        try:
            files += [f"- {' '.join(str(a.get('name') or 'file').split())[:120]} (open {tracker.display(int(issue['number']))} to see it)"
                      for a, _ in [x for x in (gh.attachments(repo, int(issue["number"])) or []) if x]][:10]
        except Exception:
            log.info("ticket review: the files of %s could not be listed", tracker.display(int(issue["number"])))
    title = " ".join((issue.get("title") or "").split())[:200]
    text = MERGE_COMMENT.format(source=tracker.ref(repo, int(issue["number"])), title=f"**{title}**\n\n" if title else "",
                                body=sanitize_markdown(body.strip(), MAX_MERGED) or "_(no description)_",
                                files=("\n\n**Images and files of the merged ticket:**\n" + "\n".join(files)) if files else "")
    return sanitize_markdown(text, MAX_MERGED + 2000)


def _open(gh, repo: str, n: int) -> dict | None:
    i = gh.get_issue(repo, n)
    return i if i.get("state", "open") == "open" and "pull_request" not in i else None


def apply_one(cfg, gh, db, row: dict) -> str:
    """Take one queued proposal as far as it goes. Returns its status: applied, stale, or queued (a step failed: it resumes next poll)."""
    repo, n, pid = row["repo"], row["issue"], row["id"]
    try:
        if row["step"] == 0:                                    # nothing was written yet: is the proposal still true?
            issue = _open(gh, repo, n)
            if issue is None or issue.get("updated_at", "") != row["snapshot_updated"]:
                return _finish(db, pid, "stale", "the ticket changed or closed after the review")
            if row["verdict"] == "duplicate" and (row["target"] == n or _open(gh, repo, row["target"]) is None):
                return _finish(db, pid, "stale", "the ticket to keep is closed")
            if row["verdict"] == "split":
                return _split(db, pid, repo, issue, row)
            if row["verdict"] == "rerun":
                return _rerun(cfg, gh, db, pid, repo, issue)
            if row["verdict"] == "duplicate" and not row.get("merged"):     # once only, even when a later step fails and resumes
                gh.comment(repo, row["target"], merge_text(gh, repo, issue))
                db.execute("UPDATE review_proposals SET merged=1 WHERE id=?", (pid,))
                db.commit()
                row["merged"] = 1
            gh.comment(repo, n, comment_text(repo, row))
            db.execute("UPDATE review_proposals SET step=1 WHERE id=?", (pid,))
            db.commit()
            row["step"] = 1
        if row["step"] == 1:                                    # a reopen must not silently restart the work
            held = {x.get("name") for x in gh.get_issue(repo, n).get("labels", [])}
            for lab in trigger_labels(cfg):
                if lab in held:
                    gh.remove_label(repo, n, lab)
            db.execute("UPDATE review_proposals SET step=2 WHERE id=?", (pid,))
            db.commit()
        gh.update_issue(repo, n, state="closed")                # the poll's stop_closed stops any running work
        return _finish(db, pid, "applied", "")
    except Exception as e:
        log.warning("ticket review: could not apply #%s of %s (%s)", n, repo, type(e).__name__)
        fails = row["fails"] + 1
        db.execute("UPDATE review_proposals SET fails=? WHERE id=?", (fails, pid))
        db.commit()
        return _finish(db, pid, "failed", "could not be closed after several tries") if fails >= MAX_TRIES else "queued"


def _split(db, pid: int, repo: str, issue: dict, row: dict) -> str:
    """Propose the split on the ticket itself; a person approves it on its Linked tickets card, as an analyst's split."""
    raw = json.loads(row["evidence"])
    items = tuple(split.Item(d["title"], d["body"], d["repo"], tuple(d.get("after", ()))) for d in raw)
    split.ensure_tables(db)
    split.store(db, repo, issue, split.Proposal(row["reason"] or "", items))
    return _finish(db, pid, "applied", "proposed on the ticket: approve the split there")


def _rerun(cfg, gh, db, pid: int, repo: str, issue: dict) -> str:
    """The ticket's Auto button: only when it still failed and nothing runs or waits on it."""
    names = _names(issue)
    if FAILED_LABEL not in names or any(n.startswith("factory:working") for n in names) or names & set(trigger_labels(cfg)):
        return _finish(db, pid, "stale", "the ticket no longer failed, or it is already queued or running")
    gh.add_labels(repo, int(issue["number"]), [cfg.auto_label])
    return _finish(db, pid, "applied", "started again with Auto")


def _finish(db, pid: int, status: str, detail: str) -> str:
    db.execute("UPDATE review_proposals SET status=?, detail=? WHERE id=?", (status, detail, pid))
    db.commit()
    return status


def process(cfg, gh, db, emit) -> None:
    """Apply the proposals a person queued, at most MAX_BULK per poll. Called from the poll loop; never in dry-run."""
    ensure_tables(db)
    cols = ("id", "repo", "issue", "verdict", "target", "evidence", "reason", "snapshot_updated", "step", "fails", "merged")
    queued = db.execute(f"SELECT {', '.join(cols)} FROM review_proposals WHERE status='queued' ORDER BY id LIMIT ?", (tracker.MAX_BULK,)).fetchall()
    for r in queued:
        row = dict(zip(cols, r))
        status = apply_one(cfg, gh, db, row)
        if status != "queued":
            emit("ticket-review", f"{tracker.display(row['issue'])} {status} ({row['verdict']})", row["repo"], row["issue"])
