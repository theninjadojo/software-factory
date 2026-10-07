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
MIN_PROGRESS_GAP = 5                      # seconds between progress updates (the orchestrator drops faster ones)
PROGRESS_MARK = "##progress "             # a recipe prints this at the start of a line to name what it is doing
MAX_SCREEN_ARTIFACTS, MAX_SCREEN_BYTES = 300, 40_000_000      # a Playwright run for the Screens board (params purpose "screens")
MIN_SIDE, MAX_W, MAX_H = 16, 1600, 6000          # the orchestrator rejects the WHOLE result for one PNG outside these (factory/render.py)
MAX_PATCH = 4_000_000


ROOT = Path(__file__).resolve().parent.parent          # the install folder: worker/, scripts/, VERSION, worker.toml
UPDATE_EVERY = 60                                       # seconds between "should I update?" questions while idle
UPDATE_RETRY = 3600                                     # after a failed update of a release, wait this long before trying it again
APP_VERSION = re.compile(r"\d{1,4}\.\d{1,4}\.\d{1,4}")


def app_version() -> str:
    """The release this install runs (its VERSION file), or '' for a development checkout."""
    try:
        v = (ROOT / "VERSION").read_text().strip()
    except OSError:
        return ""
    return v if APP_VERSION.fullmatch(v) else ""


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
        self.install_tools = bool(raw.get("install_tools", True))      # let the worker install its own Node when a JavaScript recipe lacks one
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
    steps = [(["checkout", "--quiet", sha], None)] + ([(["apply", "--whitespace=nowarn", "-"], patch)] if patch else [])   # a scan has no patch
    for args, stdin in steps:
        r = _git(args, dest, stdin)
        if r.returncode:
            return f"git {args[0]} failed: {r.stderr[-400:]}"
    return ""


LOCKFILE_PATHS = [f":(glob)**/{n}" for n in ("package.json", "pnpm-lock.yaml", "package-lock.json", "npm-shrinkwrap.json")]


def is_lockfile(job: dict) -> bool:
    return isinstance(job.get("params"), dict) and job["params"].get("purpose") == "lockfile"


def snapshot(dest: Path) -> str:
    """The checkout's whole tree as git sees it now (a tree id), so the recipe's own changes can be diffed afterwards."""
    for args in (["add", "-A"], ["write-tree"]):
        r = _git(args, dest)
        if r.returncode:
            raise RuntimeError(f"git {args[0]} failed: {r.stderr[-300:]}")
    return r.stdout.strip()


def lockfile_diff(dest: Path, before: str) -> str:
    """What a lockfile recipe changed, limited to package manifests and lockfiles (the orchestrator checks that again)."""
    after = snapshot(dest)
    r = _git(["diff", "--binary", before, after, "--", *LOCKFILE_PATHS], dest)
    if r.returncode:
        raise RuntimeError(f"git diff failed: {r.stderr[-300:]}")
    if len(r.stdout) > MAX_PATCH:
        raise RuntimeError("the lockfile change is too large to send")
    return r.stdout


def png_fits(data: bytes) -> bool:
    """The same size limits the orchestrator applies. One oversize full-page screenshot must not turn a passing run into an invalid result."""
    if len(data) < 33 or data[12:16] != b"IHDR":
        return False
    w, h = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    return MIN_SIDE <= w <= MAX_W and MIN_SIDE <= h <= MAX_H


def is_screens(job: dict) -> bool:
    return isinstance(job.get("params"), dict) and job["params"].get("purpose") == "screens"


def collect_artifacts(recipe: Recipe, root: Path, limit: int = MAX_ARTIFACTS, budget: int | None = None) -> list[dict]:
    """PNGs matching the recipe's globs, inside the checkout, not symlinks, size-limited. The orchestrator validates them again.
    `budget` caps the total bytes, so one huge suite cannot make a result the API refuses."""
    out, seen, used = [], set(), 0
    for pattern in recipe.artifacts:
        for f in sorted(glob.glob(pattern, root_dir=root, recursive=True)):
            p = root / f
            name = re.sub(r"[^a-z0-9-]+", "-", p.stem.lower()).strip("-")[:60]
            if (len(out) >= limit or p.is_symlink() or not p.is_file() or not NAME.fullmatch(name or "-") or name in seen
                    or p.stat().st_size > MAX_PNG):
                continue
            data = p.read_bytes()
            if not data.startswith(PNG_MAGIC) or not str(p.resolve()).startswith(str(root.resolve()) + os.sep) or not png_fits(data):
                continue
            if budget is not None and used + len(data) > budget:
                continue
            used += len(data)
            seen.add(name)
            out.append({"name": name, "png_b64": base64.b64encode(data).decode()})
    return out


