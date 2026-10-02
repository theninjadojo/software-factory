"""Tiny operator CLI: python3 -m factory.ctl [status|pause|resume]"""
import sqlite3
import sys
from pathlib import Path

from . import pause
from .config import load


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    cfg = load("/srv/factory/config.toml")
    state = Path(cfg.db_path).parent
    if cmd == "pause":
        (state / "PAUSED").write_text("")
        print("paused")
    elif cmd == "resume":
        (state / "PAUSED").unlink(missing_ok=True)
        (state / "pause_until").unlink(missing_ok=True)
        print("resumed")
    else:
        print("mode:", "dry-run" if cfg.dry_run else "LIVE", "| paused:", pause.paused(state) or "no")
        c = sqlite3.connect(cfg.db_path)
        for repo, issue, outcome, detail, ts in c.execute(
            "select repo,issue,outcome,substr(detail,1,110),datetime(decided_at,'unixepoch','localtime') "
            "from decisions order by decided_at desc limit 10"):
            print(f"{ts}  {repo}#{issue:<5} {outcome:<18} {detail}")


if __name__ == "__main__":
    main()
