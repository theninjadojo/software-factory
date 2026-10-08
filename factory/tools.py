"""Updating the agent CLIs (Claude Code, Codex, Gemini) inside their sandbox images.

The CLI is installed by `npm install -g` in a Docker layer, so a plain rebuild reuses the cached layer and never changes the version.
An update here rebuilds the image with a changed `TOOL_REFRESH` build argument (which invalidates that layer), checks the new CLI
starts (`<binary> --version`), then moves the image tag over, keeping the old image as `<name>:previous`. Running sandboxes keep the
image they started from, so nothing waits and nothing is interrupted; a failed build or check leaves the current image untouched.

Only the orchestrator talks to the container engine. The UI writes a request file (`state/TOOLS_UPDATE`, names from the fixed table
below, nothing from the browser reaches a command line) and reads `state/tools.json`, which the orchestrator keeps up to date."""
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("factory.tools")
REQUEST = "TOOLS_UPDATE"
STATE = "tools.json"
LATEST = "tools_latest.json"
RESCAN_SECONDS = 6 * 3600
CACHE_SECONDS = 6 * 3600
BUILD_TIMEOUT = 1800
VERSION = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?")
_busy = threading.Lock()


@dataclass(frozen=True)
class Tool:
    package: str          # npm package
    binary: str           # what the image runs for `--version`
    dockerfile: str       # relative to sandbox/
    label: str


TOOLS = {
    "claude-code": Tool("@anthropic-ai/claude-code", "claude", "Dockerfile", "Claude Code"),
    "codex": Tool("@openai/codex", "codex", "codex/Dockerfile", "Codex CLI"),
    "gemini": Tool("@google/gemini-cli", "gemini", "gemini/Dockerfile", "Gemini CLI"),
}


def sandbox_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "sandbox"


def image_of(cfg, name: str) -> str:
    """The image the harness runs in when it is the one this repository builds, else '' (a custom image is never overwritten)."""
    from .config import default_harnesses
    default = default_harnesses(cfg.runner)[name].image
    h = cfg.harnesses.get(name)
    return default if h is None or h.image == default else ""


def _base(image: str) -> str:
    last = image.rsplit("/", 1)[-1]
    return image[: len(image) - len(last)] + last.split(":")[0]


