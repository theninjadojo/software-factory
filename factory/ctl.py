"""Tiny operator CLI: python3 -m factory.ctl [status|version|pause|resume|doctor|labels [owner/repo ...]|screens baseline <owner/repo> <checkout>|workers add <name>|schedules [list|run <name>]|scans [list|run <name>]|health]"""
import os
import secrets
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from . import pause, screens, tracker, version
from .config import load
from .github import GitHub


def factory_labels(cfg) -> list[tuple[str, str, str]]:
    """(name, colour, description) of every label the configured features use. Built from the config, so renamed labels follow."""
    out = [(cfg.trigger_label, "0e8a16", "Build it: agents edit the repos and open PRs"),
           (cfg.auto_label, "5319e7", "The classifier picks the next stage")]
    for r in cfg.roles:
        out += [(r.label, "1d76db", f"Run the {r.name} stage"), (r.done_label, "c5def5", f"The {r.name} stage is done")]
    out += [(cfg.review.label, "1d76db", "Review this ticket's PRs"), (cfg.review.done_label, "c5def5", "Reviewed"),
            (cfg.conflicts.label, "1d76db", "Resolve merge conflicts in this ticket's PRs"),
            (cfg.pm.unblock_label, "bfdadc", "Ignore the project manager's blockers"),
            (cfg.mockups.bypass_label, "bfdadc", "Build without design mockups"),
            (cfg.mockups.approve_label, "0e8a16", "The design mockups are approved"),
            (cfg.mockups.request_label, "fbca04", "A person asked for screens to review before the build"),
            (cfg.screens.label, "fbca04", "Screen baselines changed: review the images"),
            (tracker.MOVED_LABEL, "ededed", "Moved to the factory's local tracker"), ("factory:working", "fef2c0", "An agent is working on this"), ("factory:pr-open", "c5def5", "The factory opened a PR"),
            ("factory:failed", "d93f0b", "The last factory run failed"), ("factory:needs-answers", "fbca04", "Open questions wait for a person"),
            ("priority: high", "b60205", "Picked up first"), ("priority: low", "c2e0c6", "Picked up last")]
    out += [(f"factory:working-{k}", "fef2c0", f"A {k} agent is working on this") for k in ("analyze", "design", "architect", "review", "conflicts")]
    seen, uniq = set(), []
    for n in out:
        if n[0] and n[0] not in seen:
            seen.add(n[0])
            uniq.append(n)
    return uniq


def create_labels(cfg, gh, repos: list[str]) -> int:
    """Create the factory labels in each repo (existing ones are left alone). Returns 1 if any repo could not be done."""
    bad = 0
    for repo in repos:
        try:
            for name, color, desc in factory_labels(cfg):
                gh.create_label(repo, name, color, desc)
            print(f"{repo}: labels ready")
        except Exception as e:
            bad = 1
            print(f"{repo}: could not create labels ({type(e).__name__}: {e}). Does the token have Issues write on it?")
    return bad


