"""Comment triage: what a new comment on a ticket the factory already worked on should lead to.

A person with write access (or the admin UI, for a local ticket) comments on a ticket that is done or part-way. A read-only agent
reads the comment with the ticket and its stage documents, and ends its document with a `factory-triage` block. The comment and the
block are untrusted (the block is derived from text anyone may write), so the block is only data, validated against fixed sets:

* a decision of none, needs-person, redirect or followup;
* for a redirect, a stage that is one of the configured roles;
* for a followup, a one-line title and a body, both passed through sanitize_markdown with length caps.

Anything else, and a missing or malformed block, becomes needs-person: nothing else changes. none and needs-person only produce a
comment. A redirect (send the ticket back to a stage) and a followup (a new linked ticket, with no labels so nothing starts by
itself) are stored as a proposal and happen only after a person confirms it (`triage:<id>` in the approvals table, from the chat
buttons). The comments handled are recorded once each in `comment_triage` (keyed by the comment's id), so a restart or a repeated
poll never triages one twice, and the factory's own comments are never recorded. This module only validates and stores; main.py
decides when to run and applies what a person confirmed."""
import json
import re
import time
from dataclasses import dataclass

from .sanitize import sanitize_markdown

BLOCK = re.compile(r"^```factory-triage[ \t]*\n(.*?)^```[ \t]*$", re.M | re.S)
DECISIONS = ("none", "needs-person", "redirect", "followup")
MAX_TITLE, MAX_BODY, MAX_REASON, MAX_COMMENT = 120, 4000, 300, 3000
DAY = 86400
COLS = ("id", "repo", "issue", "comment_key", "author", "comment_at", "comment", "status", "decision", "stage", "title", "body",
        "reason", "run_id", "result")
SETTABLE = frozenset({"decision", "stage", "title", "body", "reason", "run_id", "result"})

NOTE = "The factory's comment triage read the comment from {who}: {what} {reason}"
PROPOSAL = ("The factory's comment triage read the comment from {who} and suggests to {what}. {reason}\n\n"
            "Nothing changes until a person confirms it (Confirm or Dismiss in the factory's chat message).")
FOLLOWUP_HEAD = "Follow-up of {ref}, raised in a comment there.\n\n"
FOLLOWUP_DONE = "The factory's comment triage opened the follow-up ticket {shown}, as a person confirmed. It carries no labels: a person starts it."


@dataclass(frozen=True)
class Decision:
    decision: str
    stage: str = ""
    title: str = ""
    body: str = ""
    reason: str = ""


def _line(v, limit: int) -> str:
    return " ".join(v.split())[:limit] if isinstance(v, str) else ""


def parse(text: str, stages) -> Decision | None:
    """The validated last `factory-triage` block, or None when there is none or anything in it is invalid (the caller then asks a
    person). `stages` are the names a redirect may name."""
    blocks = BLOCK.findall(text or "")
    if not blocks:
        return None
    try:
        d = json.loads(blocks[-1])
    except ValueError:
        return None
    if not isinstance(d, dict) or d.get("decision") not in DECISIONS:
        return None
    kind, reason = d["decision"], sanitize_markdown(_line(d.get("reason"), MAX_REASON), MAX_REASON + 20)
    stage = title = body = ""
    if kind == "redirect":
        stage = d.get("stage")
        if not isinstance(stage, str) or stage not in tuple(stages):
            return None
    elif kind == "followup":
        title = sanitize_markdown(_line(d.get("title"), MAX_TITLE), MAX_TITLE + 20)
        raw = d.get("body")
        if not title or not isinstance(raw, str) or not raw.strip():
            return None
        body = sanitize_markdown(raw.strip()[:MAX_BODY], MAX_BODY + 50)
    return Decision(kind, stage, title, body, reason)


def ensure_tables(db) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS comment_triage (
            id INTEGER PRIMARY KEY AUTOINCREMENT, repo TEXT NOT NULL, issue INTEGER NOT NULL,
            comment_key TEXT NOT NULL,                  -- gh:<comment id> or local:<local_comments.id>: each comment once
            author TEXT NOT NULL DEFAULT '', comment_at REAL NOT NULL DEFAULT 0, comment TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',     -- pending, seen-by-run, running, done, proposed, confirmed, dismissed, ignored, failed
            decision TEXT NOT NULL DEFAULT '', stage TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '', run_id INTEGER, result TEXT NOT NULL DEFAULT '',
            created REAL NOT NULL, updated REAL NOT NULL, UNIQUE (repo, comment_key));
        CREATE INDEX IF NOT EXISTS comment_triage_ticket ON comment_triage (repo, issue, status);
    """)
    db.commit()


def add(db, repo: str, issue: int, key: str, author: str, at: float, text: str, status: str = "pending", reason: str = "") -> bool:
    """Record a comment once. False when it was recorded before."""
    now = time.time()
    cur = db.execute("INSERT OR IGNORE INTO comment_triage (repo, issue, comment_key, author, comment_at, comment, status, reason, created, updated) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?)", (repo, int(issue), key, author, at, text[:MAX_COMMENT], status, reason[:MAX_REASON], now, now))
    db.commit()
    return cur.rowcount == 1


def _rows(db, where: str, args: tuple) -> list[dict]:
    return [dict(zip(COLS, r)) for r in db.execute(f"SELECT {', '.join(COLS)} FROM comment_triage WHERE {where} ORDER BY id", args)]


def get(db, tid: int) -> dict | None:
    return next(iter(_rows(db, "id=?", (int(tid),))), None)


def with_status(db, repo: str, status: str) -> list[dict]:
    return _rows(db, "repo=? AND status=?", (repo, status))


def mark(db, ids, status: str, **fields) -> None:
    """Set the status of rows, and some of their result fields (only the known ones)."""
    if not ids:
        return
    sets = [f"{k}=?" for k in fields if k in SETTABLE]
    args = [fields[k] for k in fields if k in SETTABLE]
    marks = ",".join("?" * len(ids))
    db.execute(f"UPDATE comment_triage SET status=?, updated=?{''.join(', ' + s for s in sets)} WHERE id IN ({marks})",
               (status, time.time(), *args, *ids))
    db.commit()


def runs_today(db, repo: str, issue: int, now: float) -> int:
    """Triage runs of a ticket in the last 24 hours (a run that hit the rate limit is tried again, so it does not count)."""
    return db.execute("SELECT COUNT(*) FROM runs WHERE kind='triage' AND repo=? AND issue=? AND started>? AND status!='rate-limited'",
                      (repo, int(issue), now - DAY)).fetchone()[0]


def last_work_started(db, repo: str, issue: int) -> float:
    """When the factory last started a run (any kind but a triage) on the ticket; 0 when it never did."""
    return db.execute("SELECT MAX(started) FROM runs WHERE kind!='triage' AND repo=? AND issue=?", (repo, int(issue))).fetchone()[0] or 0.0
