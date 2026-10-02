import sqlite3
import time


def connect(path: str) -> sqlite3.Connection:
    db = sqlite3.connect(path)
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