def doctor(cfg, gh, run=subprocess.run) -> list[tuple[str, str]]:
    """Check what a fresh install usually gets wrong. Returns [(level, message)], level ok|warn|FAIL. Reads only: nothing is changed."""
    out: list[tuple[str, str]] = []
    add = lambda lvl, msg: out.append((lvl, msg))
    add("ok" if cfg.dry_run else "warn", "dry-run is ON: the factory only logs" if cfg.dry_run else "LIVE: the factory acts on GitHub")
    # container engine and images
    eng = cfg.runner.engine
    try:
        run([eng, "--version"], capture_output=True, check=True, timeout=20)
        add("ok", f"{eng} is installed")
        images = [("agent sandbox", cfg.runner.image)]
        if cfg.runner.render_previews:
            images.append(("design previews", cfg.runner.render_image))
        if cfg.screens.pages:
            images.append(("screen checks", cfg.screens.image))
        for what, img in images:
            r = run([eng, "image", "inspect", img], capture_output=True, timeout=30)
            add("ok" if r.returncode == 0 else "FAIL", f"{what} image {img}" + ("" if r.returncode == 0 else " is missing: build it (docker compose --profile build build, or scripts/deploy.sh --image)"))
    except (OSError, subprocess.SubprocessError):
        add("FAIL", f"cannot run '{eng}': is it installed, and is [runner] engine right?")
    # secrets and folders
    env = Path(cfg.runner.claude_env_file)
    ok = env.is_file() and any(k in env.read_text() for k in ("ANTHROPIC_API_KEY=", "CLAUDE_CODE_OAUTH_TOKEN="))
    add("ok" if ok else "FAIL", f"model credentials in {env}" + ("" if ok else " are missing (ANTHROPIC_API_KEY=... or CLAUDE_CODE_OAUTH_TOKEN=...)"))
    work = Path(cfg.runner.work_dir)
    add("ok" if work.is_dir() else "FAIL", f"work folder {work}" + ("" if work.is_dir() else " does not exist"))
    sock = Path(cfg.runner.proxy_socket)
    add("ok" if sock.exists() else "warn", f"proxy socket {sock}" + ("" if sock.exists() else " is not there (is the proxy running?)"))
    # verification workers
    if cfg.workers.enabled:
        from .workerapi import read_tokens
        n = len(read_tokens(cfg.workers.tokens_file))
        add("ok" if n else "FAIL", f"{n} worker token(s) in {cfg.workers.tokens_file}" if n else f"no worker token in {cfg.workers.tokens_file}: run 'ctl workers add <name>'")
        add("ok" if cfg.workers.checks else "warn", f"{len(cfg.workers.checks)} worker check(s) configured" if cfg.workers.checks else "[workers] is on but no [[workers.checks]] are set, so nothing is verified")
        try:
            db = sqlite3.connect(f"file:{cfg.db_path}?mode=ro", uri=True, timeout=5)
            try:
                last = db.execute("SELECT name, MAX(last_seen) FROM workers").fetchone()
            finally:
                db.close()
            seen = last[1] if last and last[1] else None
            ago = time.time() - seen if seen else None
            add("ok" if ago is not None and ago < 600 else "warn", f"worker {last[0]!r} polled {int(ago)}s ago" if ago is not None and ago < 600
                else "no worker has polled in the last 10 minutes (is the worker API up, and a worker running? scripts/setup-worker.sh)")
        except sqlite3.Error:
            add("warn", "no worker has connected yet")
    # GitHub: token, repo access, labels
    if gh is None:
        add("FAIL", f"no GitHub token at {cfg.token_file}")
        return out
    try:
        add("ok", f"GitHub token works (acting as {gh.login()})")
    except Exception as e:
        add("FAIL", f"the GitHub token was refused ({type(e).__name__}): expired, or the wrong file?")
        return out
    want = {n for n, _, _ in factory_labels(cfg)}
    for repo in cfg.repos:
        try:
            have = {lb["name"] for lb in gh.repo_labels(repo)}
        except Exception as e:
            add("FAIL", f"{repo}: cannot read it ({type(e).__name__}): is it in the token's repositories?")
            continue
        missing = sorted(want - have)
        add("ok" if not missing else "warn", f"{repo}: labels complete" if not missing else f"{repo}: {len(missing)} label(s) missing: run 'ctl labels' (for example {missing[0]})")
    return out


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


def workers_add(cfg, name: str) -> int:
    """Create a token for a verification worker: appended to workers.tokens_file (mode 0600) and printed once. Never overwrites."""
    from .workerapi import add_token
    try:
        token = add_token(cfg.workers.tokens_file, name)
    except ValueError as e:
        print(e)
        return 1
    print(f"Token for worker {name} (shown once; put it in the worker's token_file):\n{token}")
    return 0


def schedules_cmd(cfg, args: list[str]) -> int:
    """`schedules` lists each schedule and when it last ran; `schedules run <name>` runs one now (needs the GitHub token, ignores dry_run)."""
    from . import db as dbm, schedules
    from .github import GitHub
    db = dbm.local(cfg.db_path)
    if args[:1] == ["run"] and len(args) == 2:
        if args[1] not in {s.name for s in cfg.schedules}:
            print(f"no schedule called {args[1]}")
            return 1
        token = Path(cfg.token_file).read_text().strip() if cfg.token_file and Path(cfg.token_file).exists() else None
        schedules.tick(cfg, GitHub(token), db, time.time(), Path(cfg.db_path).parent,
                       lambda kind, msg, *a: print(f"{kind}: {msg}"), lambda text, event="info": print(text), only=args[1])
        return 0
    if args:
        print("usage: schedules [list|run <name>]")
        return 1
    for s in cfg.schedules:
        st = schedules.get_state(db, s.name) or {}
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(st["last_run"])) if st else "never"
        print(f"{s.name:<24} {s.repo:<30} {s.every or s.cron:<12} {'on ' if s.enabled else 'off'} last: {when} {st.get('status', '')} {st.get('detail', '')}")
    return 0


