"""Tiny operator CLI: python3 -m factory.ctl [status|pause|resume|screens baseline <owner/repo> <checkout>]"""
import os
import sqlite3
import sys
from pathlib import Path

from . import pause, screens
from .config import load


def screens_baseline(cfg, repo: str, checkout: Path) -> int:
    """Render the configured screens of `repo` from a local checkout and write them as the baselines (a person reviews the images
    and commits them: nothing is pushed from here)."""
    if not screens.shots_for(cfg.screens, repo):
        print(f"no screens are configured for {repo}")
        return 1
    got, problem = screens.capture(cfg.runner, cfg.screens, repo, checkout)
    out = checkout / cfg.screens.baseline_dir
    out.mkdir(parents=True, exist_ok=True)
    for i, png in got.items():
        (out / f"{i}.png").write_bytes(png)
        print(f"wrote {out / (i + '.png')}")
    if problem:
        print("problem:", problem)
        return 1
    print("Review the images, then commit them.")
    return 0


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    cfg = load(os.environ.get("FACTORY_CONFIG", "/srv/factory/config.toml"))
    state = Path(cfg.db_path).parent
    if cmd == "screens" and sys.argv[2:3] == ["baseline"] and len(sys.argv) == 5:
        sys.exit(screens_baseline(cfg, sys.argv[3], Path(sys.argv[4])))
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
