"""Refresh package lockfiles on a worker for a second build (waves.py), so the PR installs the packages that were just published.

The agent has no network, so it can change a version in package.json but cannot write the lockfile entry (that needs the registry's
integrity hash). After the agent's patch is validated and applied, and only for a repo listed in [[workers.lockfiles]], the
orchestrator sends a worker ONLY the manifest and lockfile part of that patch, and a job naming the published packages. The worker
runs its own recipe (worker/recipes/lockfile-update.sh: `pnpm install --lockfile-only`, then `pnpm update` of those packages, or
the npm equivalents; never the repo's scripts) and returns the diff it made. That diff is untrusted: it may only touch package.json
and lockfiles, and it goes through the same patch checks as the agent's before it joins the commit. It is advisory: when it cannot
be done (no worker, a registry error), the PR is opened anyway and says the lockfile still needs refreshing."""
import json
import logging
import time

from . import db as dbm
from . import jobs
from .config import Config, LockfileJob, Project

log = logging.getLogger("factory.lockfiles")
MANIFESTS = ("package.json",)
LOCKS = ("pnpm-lock.yaml", "package-lock.json", "npm-shrinkwrap.json")
POLL_SECONDS = 2.0


def job_for(cfg: Config, repo: str) -> LockfileJob | None:
    return next((j for j in cfg.workers.lockfiles if j.repo == repo), None) if cfg.workers.enabled else None


def packages_for(project: Project, repo: str) -> list[str]:
    """The packages published by the repos `repo` depends on."""
    me = project.repo(repo)
    out = []
    for dep in (me.depends_on if me else ()):
        pub = project.repo(dep).publish if project.repo(dep) else None
        out += list(pub.packages) if pub else []
    return list(dict.fromkeys(out))


def allowed(path: str) -> bool:
    return path.rsplit("/", 1)[-1] in MANIFESTS + LOCKS


def only_manifests(patch: str) -> str:
    """The sections of a git diff that change package manifests or lockfiles, and nothing else."""
    out, keep = [], False
    for line in patch.splitlines(keepends=True):
        if line.startswith("diff --git "):
            parts = line.rstrip("\n").split(" ")
            keep = len(parts) == 4 and all(p[2:] and allowed(p[2:]) for p in parts[2:])
        if keep:
            out.append(line)
    return "".join(out)


def dirs_of(patch: str) -> list[str]:
    """The folders whose package.json the patch changes ('' is the repository root)."""
    out = []
    for line in patch.splitlines():
        parts = line.split(" ")
        if line.startswith("diff --git ") and len(parts) == 4 and parts[3].startswith("b/") and parts[3].rsplit("/", 1)[-1] == "package.json":
            out.append(parts[3][2:].rpartition("/")[0])
    return list(dict.fromkeys(out))


def request(cfg: Config, repo: str, issue: int, base_sha: str, agent_patch: str, packages: list[str], db=None,
            sleep=time.sleep, clock=time.time) -> tuple[str, str]:
    """Run the lockfile job for `repo` and wait for it. (diff, note): the worker's diff ('' when nothing changed or it failed) and one
    line for the PR. The diff is NOT validated here; the caller applies it with the usual patch checks."""
    lf, w = job_for(cfg, repo), cfg.workers
    if lf is None:
        return "", ""
    db = db or dbm.local(cfg.db_path)
    manifest = only_manifests(agent_patch)
    params = json.dumps({"purpose": jobs.LOCKFILE_PURPOSE, "packages": packages, "dirs": dirs_of(manifest)})
    jid = jobs.enqueue(db, repo, issue, base_sha, manifest, lf.recipe, lf.platform, clock(), params)
    deadline = clock() + w.max_wait_seconds
    while True:
        jobs.expire_stale(db, clock(), w.lease_seconds, w.claim_wait_seconds, w.max_attempts)
        job = jobs.get(db, jid)
        if job and job["status"] in jobs.FINAL:
            break
        if clock() >= deadline:
            jobs.cancel(db, jid, clock(), f"The orchestrator stopped waiting after {w.max_wait_seconds}s.")
            return "", f"the lockfile was not refreshed: the worker did not finish within {w.max_wait_seconds}s"
        sleep(POLL_SECONDS)
    where = f" on {job['worker']}" if job["worker"] else ""
    if job["status"] != "passed":
        return "", f"the lockfile was not refreshed ({job['status']}{where}; the log is on the Workers page, job {int(jid)})"
    if not job["result_patch"].strip():
        return "", f"the lockfile was already up to date (checked{where})"
    return job["result_patch"], f"the lockfile was refreshed{where} for the published packages"