def scans_cmd(cfg, args: list[str]) -> int:
    """`scans` lists each scan and its last result; `scans run <name>` asks the orchestrator to start one at its next poll."""
    from . import db as dbm, scanner
    db = dbm.local(cfg.db_path)
    if args[:1] == ["run"] and len(args) == 2:
        if args[1] not in {s.name for s in cfg.scanner.scans}:
            print(f"no scan called {args[1]}")
            return 1
        scanner.request_run(db, args[1], time.time())
        print(f"requested: {args[1]} starts at the next poll (not in dry-run or while paused)")
        return 0
    if args and args != ["list"]:
        print("usage: scans [list|run <name>]")
        return 1
    for s in cfg.scanner.scans:
        st = scanner.get_state(db, s.name) or {}
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(st["last_run"])) if st else "never"
        print(f"{s.name:<24} {s.repo:<30} {s.every or s.cron:<12} {'on ' if s.enabled else 'off'} last: {when} {st.get('status', '')} {st.get('detail', '')}")
    return 0


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    cfg = load(os.environ.get("FACTORY_CONFIG", "/srv/factory/config.toml"))
    state = Path(cfg.db_path).parent
    if cmd == "screens" and sys.argv[2:3] == ["baseline"] and len(sys.argv) == 5:
        sys.exit(screens_baseline(cfg, sys.argv[3], Path(sys.argv[4])))
    if cmd == "version":
        print(version.current())
        return
    if cmd == "doctor":
        token = Path(cfg.token_file).read_text().strip() if cfg.token_file and Path(cfg.token_file).exists() else None
        results = doctor(cfg, GitHub(token) if token else None)
        for lvl, msg in results:
            print(f"{lvl:>4}  {msg}")
        bad = sum(1 for lvl, _ in results if lvl == "FAIL")
        print("\nAll good." if not bad else f"\n{bad} problem(s) to fix.")
        sys.exit(1 if bad else 0)
    if cmd == "labels":
        token = Path(cfg.token_file).read_text().strip() if cfg.token_file and Path(cfg.token_file).exists() else None
        if not token:
            print("no GitHub token: put it in", cfg.token_file)
            sys.exit(1)
        sys.exit(create_labels(cfg, GitHub(token), sys.argv[2:] or list(cfg.repos)))
    if cmd == "workers" and sys.argv[2:3] == ["add"] and len(sys.argv) == 4:
        sys.exit(workers_add(cfg, sys.argv[3]))
    if cmd == "health":
        from . import health
        results = health.run_checks(cfg)
        print(health.format_report(results))
        sys.exit(1 if any(r.level == "crit" for r in results) else 0)
    if cmd == "schedules":
        sys.exit(schedules_cmd(cfg, sys.argv[2:]))
    if cmd == "scans":
        sys.exit(scans_cmd(cfg, sys.argv[2:]))
    if cmd == "pause":
        (state / "PAUSED").write_text("")
        print("paused")
    elif cmd == "resume":
        (state / "PAUSED").unlink(missing_ok=True)
        (state / "pause_until").unlink(missing_ok=True)
        print("resumed")
    else:
        print("version:", version.current(), "| mode:", "dry-run" if cfg.dry_run else "LIVE", "| paused:", pause.paused(state) or "no")
        c = sqlite3.connect(cfg.db_path)
        for repo, issue, outcome, detail, ts in c.execute(
            "select repo,issue,outcome,substr(detail,1,110),datetime(decided_at,'unixepoch','localtime') "
            "from decisions order by decided_at desc limit 10"):
            print(f"{ts}  {repo}#{issue:<5} {outcome:<18} {detail}")


if __name__ == "__main__":
    main()
