#!/usr/bin/env python3
"""Reference verification worker for software-factory (docs/workers.md). Standard library only; runs on macOS or Linux.

It polls the orchestrator's worker API, and for each job: clones the repo at the base commit with *this machine's* git credentials,
applies the (already validated) patch, runs the recipe of that name from *this machine's* config, and posts the result. The job never
carries a command: only recipes defined in worker.toml can run.

The recipe runs agent-written code on a machine with a network. Use a dedicated unprivileged account or a throwaway VM, and keep
credentials off it (see docs/workers.md, "Security model").

    python3 worker.py --config worker.toml
"""
import argparse
import base64
import glob
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("factory.worker")
PROTOCOL = 1
REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}/[A-Za-z0-9][A-Za-z0-9._-]{0,99}")
SHA = re.compile(r"[0-9a-f]{40}")
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,60}")
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAX_PNG, MAX_ARTIFACTS, MAX_LOG = 3_000_000, 8, 150_000
MAX_PATCH = 4_000_000


class Recipe:
    def __init__(self, name: str, raw: dict):
        cmd = raw.get("command")
        if not isinstance(cmd, list) or not cmd or not all(isinstance(c, str) and c for c in cmd):
            raise ValueError(f"recipe {name}: command must be a non-empty list of strings (an argv, no shell)")
        self.name, self.command = name, cmd
        self.timeout = int(raw.get("timeout_seconds", 1800))
        self.artifacts = [str(a) for a in raw.get("artifacts", [])]
        self.env_passthrough = [str(e) for e in raw.get("env_passthrough", [])]
        if not 10 <= self.timeout <= 86400:
            raise ValueError(f"recipe {name}: timeout_seconds must be from 10 to 86400")
        if any(a.startswith("/") or ".." in Path(a).parts for a in self.artifacts):
            raise ValueError(f"recipe {name}: artifacts must be relative globs inside the checkout")


class Config:
    def __init__(self, raw: dict):
        self.server = str(raw["server"]).rstrip("/")
        self.token = Path(raw["token_file"]).expanduser().read_text().strip()
        self.platform = str(raw["platform"])
        self.poll_seconds = float(raw.get("poll_seconds", 5))
        self.git_url = str(raw.get("git_url", "https://github.com/{repo}.git"))
        self.work_dir = Path(raw.get("work_dir", tempfile.gettempdir())).expanduser()
        self.recipes = {n: Recipe(n, r) for n, r in raw.get("recipes", {}).items()}
        if not NAME.fullmatch(self.platform):
            raise ValueError("platform must match [a-z0-9-]")
        for n in self.recipes:
            if not NAME.fullmatch(n):
                raise ValueError(f"recipe name {n!r} must match [a-z0-9-]")


def load_config(path: str) -> Config:
    with open(path, "rb") as f:
        return Config(tomllib.load(f))


