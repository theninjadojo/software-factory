"""Follow actions from the automated code review: proposals a person approves in the admin UI.

The reviewer ends its document with a `factory-review-actions` block. That block is untrusted agent output (derived from the diff and
the ticket, which anyone may write), so it is only data: each entry is validated against fixed sets (a label of `review.follow_labels`,
a configured stage) with a one-line sanitized reason, and anything else is dropped. The block is cut out of the PR comment. Storing a
proposal changes nothing.

Only proposals a person picks on the ticket page (at most 25 at a time, ids checked against the database and the ticket) are applied,
by the orchestrator, one resumable step at a time: re-check the ticket (closed, or its open PRs changed since the review, means stale),
then either post a fixed comment and add the label to the ticket, or queue the existing `redirect:<stage>` approval. A proposal a
person rejected is not proposed again for the same PRs. Nothing here touches a pull request."""
import json
import logging
import re
import sqlite3
import time

from . import tracker
from .sanitize import sanitize_markdown

log = logging.getLogger("factory.reviewactions")
BLOCK = re.compile(r"^```factory-review-actions[ \t]*\n(.*?)^```[ \t]*$", re.M | re.S)
KINDS = ("label", "send-back")
MAX_REASON, MAX_ACTIONS, MAX_TRIES, MAX_LABEL = 200, 5, 5, 50
LABEL_COMMENT = ("Follow-up from the automated code review, approved in the admin UI: labelled `{value}`.\n\n**Why:** {reason}")
PROMPT = (
    "\n\nFOLLOW-UP ACTIONS BLOCK (required). End your reply with exactly one fenced code block whose info string is "
    '`factory-review-actions`, holding JSON like {{"actions": [{{"kind": "label", "value": "{label}", "reason": "One plain line."}}]}}. '
    "It is only a proposal: a person approves each action. kind is `label` (value is one of: {labels}) or `send-back` (value is "
    "the stage to redo the ticket from, one of: {stages}). At most 5 actions, each with a reason of at most 200 characters; use "
    '{{"actions": []}} when nothing is needed. The block is machine-read: valid JSON, plain text, no Markdown inside it.')


def prompt(cfg) -> str:
    """The reviewer's instructions for the block; empty when follow actions are off."""
    r = cfg.review
    if not r.follow_actions or not r.follow_labels:
        return ""
    return PROMPT.format(label=r.follow_labels[0], labels=", ".join(r.follow_labels), stages=", ".join(x.name for x in cfg.roles) or "(none)")


def valid_label(label, reserved=()) -> bool:
    """A follow label must not start work or move a ticket through the pipeline: no factory: or stage: prefix, and none of the
    configured trigger labels (`reserved`)."""
    if not isinstance(label, str) or not 0 < len(label) <= MAX_LABEL or label != label.strip() or not label.isprintable():
        return False
    if any(c in label for c in ",\n`<>"):
        return False
    low = label.lower()
    return not (low.startswith(("factory:", "stage:")) or low in {x.lower() for x in reserved})


def ensure_tables(db: sqlite3.Connection) -> None:
    db.execute(
        """CREATE TABLE IF NOT EXISTS review_actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, repo TEXT NOT NULL, issue INTEGER NOT NULL,
            prs TEXT NOT NULL DEFAULT '[]',
            kind TEXT NOT NULL CHECK (kind IN ('label','send-back')), value TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL CHECK (status IN ('proposed','queued','applied','rejected','stale','failed')),
            step INTEGER NOT NULL DEFAULT 0, fails INTEGER NOT NULL DEFAULT 0, detail TEXT NOT NULL DEFAULT '',
            created REAL NOT NULL, decided REAL)""")
    db.execute("CREATE INDEX IF NOT EXISTS review_actions_ticket ON review_actions (repo, issue, status)")
    db.commit()


# ---------------------------------------------------------------- validating the agent's block
def strip(text: str) -> str:
    """The review without its machine-read block(s), so the PR comment never echoes the JSON."""
    return BLOCK.sub("", text or "").rstrip()


def parse(text: str, labels, stages) -> list[tuple[str, str, str]]:
    """(kind, value, reason) of the valid entries of the last block, at most MAX_ACTIONS, duplicates removed. Invalid entries are
    dropped; a missing or malformed block gives []."""
    blocks = BLOCK.findall(text or "")
    if not blocks:
        return []
    try:
        data = json.loads(blocks[-1])
    except ValueError:
        return []
    if not isinstance(data, dict) or not isinstance(data.get("actions"), list):
        return []
    out: dict[tuple[str, str], str] = {}
    for d in data["actions"][:50]:
        if not isinstance(d, dict):
            continue
        kind, value, reason = d.get("kind"), d.get("value"), d.get("reason", "")
        if kind == "label" and value not in labels or kind == "send-back" and value not in stages or kind not in KINDS:
            continue
        reason = " ".join(reason.split())[:MAX_REASON] if isinstance(reason, str) else ""
        out.setdefault((kind, value), sanitize_markdown(reason, MAX_REASON))
        if len(out) >= MAX_ACTIONS:
            break
    return [(k, v, r) for (k, v), r in out.items()]


