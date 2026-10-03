"""The verification-job queue shared by the orchestrator (which enqueues and waits) and the worker API (which hands jobs to workers).

Both sides use the same SQLite file (WAL), so they can be separate processes. Everything a worker sends back is untrusted and is
validated here before it is stored; the orchestrator reads only the stored, validated verdict. See docs/workers.md."""
import base64
import binascii
import re
import sqlite3
import time

from .render import png_ok

STATUSES = ("queued", "claimed", "passed", "failed", "error", "cancelled")
FINAL = ("passed", "failed", "error", "cancelled")
REPORTED = ("passed", "failed", "error")          # what a worker may report
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,60}")
MAX_LOG = 200_000
MAX_ARTIFACTS = 8
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def ensure_tables(db: sqlite3.Connection) -> None:
    db.execute(
        """CREATE TABLE IF NOT EXISTS verify_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, repo TEXT NOT NULL, issue INTEGER NOT NULL DEFAULT 0, base_sha TEXT NOT NULL,
            patch TEXT NOT NULL, recipe TEXT NOT NULL, platform TEXT NOT NULL DEFAULT 'any',
            status TEXT NOT NULL DEFAULT 'queued', worker TEXT, attempts INTEGER NOT NULL DEFAULT 0,
            created REAL NOT NULL, claimed REAL, heartbeat REAL, finished REAL, exit_code INTEGER, log TEXT NOT NULL DEFAULT '')"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS verify_artifacts (
            job_id INTEGER NOT NULL, name TEXT NOT NULL, png BLOB NOT NULL, PRIMARY KEY (job_id, name))"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS workers (
            name TEXT PRIMARY KEY, platform TEXT NOT NULL DEFAULT '', recipes TEXT NOT NULL DEFAULT '', last_seen REAL NOT NULL,
            version INTEGER NOT NULL DEFAULT 1)"""
    )


def _row(cur, row) -> dict:
    return dict(zip([c[0] for c in cur.description], row))


def get(db, job_id: int) -> dict | None:
    cur = db.execute("SELECT * FROM verify_jobs WHERE id=?", (job_id,))
    row = cur.fetchone()
    return _row(cur, row) if row else None


def artifacts(db, job_id: int) -> dict[str, bytes]:
    return {n: bytes(b) for n, b in db.execute("SELECT name, png FROM verify_artifacts WHERE job_id=? ORDER BY name", (job_id,))}


def enqueue(db, repo: str, issue: int, base_sha: str, patch: str, recipe: str, platform: str = "any", now: float | None = None) -> int:
    cur = db.execute(
        "INSERT INTO verify_jobs (repo, issue, base_sha, patch, recipe, platform, created) VALUES (?,?,?,?,?,?,?)",
        (repo, int(issue), base_sha, patch, recipe, platform, now if now is not None else time.time()))
    db.commit()
    return cur.lastrowid


def touch_worker(db, name: str, platform: str, recipes: list[str], version: int, now: float) -> None:
    db.execute("INSERT OR REPLACE INTO workers VALUES (?,?,?,?,?)", (name, platform, ",".join(recipes), now, version))


def online_workers(db, now: float, within: float) -> list[dict]:
    cur = db.execute("SELECT * FROM workers WHERE last_seen >= ? ORDER BY name", (now - within,))
    return [_row(cur, r) for r in cur.fetchall()]


def expire_stale(db, now: float, lease: int, claim_wait: int, max_attempts: int) -> int:
    """Put jobs whose worker went quiet back in the queue (or fail them after max_attempts), and fail jobs nobody claimed in time."""
    n = 0
    for jid, attempts in db.execute("SELECT id, attempts FROM verify_jobs WHERE status='claimed' AND heartbeat < ?", (now - lease,)).fetchall():
        if attempts >= max_attempts:
            db.execute("UPDATE verify_jobs SET status='error', finished=?, log=log || ? WHERE id=?",
                       (now, "\nThe worker stopped reporting and the job ran out of attempts.", jid))
        else:
            db.execute("UPDATE verify_jobs SET status='queued', worker=NULL, claimed=NULL, heartbeat=NULL WHERE id=?", (jid,))
        n += 1
    cur = db.execute("UPDATE verify_jobs SET status='error', finished=?, log=? WHERE status='queued' AND created < ?",
                     (now, f"No worker claimed this job within {claim_wait}s (is a worker running with this recipe and platform?).",
                      now - claim_wait))
    db.commit()
    return n + cur.rowcount


