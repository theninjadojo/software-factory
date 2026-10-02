"""Pause switch. Manual: a PAUSED file in the state dir. Automatic: pause_until (rate-limit backoff).
Telegram's /pause and /resume will just create/remove the same file."""
import os
import tempfile
import threading
import time
from pathlib import Path

_lock = threading.Lock()


def paused(state_dir: str | Path) -> str | None:
    d = Path(state_dir)
    if (d / "PAUSED").exists():
        return "manual pause"
    f = d / "pause_until"
    with _lock:                            # never delete a deadline another thread has just written
        if f.exists():
            try:
                until = float(f.read_text())
            except ValueError:
                return "corrupt pause_until (treating as paused)"
            if time.time() < until:
                return "backoff until " + time.strftime("%H:%M:%S", time.localtime(until))
            f.unlink(missing_ok=True)
    return None


def set_backoff(state_dir: str | Path, seconds: int) -> bool:
    """Pause every job until now + seconds (an existing later deadline is kept). Parallel runs share one subscription, so
    several can hit the limit together: the file is replaced atomically (a torn write would read as corrupt, which means
    paused until a person resumes), and the return value says whether this call started the backoff, so one alert is sent."""
    d = Path(state_dir)
    with _lock:
        f = d / "pause_until"
        try:
            current = float(f.read_text())
        except (OSError, ValueError):
            current = 0.0
        now = time.time()
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".pause_until.")
        with os.fdopen(fd, "w") as out:
            out.write(str(max(current, now + seconds)))
        os.replace(tmp, f)
        return current <= now
