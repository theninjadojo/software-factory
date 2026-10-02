"""Pause switch. Manual: a PAUSED file in the state dir. Automatic: pause_until (rate-limit backoff).
Telegram's /pause and /resume will just create/remove the same file."""
import time
from pathlib import Path


def paused(state_dir: str | Path) -> str | None:
    d = Path(state_dir)
    if (d / "PAUSED").exists():
        return "manual pause"
    f = d / "pause_until"
    if f.exists():
        try:
            until = float(f.read_text())
        except ValueError:
            return "corrupt pause_until (treating as paused)"
        if time.time() < until:
            return "backoff until " + time.strftime("%H:%M:%S", time.localtime(until))
        f.unlink(missing_ok=True)
    return None


def set_backoff(state_dir: str | Path, seconds: int) -> None:
    (Path(state_dir) / "pause_until").write_text(str(time.time() + seconds))