def read(state_dir) -> dict:
    try:
        d = json.loads((Path(state_dir) / STATE).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tools.")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def _update_state(state_dir, **fields) -> None:
    d = read(state_dir)
    d.update(fields)
    _write(Path(state_dir) / STATE, d)


def request(state_dir, names) -> list[str]:
    """Ask the orchestrator to update these tools. Unknown names are dropped; returns the ones asked for."""
    names = [n for n in dict.fromkeys(names) if n in TOOLS]
    if names:
        (Path(state_dir) / REQUEST).write_text("\n".join(names) + "\n")
    return names


def requested(state_dir) -> list[str]:
    try:
        return [n for n in (Path(state_dir) / REQUEST).read_text().split() if n in TOOLS]
    except OSError:
        return []


# ---------------------------------------------------------------- versions
def latest_version(package: str, opener=urllib.request.urlopen) -> str:
    req = urllib.request.Request("https://registry.npmjs.org/" + package.replace("/", "%2F") + "/latest",
                                 headers={"Accept": "application/json", "User-Agent": "shikumi"})
    try:
        with opener(req, timeout=10) as r:
            v = str(json.loads(r.read(500_000)).get("version", ""))
    except Exception:
        log.info("could not read the latest %s version", package)
        return ""
    return v if VERSION.fullmatch(v) else ""


def latest_due(state_dir, now=time.time) -> bool:
    return now() - checked(state_dir) > CACHE_SECONDS


def checked(state_dir) -> float:
    try:
        return float(json.loads((Path(state_dir) / LATEST).read_text()).get("checked", 0))
    except (OSError, ValueError, TypeError, AttributeError):
        return 0.0


def refresh_latest(state_dir, now=time.time, fetch=latest_version) -> dict:
    """Ask the npm registry for each tool's newest version and remember it (a failed read is remembered as '')."""
    out = {"checked": now(), "latest": {n: fetch(t.package) for n, t in TOOLS.items()}}
    try:
        _write(Path(state_dir) / LATEST, out)
    except OSError:
        log.warning("could not save the latest tool versions")
    return out


def latest(state_dir) -> dict:
    try:
        d = json.loads((Path(state_dir) / LATEST).read_text())
        return {k: v for k, v in d.get("latest", {}).items() if k in TOOLS and isinstance(v, str)}
    except (OSError, ValueError, AttributeError):
        return {}


def _vtuple(v: str):
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", v or "")
    return tuple(int(x) for x in m.groups()) if m else None


def outdated(installed: str, newest: str) -> bool:
    a, b = _vtuple(installed), _vtuple(newest)
    return bool(a and b and b > a)


# ---------------------------------------------------------------- engine work (orchestrator only)
def image_exists(engine: str, image: str, run=subprocess.run) -> bool:
    try:
        return run([engine, "image", "inspect", image], capture_output=True, timeout=60).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def installed_version(engine: str, image: str, binary: str, run=subprocess.run) -> tuple[str, str]:
    """(version, '') or ('', why not): runs `<binary> --version` in the image with no network."""
    try:
        r = run([engine, "run", "--rm", "--network", "none", "--entrypoint", binary, image, "--version"],
                capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as e:
        return "", type(e).__name__
    m = VERSION.search(r.stdout or "")
    if r.returncode == 0 and m:
        return m.group(0), ""
    return "", ((r.stderr or r.stdout or "").strip()[-300:] or f"exit {r.returncode}")


def scan(cfg, state_dir, run=subprocess.run, now=time.time) -> dict:
    """Record the installed version of every tool whose image exists here."""
    found = {}
    for name, t in TOOLS.items():
        h = cfg.harnesses.get(name)
        image = h.image if h else image_of(cfg, name)
        if image and image_exists(cfg.runner.engine, image, run):
            v, _ = installed_version(cfg.runner.engine, image, t.binary, run)
            found[name] = {"version": v, "managed": bool(image_of(cfg, name))}
    _update_state(state_dir, installed=found, scanned=now())
    return found


def update_one(cfg, state_dir, name: str, run=subprocess.run, now=time.time) -> tuple[bool, str]:
    """Rebuild one tool's image with a fresh CLI. (True, message) on success or already current; (False, why) otherwise."""
    t, engine = TOOLS[name], cfg.runner.engine
    image = image_of(cfg, name)
    if not image:
        return False, "this harness uses a custom image, which is not rebuilt from here"
    sdir = sandbox_dir()
    if not (sdir / t.dockerfile).is_file():
        return False, "the sandbox build files are not in this install"
    if not image_exists(engine, image, run):
        return False, f"the image {image} is not built here yet"
    before, _ = installed_version(engine, image, t.binary, run)
    candidate, previous = _base(image) + ":candidate", _base(image) + ":previous"
    try:
        b = run([engine, "build", "-q", "-t", candidate, "--build-arg", f"TOOL_REFRESH={int(now())}", "-f", str(sdir / t.dockerfile), str(sdir)],
                capture_output=True, text=True, timeout=BUILD_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"the build did not finish ({type(e).__name__})"
    if b.returncode != 0:
        return False, "the build failed: " + ((b.stderr or b.stdout).strip()[-300:] or f"exit {b.returncode}")
    after, why = installed_version(engine, candidate, t.binary, run)
    if not after:
        run([engine, "rmi", candidate], capture_output=True, timeout=60)
        return False, f"the new {t.label} does not start, so nothing was changed: {why}"
    if before and after == before:
        run([engine, "rmi", candidate], capture_output=True, timeout=60)
        return True, f"{t.label} is already on the newest version ({after})"
    # The last step drops what the old :previous pointed at, now untagged; images a running sandbox uses are never removed.
    for cmd in ([engine, "tag", image, previous], [engine, "tag", candidate, image], [engine, "rmi", candidate], [engine, "image", "prune", "-f"]):
        run(cmd, capture_output=True, timeout=60)
    return True, f"{t.label} updated: {before or '?'} to {after}. The previous image is kept as {previous}."


def _work(cfg, state_dir, names, run, now) -> None:
    try:
        for name in names:
            _update_state(state_dir, building=name)
            try:
                ok, msg = update_one(cfg, state_dir, name, run, now)
            except Exception as e:                      # a bug must not leave the page saying "building" forever
                log.exception("tool update of %s failed", name)
                ok, msg = False, f"unexpected error ({type(e).__name__})"
            log.info("tools: %s: %s", name, msg)
            _update_state(state_dir, last={"name": name, "ok": ok, "message": msg, "at": now()})
        scan(cfg, state_dir, run, now)
    finally:
        _busy.release()                                 # first: a failed write below (a full disk) must not block every later update
        try:
            _update_state(state_dir, building="")
        except OSError:
            log.exception("could not clear the tool build state")


def tick(cfg, state_dir, run=subprocess.run, now=time.time, spawn=None) -> bool:
    """Called every poll: start a requested update, or a periodic version scan, in a background thread. True if work started.
    Builds never block the poll and never touch running jobs (see the module docstring)."""
    if cfg.dry_run:
        return False
    names, st = requested(state_dir), read(state_dir)
    if st.get("building") and _busy.acquire(blocking=False):
        try:                                            # no build is running here: a leftover from a failed write or a restart
            _update_state(state_dir, building="")
        except OSError:
            pass
        finally:
            _busy.release()
    due = now() - float(st.get("scanned", 0) or 0) > RESCAN_SECONDS
    if not names and not due:
        return False
    if not _busy.acquire(blocking=False):
        return False
    (Path(state_dir) / REQUEST).unlink(missing_ok=True)
    target = (lambda: _work(cfg, state_dir, names, run, now)) if names else (lambda: _scan_work(cfg, state_dir, run, now))
    (spawn or (lambda f: threading.Thread(target=f, daemon=True, name="tools").start()))(target)
    return True


def _scan_work(cfg, state_dir, run, now) -> None:
    try:
        scan(cfg, state_dir, run, now)
    except Exception:
        log.exception("tool version scan failed")
    finally:
        _busy.release()