class Api:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def call(self, path: str, body: dict, timeout: int = 60) -> tuple[int, dict | None]:
        req = urllib.request.Request(self.cfg.server + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {self.cfg.token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"null")
            except ValueError:
                return e.code, None


def _git(args: list[str], cwd, stdin: str | None = None, timeout: int = 600):
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/tmp"), "GIT_TERMINAL_PROMPT": "0"}
    return subprocess.run(["git", "-c", "core.hooksPath=/dev/null", "-c", "protocol.ext.allow=never", *args], cwd=cwd, env=env,
                          input=stdin, capture_output=True, text=True, timeout=timeout)


def prepare(cfg: Config, job: dict, dest: Path) -> str:
    """Clone, check out the base commit, apply the patch. '' on success, else why not (reported as an error, not a test failure)."""
    repo, sha, patch = job.get("repo"), job.get("base_sha"), job.get("patch")
    if not (isinstance(repo, str) and REPO.fullmatch(repo) and isinstance(sha, str) and SHA.fullmatch(sha)
            and isinstance(patch, str) and len(patch) <= MAX_PATCH):
        return "the job is malformed"
    for args, stdin in ((["clone", "--quiet", "--no-checkout", cfg.git_url.format(repo=repo), str(dest)], None),):
        r = _git(args, None, stdin)
        if r.returncode:
            return f"git clone failed: {r.stderr[-400:]}"
    for args, stdin in ((["checkout", "--quiet", sha], None), (["apply", "--whitespace=nowarn", "-"], patch)):
        r = _git(args, dest, stdin)
        if r.returncode:
            return f"git {args[0]} failed: {r.stderr[-400:]}"
    return ""


def collect_artifacts(recipe: Recipe, root: Path) -> list[dict]:
    """PNGs matching the recipe's globs, inside the checkout, not symlinks, size-limited. The orchestrator validates them again."""
    out, seen = [], set()
    for pattern in recipe.artifacts:
        for f in sorted(glob.glob(pattern, root_dir=root, recursive=True)):
            p = root / f
            name = re.sub(r"[^a-z0-9-]+", "-", p.stem.lower()).strip("-")[:60]
            if (len(out) >= MAX_ARTIFACTS or p.is_symlink() or not p.is_file() or not NAME.fullmatch(name or "-") or name in seen
                    or p.stat().st_size > MAX_PNG):
                continue
            data = p.read_bytes()
            if not data.startswith(PNG_MAGIC) or not str(p.resolve()).startswith(str(root.resolve()) + os.sep):
                continue
            seen.add(name)
            out.append({"name": name, "png_b64": base64.b64encode(data).decode()})
    return out


def run_recipe(recipe: Recipe, cwd: Path, logfile: Path, cancelled: threading.Event) -> tuple[int | None, str]:
    """Run the recipe's argv with a minimal environment. (exit code or None, note). Kills the whole process group on timeout or cancel."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/tmp"), "LANG": "en_US.UTF-8", "CI": "true"}
    env.update({k: os.environ[k] for k in recipe.env_passthrough if k in os.environ})
    with open(logfile, "wb") as out:
        proc = subprocess.Popen(recipe.command, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                start_new_session=True)
        deadline = time.monotonic() + recipe.timeout
        while proc.poll() is None:
            if cancelled.is_set() or time.monotonic() > deadline:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                return None, "cancelled by the orchestrator" if cancelled.is_set() else f"timed out after {recipe.timeout}s"
            time.sleep(0.5)
        return proc.returncode, ""


def read_tail(path: Path) -> str:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - MAX_LOG))
            return f.read().decode(errors="replace")
    except OSError:
        return ""


def execute(cfg: Config, api: Api, job: dict) -> dict | None:
    """Do one job and return the result body (None when the orchestrator cancelled it)."""
    recipe = cfg.recipes.get(job.get("recipe"))
    if recipe is None:
        return {"status": "error", "exit_code": None, "log": f"this worker has no recipe named {job.get('recipe')!r}"}
    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="factory-job-", dir=cfg.work_dir))
    cancelled, done = threading.Event(), threading.Event()

    def beat():
        every = max(5, int(job.get("lease_seconds", 120)) // 3)
        while not done.wait(every):
            try:
                status, reply = api.call(f"/v1/jobs/{job['id']}/heartbeat", {}, 30)
                if status == 404 or (reply or {}).get("cancel"):
                    cancelled.set()
            except OSError:
                log.warning("heartbeat failed; will retry")
    threading.Thread(target=beat, daemon=True).start()
    try:
        checkout, logfile = tmp / "src", tmp / "recipe.log"
        problem = prepare(cfg, job, checkout)
        if problem:
            return {"status": "error", "exit_code": None, "log": problem}
        code, note = run_recipe(recipe, checkout, logfile, cancelled)
        if cancelled.is_set():
            return None
        text = read_tail(logfile) + (f"\n[worker] {note}" if note else "")
        return {"status": "passed" if code == 0 else "failed", "exit_code": code, "log": text,
                "artifacts": collect_artifacts(recipe, checkout)}
    except Exception as e:                                   # a worker bug is an error, never a verdict on the patch
        log.exception("job failed")
        return {"status": "error", "exit_code": None, "log": f"worker error: {type(e).__name__}: {e}"}
    finally:
        done.set()
        shutil.rmtree(tmp, ignore_errors=True)


def poll_once(cfg: Config, api: Api) -> bool:
    """Claim and run at most one job. True if one ran."""
    status, job = api.call("/v1/claim", {"platform": cfg.platform, "recipes": sorted(cfg.recipes), "version": PROTOCOL}, 30)
    if status != 200 or not job:
        if status not in (200, 204):
            log.warning("claim refused (%s): %s", status, job)
        return False
    log.info("job %s: %s on %s", job["id"], job["recipe"], job["repo"])
    result = execute(cfg, api, job)
    if result is not None:
        status, reply = api.call(f"/v1/jobs/{job['id']}/result", result, 300)
        log.info("job %s reported %s (HTTP %s)", job["id"], result["status"], status)
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description="software-factory verification worker")
    ap.add_argument("--config", required=True)
    ap.add_argument("--once", action="store_true", help="claim at most one job, then exit")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = load_config(args.config)
    api = Api(cfg)
    log.info("worker up: platform=%s recipes=%s server=%s", cfg.platform, sorted(cfg.recipes), cfg.server)
    while True:
        try:
            ran = poll_once(cfg, api)
        except (OSError, ValueError) as e:
            log.warning("server unreachable: %s", e)
            ran = False
        if args.once:
            return
        if not ran:
            time.sleep(cfg.poll_seconds)


if __name__ == "__main__":
    main()