GRACE_SECONDS = 20          # a recipe that owns a VM or a container needs this long to clean up after SIGTERM


def stop_group(proc: subprocess.Popen, grace: float | None = None) -> None:
    """Ask the recipe's whole process group to stop (SIGTERM, so its cleanup trap can delete a VM or container), then kill what is left.
    SIGKILL alone cannot be trapped and would leak whatever the recipe had started."""
    grace = GRACE_SECONDS if grace is None else grace
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    end = time.monotonic() + grace
    while proc.poll() is None and time.monotonic() < end:
        time.sleep(0.1)
    try:
        os.killpg(proc.pid, signal.SIGKILL)         # also reaps any child that ignored SIGTERM
    except ProcessLookupError:
        pass
    proc.wait()


def run_recipe(recipe: Recipe, cwd: Path, logfile: Path, cancelled: threading.Event, extra_env: dict | None = None) -> tuple[int | None, str]:
    """Run the recipe's argv with a minimal environment. (exit code or None, note). Kills the whole process group on timeout or cancel."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/tmp"), "LANG": "en_US.UTF-8", "CI": "true"}
    env.update({k: os.environ[k] for k in recipe.env_passthrough if k in os.environ})
    env.update(extra_env or {})
    with open(logfile, "wb") as out:
        proc = subprocess.Popen(recipe.command, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
                                start_new_session=True)
        deadline = time.monotonic() + recipe.timeout
        while proc.poll() is None:
            if cancelled.is_set() or time.monotonic() > deadline:
                stop_group(proc)
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


def scan_files(job: dict, tmp: Path) -> tuple[dict, Path | None]:
    """For a scan job (one with params): write the rules outside the checkout and name where the recipe leaves its findings.
    The repository can neither supply nor read the rules."""
    params = job.get("params")
    if not isinstance(params, dict):
        return {}, None
    pfile, ffile = tmp / "params.json", tmp / "findings.json"
    pfile.write_text(json.dumps(params))
    return {"FACTORY_JOB_PARAMS": str(pfile), "FACTORY_FINDINGS_FILE": str(ffile)}, ffile


def read_findings(path: Path) -> list:
    """What the recipe wrote (the orchestrator validates it again). No file means it found nothing."""
    if not path.is_file():
        return []
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise ValueError("the findings file must hold a JSON list")
    return data


def recipe_progress(logfile) -> str:
    """The text of the last `##progress ` line in the recipe output (empty if none)."""
    last = ""
    for line in read_tail(logfile).splitlines():
        if line.startswith(PROGRESS_MARK):
            last = " ".join(line[len(PROGRESS_MARK):].split())[:200]
    return last


def current_progress(step: dict) -> str:
    """The worker's own step, with the recipe's latest ##progress line while the recipe runs."""
    text = step["text"]
    if text.startswith("running recipe") and step["log"]:
        note = recipe_progress(step["log"])
        if note:
            return f"{text.removeprefix('running ')}: {note}"
    return text


