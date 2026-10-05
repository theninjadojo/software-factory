"""Playwright runs for the Screens board: a person presses Build screens, a worker runs the project's Playwright suite on the default
branch, and the screenshots it took appear on the board under the project.

Nothing runs here. The UI records a request; at its next poll the orchestrator turns each request into one job for the worker recipe
named in [[screens.captures]] (a recipe lives on the worker, never in a request or a repo); the worker returns PNGs, which jobs.py
validates like every other result. The board reads the newest job of each repo. A request for a repo that already has a job queued or
running adds nothing, so pressing the button twice cannot pile up runs."""
import json
import logging
import re
import sqlite3
import time

from . import jobs

log = logging.getLogger("factory.captures")
KEEP_RUNS = 3                                       # finished runs kept per repo: the newest is shown, the others let you compare
REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}")


def ensure_tables(db: sqlite3.Connection) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS capture_requests (repo TEXT PRIMARY KEY, created REAL NOT NULL)")


def request(db, repos: list[str], now: float | None = None) -> None:
    ensure_tables(db)
    for r in repos:
        db.execute("INSERT OR REPLACE INTO capture_requests (repo, created) VALUES (?,?)", (r, now if now is not None else time.time()))
    db.commit()


def requested(db) -> set[str]:
    try:
        return {r[0] for r in db.execute("SELECT repo FROM capture_requests")}
    except sqlite3.OperationalError:
        return set()


def _screens_jobs(db, repo: str, limit: int) -> list[dict]:
    try:
        cur = db.execute("SELECT * FROM verify_jobs WHERE repo=? AND params != '' ORDER BY id DESC LIMIT 200", (repo,))
        rows = [dict(zip([c[0] for c in cur.description], r)) for r in cur.fetchall()]
    except sqlite3.OperationalError:
        return []
    return [j for j in rows if jobs.is_screens(j)][:limit]


def active(db, repo: str) -> dict | None:
    """The queued or running Playwright job of `repo`, if any."""
    return next((j for j in _screens_jobs(db, repo, 20) if j["status"] in ("queued", "claimed")), None)


def latest(db, repo: str) -> dict | None:
    """The newest Playwright job of `repo` that finished with screenshots or an answer, with its screenshot names; else the active one."""
    for j in _screens_jobs(db, repo, 20):
        if j["status"] in jobs.FINAL and j["status"] != "cancelled":
            return dict(j, shots=sorted(jobs.artifacts(db, j["id"])))
    return None


def tick(cfg, gh, db, now: float) -> int:
    """Turn requests into worker jobs and prune old runs. Returns how many jobs were queued."""
    ensure_tables(db)
    caps = {c.repo: c for c in cfg.screens.captures}
    queued = 0
    for repo in sorted(requested(db)):
        db.execute("DELETE FROM capture_requests WHERE repo=?", (repo,))
        db.commit()
        cap = caps.get(repo)
        if not cap or not cfg.workers.enabled or active(db, repo):
            continue
        try:
            sha = gh.branch_sha(repo, gh.default_branch(repo))
            jobs.enqueue(db, repo, 0, sha, "", cap.recipe, cap.platform, now, json.dumps({"purpose": jobs.SCREENS_PURPOSE}))
            queued += 1
            log.info("screens: queued a Playwright run of %s at %s", repo, sha[:7])
        except Exception:
            log.exception("screens: could not queue a Playwright run of %s", repo)
    for repo in caps:
        finished = [j["id"] for j in _screens_jobs(db, repo, 200) if j["status"] in jobs.FINAL]
        for jid in finished[KEEP_RUNS:]:
            db.execute("DELETE FROM verify_artifacts WHERE job_id=?", (jid,))
            db.execute("DELETE FROM verify_jobs WHERE id=?", (jid,))
    db.commit()
    return queued
