"""Updating a native install (rootless Podman + systemd) from the UI: the same `deploy/update-native.sh` a person runs by hand, started
as its own transient systemd unit (so restarting the UI, which that script does, cannot kill it), plus the nightly auto-update timer.

Nothing here downloads or installs anything itself: the script tests the new code, refuses while an agent run is in flight, rebuilds the
sandbox images, restarts the services and rolls back if they do not come up. A tag is checked against the release pattern before it
reaches the command line. Anything that is not a native install gets `native() == False` and the UI says how to update it instead."""
import re
import shutil
import subprocess
from pathlib import Path

UNIT = "shikumi-update-now"
TIMER = "shikumi-update.timer"
SERVICE_FILES = ("shikumi-update.service", "shikumi-update.timer")
AUTO_FILE = "AUTO_UPDATE"             # state/AUTO_UPDATE: "patch" or "all"; update-native.sh --auto reads it
LOG = "update.log"
MODES = ("off", "patch", "all")
TAG = re.compile(r"v\d{1,4}\.\d{1,4}\.\d{1,4}")


def root_of(state_dir) -> Path:
    return Path(state_dir).parent


def script(state_dir) -> Path:
    return root_of(state_dir) / "app" / "deploy" / "update-native.sh"


def native(state_dir, which=shutil.which) -> bool:
    """A native install: the update script is on disk and systemd can run it as a user unit."""
    return script(state_dir).is_file() and bool(which("systemd-run")) and bool(which("systemctl"))


def _ctl(run, *args) -> subprocess.CompletedProcess:
    return run(["systemctl", "--user", *args], capture_output=True, text=True, timeout=30)


def running(run=subprocess.run) -> bool:
    try:
        return _ctl(run, "is-active", "--quiet", UNIT + ".service").returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def start(state_dir, tag: str = "", run=subprocess.run) -> str:
    """Start the update as a detached transient unit. '' on success, else why not. `tag` is a release tag or '' for the latest."""
    if tag and not TAG.fullmatch(tag):
        return "That is not a release tag."
    if not native(state_dir):
        return "This install cannot update itself from the UI."
    if running(run):
        return "An update is already running."
    log = root_of(state_dir) / "state" / LOG
    cmd = ["systemd-run", "--user", "--collect", "--unit", UNIT, "-p", f"StandardOutput=truncate:{log}", "-p", f"StandardError=truncate:{log}",
           "-p", f"WorkingDirectory={root_of(state_dir)}", str(script(state_dir)), *([tag] if tag else [])]
    try:
        r = run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return f"Could not start the update: {type(e).__name__}."
    return "" if r.returncode == 0 else f"Could not start the update: {(r.stderr or '').strip()[-200:]}"


def log_tail(state_dir, size: int = 4000) -> str:
    try:
        data = (root_of(state_dir) / "state" / LOG).read_bytes()[-size:]
    except OSError:
        return ""
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", data.decode(errors="replace"))


def auto_mode(state_dir, run=subprocess.run) -> str:
    """off | patch | all: the timer's state and the mode file."""
    try:
        if _ctl(run, "is-enabled", "--quiet", TIMER).returncode != 0:
            return "off"
    except (OSError, subprocess.SubprocessError):
        return "off"
    try:
        mode = (root_of(state_dir) / "state" / AUTO_FILE).read_text().strip()
    except OSError:
        mode = ""
    return mode if mode in ("patch", "all") else "patch"


def set_auto(state_dir, mode: str, run=subprocess.run, home: Path | None = None) -> str:
    """Turn the nightly auto-update off, or on for patch releases or for every release. '' on success, else why not."""
    if mode not in MODES:
        return "Pick off, patch releases or every release."
    if not native(state_dir):
        return "This install cannot update itself from the UI."
    unit_dir = (home or Path.home()) / ".config" / "systemd" / "user"
    try:
        if mode == "off":
            _ctl(run, "disable", "--now", TIMER)
            return ""
        src = root_of(state_dir) / "app" / "deploy" / "systemd"
        unit_dir.mkdir(parents=True, exist_ok=True)
        for f in SERVICE_FILES:
            if not (unit_dir / f).is_file() or (unit_dir / f).read_bytes() != (src / f).read_bytes():
                shutil.copyfile(src / f, unit_dir / f)
        (root_of(state_dir) / "state" / AUTO_FILE).write_text(mode + "\n")
        _ctl(run, "daemon-reload")
        r = _ctl(run, "enable", "--now", TIMER)
    except (OSError, subprocess.SubprocessError) as e:
        return f"Could not change the timer: {type(e).__name__}."
    return "" if r.returncode == 0 else f"Could not change the timer: {(r.stderr or '').strip()[-200:]}"