# ---------------------------------------------------------------- storing and deciding
def store(db, repo: str, issue: int, prs: list, found: list[tuple[str, str, str]], now: float) -> int:
    """Supersede the ticket's proposals still waiting and keep the new ones, except those a person rejected for the same PRs."""
    ensure_tables(db)
    key = json.dumps(sorted([r, int(n)] for r, n in prs))
    rejected = {(k, v) for k, v in db.execute(
        "SELECT kind, value FROM review_actions WHERE repo=? AND issue=? AND status='rejected' AND prs=?", (repo, issue, key))}
    db.execute("UPDATE review_actions SET status='stale', detail='superseded by a newer review' "
               "WHERE repo=? AND issue=? AND (status='proposed' OR (status='queued' AND step=0))", (repo, issue))
    kept = 0
    for kind, value, reason in found:
        if (kind, value) in rejected:
            continue
        db.execute("INSERT INTO review_actions (repo, issue, prs, kind, value, reason, status, created) VALUES (?,?,?,?,?,?,'proposed',?)",
                   (repo, issue, key, kind, value, reason, now))
        kept += 1
    db.commit()
    return kept


def pending(db, repo: str, issue: int) -> list[dict]:
    """The ticket's proposals waiting for a person, and those queued, applied or failed."""
    try:
        rows = db.execute("SELECT id, kind, value, reason, status, detail FROM review_actions WHERE repo=? AND issue=? "
                          "AND status IN ('proposed','queued','applied','failed') ORDER BY id", (repo, issue)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [dict(zip(("id", "kind", "value", "reason", "status", "detail"), r)) for r in rows]


def decide(db, repo: str, issue: int, ids: list[int], accept: bool, now: float) -> int:
    """Queue (accept) or reject the picked proposals of this ticket. Only proposals still waiting count, at most MAX_BULK."""
    ensure_tables(db)
    n = 0
    for pid in ids[:tracker.MAX_BULK]:
        cur = db.execute("UPDATE review_actions SET status=?, decided=? WHERE id=? AND repo=? AND issue=? AND status='proposed'",
                         ("queued" if accept else "rejected", now, int(pid), repo, issue))
        n += cur.rowcount
    db.commit()
    return n


# ---------------------------------------------------------------- applying what a person picked
def _finish(db, pid: int, status: str, detail: str) -> str:
    db.execute("UPDATE review_actions SET status=?, detail=? WHERE id=?", (status, detail, pid))
    db.commit()
    return status


def apply_one(cfg, gh, db, row: dict) -> str:
    """Take one queued proposal as far as it goes: applied, stale, failed, or queued (waiting, or a step failed: it resumes)."""
    from . import db as dbm
    repo, n, pid = row["repo"], row["issue"], row["id"]
    try:
        if row["step"] == 0:                                    # nothing was written yet: is the proposal still true?
            issue = gh.get_issue(repo, n)
            if issue.get("state", "open") != "open" or "pull_request" in issue:
                return _finish(db, pid, "stale", "the ticket is closed")
            if sorted([r, int(x)] for r, x in dbm.prs_for_issue(db, repo, n)) != json.loads(row["prs"]):
                return _finish(db, pid, "stale", "the ticket's pull requests changed after the review")
            if row["kind"] == "label" and row["value"] not in cfg.review.follow_labels:
                return _finish(db, pid, "stale", "that label is no longer allowed")
            if row["kind"] == "send-back":
                if row["value"] not in [r.name for r in cfg.roles]:
                    return _finish(db, pid, "stale", "that stage is no longer configured")
                if any((r, i) == (repo, n) for r, i, _ in dbm.approvals(db)):
                    return "queued"                             # another decision waits for this ticket: try again next poll
                db.execute("INSERT OR REPLACE INTO approvals VALUES (?,?,?,?)", (repo, n, f"redirect:{row['value']}", time.time()))
                db.commit()
                return _finish(db, pid, "applied", "")
            gh.comment(repo, n, sanitize_markdown(LABEL_COMMENT.format(value=row["value"], reason=row["reason"] or "(no reason given)"), 4000))
            db.execute("UPDATE review_actions SET step=1 WHERE id=?", (pid,))
            db.commit()
        gh.add_labels(repo, n, [row["value"]])
        return _finish(db, pid, "applied", "")
    except Exception as e:
        log.warning("review actions: could not apply #%s of %s (%s)", n, repo, type(e).__name__)
        fails = row["fails"] + 1
        db.execute("UPDATE review_actions SET fails=? WHERE id=?", (fails, pid))
        db.commit()
        return _finish(db, pid, "failed", "could not be applied after several tries") if fails >= MAX_TRIES else "queued"


def process(cfg, gh, db, emit) -> None:
    """Apply the proposals a person queued, at most MAX_BULK per poll. Called from the poll loop; never in dry-run."""
    ensure_tables(db)
    cols = ("id", "repo", "issue", "prs", "kind", "value", "reason", "step", "fails")
    for r in db.execute(f"SELECT {', '.join(cols)} FROM review_actions WHERE status='queued' ORDER BY id LIMIT ?", (tracker.MAX_BULK,)).fetchall():
        row = dict(zip(cols, r))
        status = apply_one(cfg, gh, db, row)
        if status != "queued":
            emit("review-actions", f"{tracker.display(row['issue'])} {row['kind']} {row['value']}: {status}", row["repo"], row["issue"])
