"""A short conversation about a ticket, outside the label-driven pipeline.

The admin UI writes a person's message (behind its login and CSRF check) as a pending turn; the orchestrator's chat lane answers it
with a read-only run that has no repository clone and no GitHub token (runner.run_chat), and stores the sanitized reply. Both
sides live only in this database: nothing is posted to GitHub. Later stage runs and reviews get the newest turns as untrusted
data (prompt_turns).
The chat never changes the workflow itself. A reply may carry one `factory-proposal` block; parse_proposal keeps it only when it
asks to send the ticket back to a configured stage, and only a signed-in admin's confirmation (ui/chat.py) queues the existing
`redirect:<stage>` approval."""
import json
import re
import time

from .sanitize import sanitize_markdown

MAX_TEXT = 4000                       # a person's message
MAX_REPLY = 6000                      # a stored reply
MAX_REASON = 300
TURN_CHARS = 1500                     # of each turn in a later prompt
PROMPT_CHARS = 8000                   # all turns together in a later prompt
SHOWN = 100                           # turns the ticket page shows
PROPOSAL = re.compile(r"```factory-proposal[ \t]*\n(.*?)```", re.S)


def ensure_tables(db) -> None:
    db.execute(
        """CREATE TABLE IF NOT EXISTS ticket_chat (
            id INTEGER PRIMARY KEY AUTOINCREMENT, repo TEXT NOT NULL, issue INTEGER NOT NULL,
            author TEXT NOT NULL,                       -- 'person' | 'agent'
            body TEXT NOT NULL,                         -- sanitized; an agent turn without its proposal block
            created REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'done',        -- person turns: pending | running | done | failed | refused
            detail TEXT NOT NULL DEFAULT '',            -- why it failed or was refused (fixed text), shown to the person
            reply_to INTEGER,                           -- agent turn: the person turn it answers
            run_id INTEGER,                             -- runs.id (kind 'chat')
            proposal TEXT,                              -- validated JSON of an agent turn, or NULL
            proposal_state TEXT)                        -- NULL | open | confirmed | dismissed"""
    )
    db.execute("CREATE INDEX IF NOT EXISTS ticket_chat_ticket ON ticket_chat(repo, issue, id)")
    db.execute("CREATE INDEX IF NOT EXISTS ticket_chat_pending ON ticket_chat(status, id)")


def turns(db, repo: str, issue: int, limit: int = SHOWN) -> list[dict]:
    """The newest `limit` turns of a ticket, oldest first."""
    try:
        rows = db.execute("SELECT id, author, body, created, status, detail, reply_to, run_id, proposal, proposal_state FROM ticket_chat "
                          "WHERE repo=? AND issue=? ORDER BY id DESC LIMIT ?", (repo, int(issue), int(limit))).fetchall()
    except Exception:                                   # a database the orchestrator has not upgraded yet
        return []
    keys = ("id", "author", "body", "created", "status", "detail", "reply_to", "run_id", "proposal", "proposal_state")
    out = [dict(zip(keys, r)) for r in reversed(rows)]
    for t in out:
        t["proposal"] = _load(t["proposal"])
    return out


def _load(raw) -> dict | None:
    try:
        v = json.loads(raw) if raw else None
    except ValueError:
        return None
    return v if isinstance(v, dict) else None


def prompt_turns(db, repo: str, issue: int, limit: int, upto: int | None = None) -> list[tuple[str, str]]:
    """What a later stage prompt carries: the newest `limit` answered or sent turns (failed and refused ones are not part of the
    conversation), oldest first, each cut to TURN_CHARS and all together to PROMPT_CHARS (the newest are kept). [(author, text)].
    upto: only turns up to that id (the chat's own reply sees the conversation as of the message it answers)."""
    try:
        rows = db.execute("SELECT author, body FROM ticket_chat WHERE repo=? AND issue=? AND id<=? "
                          "AND (author='agent' OR status IN ('pending','running','done')) ORDER BY id DESC LIMIT ?",
                          (repo, int(issue), upto if upto is not None else 2 ** 62, int(limit))).fetchall()
    except Exception:
        return []
    out, used = [], 0
    for author, body in rows:                           # newest first, so the cap drops the oldest
        text = body[:TURN_CHARS]
        if used + len(text) > PROMPT_CHARS:
            break
        used += len(text)
        out.append((author, text))
    return out[::-1]


