"""The verification gate: after an agent's patch is validated and applied (and the screen check passed), before anything is pushed, each
check configured for the repo ([[workers.checks]]) runs on a worker and must pass. See docs/workers.md.

Checks come from trusted config, never from the repo, the ticket or the agent. The orchestrator enqueues a job (repo, base commit,
the already-validated patch, a recipe *name*) and waits; the worker's reply is validated in jobs.py, and only that stored verdict is
used here. Fails closed: no worker, a timeout or an invalid result is a failed check (mode "warn" lets the caller push anyway)."""
import logging
import time
from dataclasses import dataclass, field

from . import db as dbm
from . import jobs
from .config import Config, WorkerCheck

log = logging.getLogger("factory.verify")
POLL_SECONDS = 2.0
LOG_TAIL = 1500


@dataclass
class Report:
    ok: bool = True                                        # every REQUIRED check passed
    fixable: bool = False                                  # a required check genuinely FAILED (not "could not run"): a fix round can help
    lines: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)      # advisory checks that did not pass: noted on the PR, never block
    images: list[dict] = field(default_factory=list)      # {kind: "verify", name, png}: screenshots the worker returned (validated)

    def fail(self, line: str) -> "Report":
        self.ok = False
        self.lines.append(line)
        return self

    def text(self) -> str:
        return "\n".join(self.lines)


def checks_for(cfg: Config, repo: str) -> list[WorkerCheck]:
    return [c for c in cfg.workers.checks if c.repo == repo] if cfg.workers.enabled else []


def _tail(text: str) -> str:
    text = (text or "").strip()
    return text[-LOG_TAIL:].replace("```", "'''")


def gate(cfg: Config, repo: str, issue: int, base_sha: str, patch: str, db=None, sleep=time.sleep, clock=time.time) -> Report:
    """Run every check for `repo` on workers, in parallel, and wait for them all. A repo with no checks passes untouched."""
    rep, checks, w = Report(), checks_for(cfg, repo), cfg.workers
    if not checks:
        return rep
    db = db or dbm.local(cfg.db_path)
    required = {c.recipe: c.required for c in checks}
    ids = {c.recipe: jobs.enqueue(db, repo, issue, base_sha, patch, c.recipe, c.platform, clock()) for c in checks}
    deadline = clock() + w.max_wait_seconds
    final: dict[str, dict] = {}
    while len(final) < len(ids):
        jobs.expire_stale(db, clock(), w.lease_seconds, w.claim_wait_seconds, w.max_attempts)
        for recipe, jid in ids.items():
            job = jobs.get(db, jid)
            if recipe not in final and job and job["status"] in jobs.FINAL:
                final[recipe] = job
        if len(final) == len(ids):
            break
        if clock() >= deadline:
            for recipe, jid in ids.items():
                if recipe not in final:
                    jobs.cancel(db, jid, clock(), f"The orchestrator stopped waiting after {w.max_wait_seconds}s.")
                    line = f"{repo}: check {recipe!r} did not finish within {w.max_wait_seconds}s"
                    rep.fail(line) if required[recipe] else rep.warnings.append(line)
            break
        sleep(POLL_SECONDS)
    failed_for_real: list[bool] = []
    for recipe, job in final.items():
        for name, png in jobs.artifacts(db, job["id"]).items():
            rep.images.append({"kind": "verify", "name": f"{recipe}-{name}", "png": png})
        where = f" on {job['worker']}" if job["worker"] else ""
        if job["status"] == "passed":
            rep.lines.append(f"{repo}: check {recipe!r} passed{where}")
        else:
            why = {"failed": "failed", "error": "could not run", "cancelled": "was cancelled"}[job["status"]]
            line = f"{repo}: check {recipe!r} {why}{where}.\n```\n{_tail(job['log'])}\n```"
            if required[recipe]:
                rep.fail(line)
                failed_for_real.append(job["status"] == "failed")
            else:
                rep.warnings.append(line)
    # A fix round only helps when every required problem is a real test failure: not a missing worker, a timeout or a worker error.
    rep.fixable = not rep.ok and bool(failed_for_real) and all(failed_for_real) and not any(
        recipe not in final and required[recipe] for recipe in ids)
    return rep


ONLINE_SECONDS = 120          # a worker that has not polled for this long is offline (workers poll every few seconds)
GRACE_SECONDS = 120           # jobs must have waited this long, with no worker online, before a person is told


def watch(cfg: Config, db, notify, now: float | None = None) -> None:
    """Called every poll. Tells a person once when verification jobs are waiting and no worker is online, and once when that clears,
    so a Mac that went to sleep does not silently stall builds until the claim timeout."""
    if not cfg.workers.enabled:
        return
    now = time.time() if now is None else now
    try:
        n, oldest = db.execute("SELECT COUNT(*), MIN(created) FROM verify_jobs WHERE status='queued'").fetchone()
        online = jobs.online_workers(db, now, ONLINE_SECONDS)
        flagged = (db.execute("SELECT value FROM status WHERE key='workers_alert'").fetchone() or ("0",))[0] == "1"
    except Exception:                                               # an older database without the tables
        return
    stuck = bool(n) and not online and now - oldest >= GRACE_SECONDS
    if stuck != flagged:
        dbm.set_status(db, "workers_alert", "1" if stuck else "0")
        notify(f"{n} verification job(s) are waiting and no worker has been seen for {ONLINE_SECONDS}s. Builds that need a check will fail "
               f"after {cfg.workers.claim_wait_seconds}s. Is the worker running?" if stuck else "A verification worker is back online.")
