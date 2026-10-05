"""Updating verification workers: which worker should move to which release, decided by the factory.

A worker always follows the release the factory itself runs, so they stay compatible. It asks the worker API after a quiet while (see
worker/worker.py); the answer is yes when the worker is behind and either a person pressed Update worker for it or automatic worker updates
are on (state/AUTO_UPDATE_WORKERS). The worker then fetches the release files through the API (factory/releasefiles.py)."""
import re
from pathlib import Path

from . import updates

AUTO_FILE = "AUTO_UPDATE_WORKERS"
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,40}")


def auto(state_dir) -> bool:
    return (Path(state_dir) / AUTO_FILE).is_file()


def set_auto(state_dir, on: bool) -> None:
    p = Path(state_dir) / AUTO_FILE
    if on:
        p.write_text("1\n")
    else:
        p.unlink(missing_ok=True)


def request(db, worker: str, now: float) -> bool:
    """Record a person's Update worker press. False for a name that is not a worker."""
    if not NAME.fullmatch(worker or "") or not db.execute("SELECT 1 FROM workers WHERE name=?", (worker,)).fetchone():
        return False
    db.execute("INSERT OR REPLACE INTO worker_updates (worker, requested) VALUES (?,?)", (worker, now))
    db.commit()
    return True


def requested(db, worker: str) -> bool:
    return db.execute("SELECT 1 FROM worker_updates WHERE worker=?", (worker,)).fetchone() is not None


def behind(app_version: str, factory_version: str) -> bool:
    """True when the worker reports a release older than the factory's. A worker that reports none (before this feature) cannot self-update."""
    return bool(app_version) and updates.newer(factory_version, app_version)


def wanted(db, state_dir, worker: str, app_version: str, factory_version: str) -> str:
    """The release tag `worker` should update to now, or ''. Clears a finished request."""
    if not behind(app_version, factory_version):
        if app_version and requested(db, worker):                    # it caught up
            db.execute("DELETE FROM worker_updates WHERE worker=?", (worker,))
            db.commit()
        return ""
    return "v" + factory_version if (requested(db, worker) or auto(state_dir)) else ""
