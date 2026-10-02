import sqlite3
import time


def connect(path: str) -> sqlite3.Connection:
    db = sqlite3.connect(path, timeout=10)
    db.execute("PRAGMA journal_mode=WAL")           # the UI reads while the orchestrator writes
    db.execute("PRAGMA busy_timeout=10000")
    db.execute(
        """CREATE TABLE IF NOT EXISTS decisions (
            repo TEXT NOT NULL, issue INTEGER NOT NULL, updated_at TEXT NOT NULL,
            outcome TEXT NOT NULL, detail TEXT NOT NULL, decided_at REAL NOT NULL,
            PRIMARY KEY (repo, issue, updated_at))"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS approvals (
            repo TEXT NOT NULL, issue INTEGER NOT NULL, action TEXT NOT NULL, created REAL NOT NULL,
            PRIMARY KEY (repo, issue))"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS prs (
            repo TEXT NOT NULL, number INTEGER NOT NULL, issue_repo TEXT NOT NULL, issue_num INTEGER NOT NULL,
            status TEXT NOT NULL, rounds INTEGER NOT NULL DEFAULT 0, watch_started REAL NOT NULL,
            updated REAL NOT NULL, summary TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (repo, number))"""
    )
    # Every PR the factory opened (build and design), tracked for merge conflicts whether or not CI is watched. A separate
    # table: `prs` is written positionally and feeds the CI watcher and the reviewer.
    db.execute(
        """CREATE TABLE IF NOT EXISTS pr_conflicts (
            repo TEXT NOT NULL, number INTEGER NOT NULL, issue_repo TEXT NOT NULL, issue_num INTEGER NOT NULL,
            state TEXT NOT NULL DEFAULT 'unknown', notified_sha TEXT NOT NULL DEFAULT '',
            attempts INTEGER NOT NULL DEFAULT 0, detail TEXT NOT NULL DEFAULT '', updated REAL NOT NULL,
            PRIMARY KEY (repo, number))"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, stage TEXT, repo TEXT NOT NULL,
            issue INTEGER NOT NULL, title TEXT NOT NULL DEFAULT '', harness TEXT, model TEXT, effort TEXT,
            classification TEXT NOT NULL DEFAULT '', started REAL NOT NULL, finished REAL,
            status TEXT NOT NULL DEFAULT 'running', detail TEXT NOT NULL DEFAULT '',
            pr_urls TEXT NOT NULL DEFAULT '', output TEXT NOT NULL DEFAULT '', log_tail TEXT NOT NULL DEFAULT '')"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL, repo TEXT, issue INTEGER,
            run_id INTEGER, message TEXT NOT NULL)"""
    )
    db.execute("CREATE INDEX IF NOT EXISTS events_kind ON events(kind, id)")
    db.execute(
        """CREATE TABLE IF NOT EXISTS status (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated REAL NOT NULL)"""
    )
    return db


def seen(db, repo: str, issue: int, updated_at: str) -> bool:
    return db.execute(
        "SELECT 1 FROM decisions WHERE repo=? AND issue=? AND updated_at=?",
        (repo, issue, updated_at),
    ).fetchone() is not None


def record(db, repo: str, issue: int, updated_at: str, outcome: str, detail: str) -> None:
    db.execute(
        "INSERT OR REPLACE INTO decisions VALUES (?,?,?,?,?,?)",
        (repo, issue, updated_at, outcome, detail, time.time()),
    )
    db.commit()


def pop_approvals(db) -> list[tuple[str, int, str]]:
    rows = db.execute("SELECT repo, issue, action FROM approvals ORDER BY created").fetchall()
    db.execute("DELETE FROM approvals")
    db.commit()
    return rows


def watch_pr(db, repo: str, number: int, issue_repo: str, issue_num: int) -> None:
    now = time.time()
    db.execute("INSERT OR REPLACE INTO prs VALUES (?,?,?,?,?,?,?,?,?)",
               (repo, number, issue_repo, issue_num, "watching", 0, now, now, ""))
    db.commit()


def watching(db) -> list[tuple]:
    return db.execute("SELECT repo, number, issue_repo, issue_num, rounds, watch_started, summary FROM prs "
                      "WHERE status='watching' ORDER BY watch_started").fetchall()


def update_pr(db, repo: str, number: int, **fields) -> None:
    allowed = {"status", "rounds", "watch_started", "summary"}
    assert set(fields) <= allowed
    sets = ", ".join(f"{k}=?" for k in fields) + ", updated=?"
    db.execute(f"UPDATE prs SET {sets} WHERE repo=? AND number=?", (*fields.values(), time.time(), repo, number))
    db.commit()


# state: unknown | clean | conflicting | needs-person | closed. notified_sha: the head last reported as conflicting.
def track_pr(db, repo: str, number: int, issue_repo: str, issue_num: int) -> None:
    db.execute("INSERT OR IGNORE INTO pr_conflicts (repo, number, issue_repo, issue_num, updated) VALUES (?,?,?,?,?)",
               (repo, number, issue_repo, issue_num, time.time()))
    db.commit()


def tracked_prs(db) -> list[dict]:
    return _dicts(db.execute("SELECT repo, number, issue_repo, issue_num, state, notified_sha, attempts FROM pr_conflicts "
                             "WHERE state != 'closed' ORDER BY updated"))


def conflicts_for_issue(db, issue_repo: str, issue_num: int) -> list[dict]:
    return _dicts(db.execute("SELECT repo, number, state, notified_sha, attempts FROM pr_conflicts "
                             "WHERE issue_repo=? AND issue_num=? AND state != 'closed' ORDER BY repo, number", (issue_repo, issue_num)))