def add_person(db, repo: str, issue: int, text: str) -> int:
    cur = db.execute("INSERT INTO ticket_chat (repo, issue, author, body, created, status) VALUES (?,?,'person',?,?,'pending')",
                     (repo, int(issue), text, time.time()))
    db.commit()
    return cur.lastrowid


def busy(db, repo: str, issue: int) -> bool:
    """A message of this ticket is waiting for its reply (one at a time keeps the conversation in order)."""
    return db.execute("SELECT 1 FROM ticket_chat WHERE repo=? AND issue=? AND author='person' AND status IN ('pending','running')",
                      (repo, int(issue))).fetchone() is not None


def sent_since(db, repo: str, issue: int, since: float) -> int:
    return db.execute("SELECT COUNT(*) FROM ticket_chat WHERE repo=? AND issue=? AND author='person' AND created>=?",
                      (repo, int(issue), since)).fetchone()[0]


def claim(db) -> dict | None:
    """Take the oldest pending message (pending -> running); None when there is none."""
    row = db.execute("SELECT id, repo, issue, body FROM ticket_chat WHERE author='person' AND status='pending' ORDER BY id LIMIT 1").fetchone()
    if row is None:
        return None
    cur = db.execute("UPDATE ticket_chat SET status='running' WHERE id=? AND status='pending'", (row[0],))
    db.commit()
    return dict(zip(("id", "repo", "issue", "body"), row)) if cur.rowcount == 1 else None


def settle(db, turn_id: int, status: str, detail: str = "") -> None:
    db.execute("UPDATE ticket_chat SET status=?, detail=? WHERE id=?", (status, detail[:300], turn_id))
    db.commit()


def mark_interrupted(db) -> int:
    """Start-up: a message that was being answered when the orchestrator stopped will not be."""
    cur = db.execute("UPDATE ticket_chat SET status='failed', detail='interrupted by a restart' WHERE status='running'")
    db.commit()
    return cur.rowcount


def add_reply(db, turn: dict, text: str, roles: list[str], run_id: int | None) -> int:
    """Store the agent's reply to `turn`: sanitized, with a valid proposal (if any) split off. Returns the new turn's id."""
    body, proposal = parse_proposal(text, roles)
    cur = db.execute("INSERT INTO ticket_chat (repo, issue, author, body, created, status, reply_to, run_id, proposal, proposal_state) "
                     "VALUES (?,?,'agent',?,?,'done',?,?,?,?)",
                     (turn["repo"], turn["issue"], body, time.time(), turn["id"], run_id,
                      json.dumps(proposal) if proposal else None, "open" if proposal else None))
    db.execute("UPDATE ticket_chat SET status='done', detail='' WHERE id=?", (turn["id"],))
    db.commit()
    return cur.lastrowid


def parse_proposal(text: str, roles: list[str]) -> tuple[str, dict | None]:
    """The reply's text for display (every factory-proposal block removed, then sanitized) and the proposal it carried, or None.
    The agent's output is untrusted: a proposal counts only when it is exactly a send-back to a configured stage with a short
    reason. Anything else (another action, an unknown stage, extra keys, bad JSON) is dropped without a trace."""
    found = None
    for m in PROPOSAL.finditer(text):
        try:
            obj = json.loads(m.group(1))
        except (ValueError, RecursionError):
            continue
        if (isinstance(obj, dict) and set(obj) <= {"action", "stage", "reason"} and obj.get("action") == "redirect"
                and isinstance(obj.get("stage"), str) and obj["stage"] in roles and isinstance(obj.get("reason", ""), str)):
            reason = " ".join(sanitize_markdown(obj.get("reason", ""), MAX_REASON).split())[:MAX_REASON]
            found = {"action": "redirect", "stage": obj["stage"], "reason": reason}
    shown = PROPOSAL.sub("", text)
    shown = re.sub(r"```factory-proposal.*\Z", "", shown, flags=re.S)       # an unterminated block is not shown either
    return sanitize_markdown(shown.strip(), MAX_REPLY), found


def set_proposal_state(db, turn_id: int, old: str, new: str) -> bool:
    """Move a proposal from `old` to `new` once; False when it was not in `old` (a double click, or a stale page)."""
    cur = db.execute("UPDATE ticket_chat SET proposal_state=? WHERE id=? AND proposal_state=? AND proposal IS NOT NULL", (new, turn_id, old))
    db.commit()
    return cur.rowcount == 1