def execute(cfg: Config, api: Api, job: dict) -> dict | None:
    """Do one job and return the result body (None when the orchestrator cancelled it)."""
    recipe = cfg.recipes.get(job.get("recipe"))
    if recipe is None:
        return {"status": "error", "exit_code": None, "log": f"this worker has no recipe named {job.get('recipe')!r}"}
    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="factory-job-", dir=cfg.work_dir))
    cancelled, done, changed = threading.Event(), threading.Event(), threading.Event()
    step = {"text": "preparing checkout", "log": None}

    def say(text: str) -> None:
        step["text"] = text
        changed.set()

    def beat():
        every = max(5, int(job.get("lease_seconds", 120)) // 3)
        while not done.is_set():
            changed.wait(every)                             # a new step is sent soon, else on the heartbeat timer
            changed.clear()
            if done.is_set():
                break
            try:
                status, reply = api.call(f"/v1/jobs/{job['id']}/heartbeat", {"progress": current_progress(step)}, 30)
                if status == 404 or (reply or {}).get("cancel"):
                    cancelled.set()
            except OSError:
                log.warning("heartbeat failed; will retry")
            done.wait(MIN_PROGRESS_GAP)                     # at most one update per gap
    threading.Thread(target=beat, daemon=True).start()
    try:
        checkout, logfile = tmp / "src", tmp / "recipe.log"
        step["log"] = logfile
        problem = prepare(cfg, job, checkout)
        if problem:
            return {"status": "error", "exit_code": None, "log": problem}
        extra, findings_file = scan_files(job, tmp)
        before = snapshot(checkout) if is_lockfile(job) else ""
        say(f"running recipe {job['recipe']}")
        code, note = run_recipe(recipe, checkout, logfile, cancelled, extra)
        if cancelled.is_set():
            return None
        say("collecting results")
        text = read_tail(logfile) + (f"\n[worker] {note}" if note else "")
        result = {"status": "passed" if code == 0 else "failed", "exit_code": code, "log": text,
                  "artifacts": (collect_artifacts(recipe, checkout, MAX_SCREEN_ARTIFACTS, MAX_SCREEN_BYTES) if is_screens(job)
                                                         else collect_artifacts(recipe, checkout))}
        if is_lockfile(job) and code == 0:
            result["patch"] = lockfile_diff(checkout, before)
        elif findings_file and code == 0:
            result["findings"] = read_findings(findings_file)
        return result
    except Exception as e:                                   # a worker bug is an error, never a verdict on the patch
        log.exception("job failed")
        return {"status": "error", "exit_code": None, "log": f"worker error: {type(e).__name__}: {e}"}
    finally:
        done.set()
        changed.set()
        shutil.rmtree(tmp, ignore_errors=True)


PREFLIGHT_EVERY = 60
JS_RECIPES = ("web-test.sh", "playwright-screens.sh", "lockfile-update.sh")     # the shipped recipes that need Node (worker/install-node.sh gives them one)


def install_node(root: Path = ROOT) -> str:
    """Run worker/install-node.sh (a pinned, checksummed Node into tools/node in this folder; no sudo). '' on success, else why not."""
    script = root / "worker" / "install-node.sh"
    if not script.is_file():
        return "this install has no worker/install-node.sh"
    try:
        r = subprocess.run(["bash", str(script)], cwd=root, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.SubprocessError) as e:
        return f"{type(e).__name__}: {e}"
    return "" if r.returncode == 0 else (r.stderr or r.stdout or "install-node.sh failed")[-300:].strip()


def preflight(recipe: Recipe) -> list[str]:
    """What this machine lacks for a recipe, from the recipe's own `--preflight` (worker/recipes/*.sh): exit 3 and a line per missing tool.
    Anything else, such as a recipe of your own that has no such flag, counts as ready."""
    try:
        r = subprocess.run([*recipe.command, "--preflight"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30, cwd=ROOT)
    except (OSError, subprocess.SubprocessError):
        return []
    lines = [ln.strip()[:300] for ln in r.stdout.splitlines() if ln.strip()]
    return lines[:5] if r.returncode == 3 and lines else []


def readiness(cfg: Config, state: dict, now: float | None = None) -> tuple[list[str], dict[str, str]]:
    """(recipes this machine can run now, {recipe: why not}). Asked again every PREFLIGHT_EVERY seconds, so installing a tool is noticed
    without a restart. Only ready recipes are offered to the factory, so a job it cannot run is never handed over."""
    now = time.time() if now is None else now
    if "ready" not in state or now - state.get("at", 0) >= PREFLIGHT_EVERY:
        problems = {n: preflight(r) for n, r in sorted(cfg.recipes.items())}
        needs_node = [n for n, p in problems.items() if p and Path(cfg.recipes[n].command[0]).name in JS_RECIPES]
        if needs_node and cfg.install_tools and not state.get("node_tried"):       # once per run: a failed download is not retried every minute
            state["node_tried"] = True
            log.info("%s need Node.js, which this machine lacks: installing one of its own (worker/install-node.sh)", ", ".join(needs_node))
            why = install_node()
            if why:
                log.warning("could not install Node.js: %s", why)
            else:
                log.info("Node.js installed in tools/node")
            problems = {n: preflight(r) for n, r in sorted(cfg.recipes.items())}
        state.update(at=now, ready=[n for n, p in problems.items() if not p], unready={n: p[0] for n, p in problems.items() if p})
        for n, why in state["unready"].items():
            log.warning("recipe %s is not ready: %s", n, why)
    return state["ready"], state["unready"]


def host_stats(work_dir: Path) -> dict:
    """The few numbers the factory shows for this machine: free and total bytes of the disk the work dir is on, memory available and
    total, the load average and the CPU count. Anything it cannot read is left out (the factory shows what it has)."""
    out: dict = {}
    p = work_dir
    while not p.exists() and p != p.parent:
        p = p.parent
    try:
        u = shutil.disk_usage(p)
        out["disk_free"], out["disk_total"] = u.free, u.total
    except OSError:
        pass
    try:
        out["load"] = round(os.getloadavg()[0], 2)
    except OSError:
        pass
    out["cpus"] = os.cpu_count() or 1
    try:
        out["mem_total"] = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        pass
    try:                                                    # Linux
        info = {l.split(":")[0]: int(l.split()[1]) for l in Path("/proc/meminfo").read_text().splitlines()}
        out["mem_avail"] = info["MemAvailable"] * 1024
    except (OSError, ValueError, IndexError, KeyError):
        try:                                                # macOS: free, inactive and speculative pages count as available
            vm = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=3).stdout
            size = int(re.search(r"page size of (\d+) bytes", vm).group(1))
            pages = sum(int(m.group(1)) for k in ("free", "inactive", "speculative") if (m := re.search(rf"Pages {k}:\s+(\d+)", vm)))
            out["mem_avail"] = pages * size
        except (OSError, subprocess.SubprocessError, AttributeError, ValueError):
            pass
    return out


def poll_once(cfg: Config, api: Api, ready: dict | None = None) -> bool:
    """Claim and run at most one job. True if one ran."""
    names, unready = readiness(cfg, ready if ready is not None else {})
    status, job = api.call("/v1/claim", {"platform": cfg.platform, "recipes": names, "unready": unready, "version": PROTOCOL, "app_version": app_version(),
                                  "stats": host_stats(cfg.work_dir)}, 30)
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


def managed() -> bool:
    """True when a service manager (systemd or launchd) will start this worker again after it exits."""
    return bool(os.environ.get("INVOCATION_ID")) or os.environ.get("XPC_SERVICE_NAME", "0") not in ("", "0")


def self_update(cfg: Config, api: Api, tag: str, files: list, root: Path = ROOT) -> str:
    """Install release `tag` with the install's own scripts/update-worker.sh, fed the release files by the factory (the worker holds no
    credentials for a private repository). Returns '' on success, else why not. The script refuses while a job is running, checks the new
    code and rolls back by itself; its restart step is skipped here because the worker exits afterwards and its service starts it again."""
    script = root / "scripts" / "update-worker.sh"
    if not script.is_file() or not re.fullmatch(r"v\d{1,4}\.\d{1,4}\.\d{1,4}", tag):
        return "this install has no scripts/update-worker.sh (update it by hand once)"
    tmp = Path(tempfile.mkdtemp(prefix="factory-update-", dir=cfg.work_dir if cfg.work_dir.is_dir() else None))
    try:
        for name in files:
            if name not in ("VERSION", "shikumi-worker.tar.gz", "setup-worker.sh", "update-worker.sh"):
                return f"the factory offered an unexpected file {name!r}"
            status, reply = api.call("/v1/release", {"tag": tag, "name": name}, 300)
            if status != 200 or not isinstance(reply, dict) or not isinstance(reply.get("b64"), str):
                return f"could not get {name} from the factory (HTTP {status})"
            (tmp / name).write_bytes(base64.b64decode(reply["b64"], validate=True))
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/tmp"), "SHIKUMI_ASSET_BASE": tmp.as_uri(),
               "SKIP_SERVICE": "1", "SETTLE_SECONDS": "1"}
        r = subprocess.run(["bash", str(script), tag], cwd=root, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=1800)
        if r.returncode:
            return (r.stderr or r.stdout or "the update script failed")[-300:].strip()
        return ""
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        return f"{type(e).__name__}: {e}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def maybe_update(cfg: Config, api: Api, state: dict, now: float, root: Path = ROOT) -> bool:
    """Ask the factory whether to update (at most once a minute, only when idle) and do it. True when the worker should exit so its
    service starts the new version. `state` remembers when it last asked and which release failed."""
    if now - state.get("asked", 0) < UPDATE_EVERY:
        return False
    state["asked"] = now
    have = app_version()
    if not have:
        return False
    status, reply = api.call("/v1/update", {"app_version": have}, 30)
    if status != 200 or not isinstance(reply, dict) or not reply.get("update"):
        return False
    tag, files = str(reply.get("tag", "")), reply.get("files") or []
    if state.get("failed_tag") == tag and now - state.get("failed_at", 0) < UPDATE_RETRY:
        return False
    log.info("updating from v%s to %s as the factory asked", have, tag)
    why = self_update(cfg, api, tag, files, root)
    if why:
        state.update(failed_tag=tag, failed_at=now)
        log.warning("the update to %s did not install: %s", tag, why)
        return False
    if managed():
        log.info("updated to %s: exiting so the service starts the new version", tag)
        return True
    log.info("updated to %s: restart this worker (it is not run by a service) to use it", tag)
    state.update(failed_tag=tag, failed_at=now)           # do not ask again until the retry window: the files are already new
    return False