def update_conflict(db, repo: str, number: int, **fields) -> None:
    allowed = {"state", "notified_sha", "attempts", "detail"}
    assert set(fields) <= allowed
    sets = ", ".join(f"{k}=?" for k in fields) + ", updated=?"
    db.execute(f"UPDATE pr_conflicts SET {sets} WHERE repo=? AND number=?", (*fields.values(), time.time(), repo, number))
    db.commit()


# ---- observability: runs, events, heartbeat (read by the UI) ----
MAX_OUTPUT, MAX_LOG, KEEP_EVENTS = 60000, 8000, 20000


def _dicts(cur) -> list[dict]:
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def start_run(db, kind: str, repo: str, issue: int, title: str, harness: str | None, model: str | None,
              effort: str | None, classification: str = "", stage: str | None = None) -> int:
    cur = db.execute(
        "INSERT INTO runs (kind, stage, repo, issue, title, harness, model, effort, classification, started) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)", (kind, stage, repo, issue, title[:300], harness, model, effort, classification, time.time()))
    db.commit()
    return cur.lastrowid


def finish_run(db, run_id: int, status: str, detail: str = "", pr_urls: str = "", output: str = "", log_tail: str = "") -> None:
    db.execute("UPDATE runs SET finished=?, status=?, detail=?, pr_urls=?, output=?, log_tail=? WHERE id=?",
               (time.time(), status, detail[:1000], pr_urls, output[:MAX_OUTPUT], log_tail[-MAX_LOG:], run_id))
    db.commit()


def mark_interrupted(db) -> int:
    """Runs still marked running when a new orchestrator starts were killed with the old one."""
    cur = db.execute("UPDATE runs SET status='interrupted', finished=?, detail='orchestrator restarted' WHERE status='running'", (time.time(),))
    db.commit()
    return cur.rowcount


def add_event(db, kind: str, message: str, repo: str | None = None, issue: int | None = None, run_id: int | None = None) -> None:
    cur = db.execute("INSERT INTO events (ts, kind, repo, issue, run_id, message) VALUES (?,?,?,?,?,?)",
                     (time.time(), kind, repo, issue, run_id, message[:2000]))
    if cur.lastrowid % 500 == 0:                      # keep the timeline bounded
        db.execute("DELETE FROM events WHERE id < ?", (cur.lastrowid - KEEP_EVENTS,))
    db.commit()


def set_status(db, key: str, value: str) -> None:
    db.execute("INSERT INTO status (key, value, updated) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
               (key, value, time.time()))
    db.commit()


def get_status(db) -> dict:
    return {k: {"value": v, "updated": u} for k, v, u in db.execute("SELECT key, value, updated FROM status")}


def recent_runs(db, limit: int = 50, offset: int = 0, status: str | None = None, repo: str | None = None) -> list[dict]:
    where, args = [], []
    if status:
        where.append("status=?"); args.append(status)
    if repo:
        where.append("repo=?"); args.append(repo)
    sql = ("SELECT id, kind, stage, repo, issue, title, harness, model, effort, started, finished, status, detail, pr_urls "
           "FROM runs" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ? OFFSET ?")
    return _dicts(db.execute(sql, (*args, limit, offset)))


def get_run(db, run_id: int) -> dict | None:
    rows = _dicts(db.execute("SELECT * FROM runs WHERE id=?", (run_id,)))
    return rows[0] if rows else None


def recent_events(db, limit: int = 100, kind_prefix: str | None = None, before_id: int | None = None) -> list[dict]:
    where, args = [], []
    if kind_prefix:
        where.append("kind LIKE ?"); args.append(kind_prefix + "%")
    if before_id:
        where.append("id < ?"); args.append(before_id)
    sql = "SELECT id, ts, kind, repo, issue, run_id, message FROM events" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
    return _dicts(db.execute(sql, (*args, limit)))


def latest_decisions(db, limit: int = 100) -> list[dict]:
    return _dicts(db.execute("SELECT repo, issue, outcome, detail, decided_at FROM decisions ORDER BY decided_at DESC LIMIT ?", (limit,)))


def watched_prs(db, limit: int = 100) -> list[dict]:
    return _dicts(db.execute("SELECT repo, number, issue_repo, issue_num, status, rounds, watch_started, updated, summary "
                             "FROM prs ORDER BY updated DESC LIMIT ?", (limit,)))


def tickets(db, limit: int = 100) -> list[dict]:
    """One row per ticket the factory has looked at: its latest decision and how many runs it has had."""
    return _dicts(db.execute(
        """SELECT d.repo, d.issue, d.outcome, d.detail, d.decided_at,
                  (SELECT COUNT(*) FROM runs r WHERE r.repo=d.repo AND r.issue=d.issue) AS runs,
                  (SELECT r.status FROM runs r WHERE r.repo=d.repo AND r.issue=d.issue ORDER BY r.id DESC LIMIT 1) AS last_run,
                  (SELECT r.title FROM runs r WHERE r.repo=d.repo AND r.issue=d.issue ORDER BY r.id DESC LIMIT 1) AS title
           FROM decisions d
           WHERE d.decided_at = (SELECT MAX(d2.decided_at) FROM decisions d2 WHERE d2.repo=d.repo AND d2.issue=d.issue)
           GROUP BY d.repo, d.issue ORDER BY d.decided_at DESC LIMIT ?""", (limit,)))


def prs_for_issue(db, issue_repo: str, issue_num: int) -> list[tuple[str, int]]:
    """Open factory PRs raised for a ticket (the ones a review should cover)."""
    return [(r, n) for r, n in db.execute(
        "SELECT repo, number FROM prs WHERE issue_repo=? AND issue_num=? AND status != 'closed' ORDER BY number", (issue_repo, issue_num))]