def claim(db, worker: str, platform: str, recipes: list[str], now: float, lease: int, claim_wait: int, max_attempts: int) -> dict | None:
    """Atomically hand the oldest matching queued job to `worker`. A job for platform 'any' matches every worker."""
    db.execute("BEGIN IMMEDIATE")
    try:
        touch_worker(db, worker, platform, recipes, 1, now)
        expire_stale(db, now, lease, claim_wait, max_attempts)
        if not recipes:
            db.commit()
            return None
        marks = ",".join("?" * len(recipes))
        cur = db.execute(f"SELECT * FROM verify_jobs WHERE status='queued' AND recipe IN ({marks}) AND platform IN ('any', ?) "
                         "ORDER BY id LIMIT 1", (*recipes, platform))
        row = cur.fetchone()
        job = _row(cur, row) if row else None
        if job:
            db.execute("UPDATE verify_jobs SET status='claimed', worker=?, attempts=attempts+1, claimed=?, heartbeat=? WHERE id=?",
                       (worker, now, now, job["id"]))
        db.commit()
        return job
    except Exception:
        db.rollback()
        raise


def heartbeat(db, job_id: int, worker: str, now: float) -> str:
    """'ok', 'cancel' (stop working) or 'gone' (not this worker's job). Only the claiming worker may touch a job."""
    job = get(db, job_id)
    if not job or job["worker"] != worker:
        return "gone"
    if job["status"] == "cancelled":
        return "cancel"
    if job["status"] != "claimed":
        return "gone"
    db.execute("UPDATE verify_jobs SET heartbeat=? WHERE id=?", (now, job_id))
    db.commit()
    return "ok"


def cancel(db, job_id: int, now: float, why: str = "cancelled by the orchestrator") -> None:
    db.execute("UPDATE verify_jobs SET status='cancelled', finished=?, log=log || ? WHERE id=? AND status IN ('queued','claimed')",
               (now, why, job_id))
    db.commit()


def clean_log(text) -> str:
    """Worker output is untrusted text: capped (keeping the end, where failures are), control characters removed."""
    text = text if isinstance(text, str) else ""
    return _CONTROL.sub("", text[-MAX_LOG:])


def validate_result(body) -> tuple[dict | None, str]:
    """(clean result, '') or (None, why). Strict: a fixed status set, a whole-number exit code, a capped log, validated PNGs."""
    if not isinstance(body, dict):
        return None, "the result must be a JSON object"
    status = body.get("status")
    if status not in REPORTED:
        return None, f"status must be one of {', '.join(REPORTED)}"
    code = body.get("exit_code")
    if code is not None and (not isinstance(code, int) or isinstance(code, bool) or not -1000 <= code <= 1000):
        return None, "exit_code must be a whole number or null"
    arts = body.get("artifacts", [])
    if not isinstance(arts, list) or len(arts) > MAX_ARTIFACTS:
        return None, f"at most {MAX_ARTIFACTS} artifacts"
    out: dict[str, bytes] = {}
    for a in arts:
        if not isinstance(a, dict) or not isinstance(a.get("name"), str) or not NAME.fullmatch(a["name"]) or a["name"] in out:
            return None, "artifact names must be unique and match [a-z0-9-]"
        try:
            png = base64.b64decode(a.get("png_b64", ""), validate=True)
        except (binascii.Error, ValueError, TypeError):
            return None, f"artifact {a['name']}: not valid base64"
        if not png_ok(png):
            return None, f"artifact {a['name']}: not a PNG within the size limits"
        out[a["name"]] = png
    return {"status": status, "exit_code": code, "log": clean_log(body.get("log")), "artifacts": out}, ""


def complete(db, job_id: int, worker: str, body, now: float) -> str:
    """Store a worker's result. '' on success, else why it was refused. An invalid result fails the job (closed)."""
    job = get(db, job_id)
    if not job or job["worker"] != worker or job["status"] != "claimed":
        return "not your job, or it is no longer running"
    res, why = validate_result(body)
    if res is None:
        db.execute("UPDATE verify_jobs SET status='error', finished=?, log=? WHERE id=?",
                   (now, f"The worker sent an invalid result: {why}", job_id))
        db.commit()
        return why
    db.execute("UPDATE verify_jobs SET status=?, exit_code=?, log=?, finished=? WHERE id=?",
               (res["status"], res["exit_code"], res["log"], now, job_id))
    for n, png in res["artifacts"].items():
        db.execute("INSERT OR REPLACE INTO verify_artifacts VALUES (?,?,?)", (job_id, n, png))
    db.commit()
    return ""


def recent(db, limit: int = 30) -> list[dict]:
    cur = db.execute("SELECT id, repo, issue, base_sha, recipe, platform, status, worker, attempts, created, claimed, finished, exit_code "
                     "FROM verify_jobs ORDER BY id DESC LIMIT ?", (limit,))
    return [_row(cur, r) for r in cur.fetchall()]