def check(cfg: Config, api: Api) -> list[tuple[str, str]]:
    """The worker's own doctor: [(ok|warn|FAIL, message)]. Reads only; it never claims a job."""
    out = [("ok" if cfg.recipes else "FAIL", f"{len(cfg.recipes)} recipe(s): {', '.join(sorted(cfg.recipes)) or 'none configured'}")]
    for name, r in sorted(cfg.recipes.items()):
        exe = r.command[0]
        found = exe if os.access(exe, os.X_OK) and os.path.isfile(exe) else shutil.which(exe)
        out.append(("ok" if found else "FAIL", f"recipe {name}: {exe}" + ("" if found else " is not an executable file or on PATH")))
        if found:
            for why in preflight(r):             # a warning, not a failure: a missing tool must not roll an update back
                out.append(("warn", f"recipe {name} cannot run yet, so the factory will not send it jobs: {why}"))
    out.append(("ok" if shutil.which("git") else "FAIL", "git is installed" if shutil.which("git") else "git is not installed"))
    try:
        status, reply = api.call("/v1/ping", {}, 15)
        if status == 200 and (reply or {}).get("ok"):
            out.append(("ok", f"the orchestrator at {cfg.server} accepted the token (as worker {reply.get('worker')!r})"))
        elif status == 401:
            out.append(("FAIL", f"{cfg.server} refused the token: it is wrong, or not in the factory's tokens file"))
        else:
            out.append(("FAIL", f"{cfg.server} answered HTTP {status}: is [workers] enabled and the worker API running?"))
    except (OSError, ValueError) as e:
        out.append(("FAIL", f"cannot reach {cfg.server} ({type(e).__name__}): is the URL right, and is the tunnel or VPN up?"))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="software-factory verification worker")
    ap.add_argument("--config", required=True)
    ap.add_argument("--check", action="store_true", help="check the setup (recipes, git, server, token) and exit; claims nothing")
    ap.add_argument("--once", action="store_true", help="claim at most one job, then exit")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = load_config(args.config)
    api = Api(cfg)
    if args.check:
        results = check(cfg, api)
        for level, msg in results:
            print(f"[{level}] {msg}")
        sys.exit(1 if any(lvl == "FAIL" for lvl, _ in results) else 0)
    log.info("worker up: platform=%s recipes=%s server=%s version=%s", cfg.platform, sorted(cfg.recipes), cfg.server, app_version() or "dev")
    upd: dict = {}
    ready: dict = {}
    while True:
        try:
            ran = poll_once(cfg, api, ready)
            if not ran and not args.once and maybe_update(cfg, api, upd, time.time()):
                os._exit(0)                                   # the service manager starts the new version
        except (OSError, ValueError) as e:
            log.warning("server unreachable: %s", e)
            ran = False
        if args.once:
            return
        if not ran:
            time.sleep(cfg.poll_seconds)


if __name__ == "__main__":
    main()
