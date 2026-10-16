"""Building the review-video image (sandbox/video) from the admin UI.

The image is about 0.5 GB and is not part of a release, so it is built on the factory's machine. As in tools.py, only the orchestrator
talks to the container engine: the UI writes a request file (`state/VIDEO_IMAGE_BUILD`, no content from the browser) and reads
`state/video_image.json`, which the orchestrator keeps up to date. Only the image this repository builds is built here (a custom
`videos.image` is never overwritten); the build goes to a candidate tag first, so a failed build leaves the current image in place."""
import json
import logging
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .tools import image_exists, sandbox_dir

log = logging.getLogger("factory.videoimage")
REQUEST = "VIDEO_IMAGE_BUILD"
STATE = "video_image.json"
DEFAULT = "localhost/factory-video:latest"
RESCAN_SECONDS = 600
BUILD_TIMEOUT = 3600
_busy = threading.Lock()


def managed(cfg) -> bool:
    """Whether the configured image is the one this repository builds."""
    return cfg.videos.image == DEFAULT


def read(state_dir) -> dict:
    try:
        d = json.loads((Path(state_dir) / STATE).read_text())
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _update(state_dir, **fields) -> None:
    d = read(state_dir)
    d.update(fields)
    path = Path(state_dir) / STATE
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".video_image.")
    with os.fdopen(fd, "w") as f:
        json.dump(d, f)
    os.replace(tmp, path)


def request(state_dir) -> None:
    (Path(state_dir) / REQUEST).write_text("build\n")


def requested(state_dir) -> bool:
    return (Path(state_dir) / REQUEST).exists()


def build(cfg, run=subprocess.run) -> tuple[bool, str]:
    """Build the image. (True, message) on success; (False, why) otherwise, the current image untouched."""
    engine, image = cfg.runner.engine, cfg.videos.image
    if not managed(cfg):
        return False, "the configured image is a custom one, which is not built from here"
    src = sandbox_dir() / "video"
    if not (src / "Dockerfile").is_file():
        return False, "the video build files are not in this install"
    candidate = image.rsplit(":", 1)[0] + ":candidate"
    try:
        b = run([engine, "build", "-q", "-t", candidate, "-f", str(src / "Dockerfile"), str(src)],
                capture_output=True, text=True, timeout=BUILD_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        return False, f"the build did not finish ({type(e).__name__})"
    if b.returncode != 0:
        return False, "the build failed: " + ((b.stderr or b.stdout or "").strip()[-300:] or f"exit {b.returncode}")
    for cmd in ([engine, "tag", candidate, image], [engine, "rmi", candidate], [engine, "image", "prune", "-f"]):
        run(cmd, capture_output=True, timeout=120)
    return True, "The video image is built."


def _work(cfg, state_dir, run, now) -> None:
    try:
        _update(state_dir, building=True, started=now())
        try:
            ok, msg = build(cfg, run)
        except Exception as e:                          # a bug must not leave the page saying "building" forever
            log.exception("video image build failed")
            ok, msg = False, f"unexpected error ({type(e).__name__})"
        log.info("video image: %s", msg)
        _update(state_dir, last={"ok": ok, "message": msg, "at": now()},
                exists=image_exists(cfg.runner.engine, cfg.videos.image, run), scanned=now())
    finally:
        _busy.release()
        try:
            _update(state_dir, building=False)
        except OSError:
            log.exception("could not clear the video image build state")


def _scan(cfg, state_dir, run, now) -> None:
    try:
        _update(state_dir, exists=image_exists(cfg.runner.engine, cfg.videos.image, run), scanned=now())
    except Exception:
        log.exception("video image check failed")
    finally:
        _busy.release()


def tick(cfg, state_dir, run=subprocess.run, now=time.time, spawn=None) -> bool:
    """Called every poll: start a requested build, or a periodic check that the image exists, in a background thread. True if
    work started. Never blocks the poll."""
    if cfg.dry_run:
        return False
    st = read(state_dir)
    if st.get("building") and _busy.acquire(blocking=False):
        try:                                            # no build is running here: a leftover from a restart
            _update(state_dir, building=False)
        except OSError:
            pass
        finally:
            _busy.release()
    want = requested(state_dir)
    due = now() - float(st.get("scanned", 0) or 0) > RESCAN_SECONDS
    if not want and not due:
        return False
    if not _busy.acquire(blocking=False):
        return False
    (Path(state_dir) / REQUEST).unlink(missing_ok=True)
    target = (lambda: _work(cfg, state_dir, run, now)) if want else (lambda: _scan(cfg, state_dir, run, now))
    (spawn or (lambda f: threading.Thread(target=f, daemon=True, name="video-image").start()))(target)
    return True
