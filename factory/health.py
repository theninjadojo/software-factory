"""Health watchdog: checks the things that silently stop the factory (full disk, a hung or dead orchestrator, a dead container
engine or proxy, an expired GitHub token, runs that keep failing) and alerts on Telegram when one changes state.

It is a separate process (factory-health.timer runs `python3 -m factory.health`), so it still reports when the orchestrator itself
is hung or dead. Alerts fire when a check turns bad or worse, repeat every `repeat_hours` while it stays bad, and say so when it
recovers. State between runs is one small JSON file next to the database. Each check is isolated: one that crashes is reported as
a problem of its own and never stops the others."""
import argparse
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import db as dbm
from .config import Config, load
from .events import event_enabled

log = logging.getLogger("factory.health")
LEVELS = {"ok": 0, "warn": 1, "crit": 2}
GB = 1024 ** 3


@dataclass(frozen=True)
class Result:
    name: str
    level: str          # ok | warn | crit
    detail: str


def disk_level(free: int, total: int, hc) -> str:
    pct = 100 * free / total if total else 0
    if pct < hc.disk_crit_percent or free < hc.disk_crit_gb * GB:
        return "crit"
    if pct < hc.disk_warn_percent or free < hc.disk_warn_gb * GB:
        return "warn"
    return "ok"


def check_disk(cfg: Config, hc) -> list[Result]:
    """Free space and free inodes on every filesystem the factory writes to (state, work dir, container storage)."""
    home = Path.home()
    paths = [Path(cfg.db_path).parent, Path(cfg.runner.work_dir), *map(Path, hc.extra_disk_paths),
             home / ".local/share/containers", Path("/var/lib/containers")]
    seen: dict[int, str] = {}
    out = []
    for p in paths:
        while not p.exists() and p != p.parent:     # the work dir may not exist yet: check the filesystem it will be on
            p = p.parent
        try:
            dev = p.stat().st_dev
            if dev in seen:
                continue
            seen[dev] = str(p)
            s = os.statvfs(p)
        except OSError:
            continue
        free, total = s.f_bavail * s.f_frsize, s.f_blocks * s.f_frsize
        level = disk_level(free, total, hc)
        out.append(Result(f"disk:{p}", level, f"{free / GB:.1f} GB free of {total / GB:.0f} GB ({100 * free / max(total, 1):.0f}%)"))
        if s.f_files:
            ipct = 100 * s.f_favail / s.f_files
            out.append(Result(f"inodes:{p}", "crit" if ipct < hc.disk_crit_percent else "warn" if ipct < hc.disk_warn_percent else "ok",
                              f"{ipct:.0f}% of inodes free"))
    return out


def check_memory(cfg: Config, hc) -> list[Result]:
    try:
        info = {l.split(":")[0]: int(l.split()[1]) for l in Path("/proc/meminfo").read_text().splitlines()}
    except (OSError, ValueError, IndexError):
        return []                                    # not Linux: nothing to say
    avail = info.get("MemAvailable", 0) * 1024
    level = "crit" if avail < hc.mem_crit_mb * 1024 ** 2 else "warn" if avail < hc.mem_warn_mb * 1024 ** 2 else "ok"
    return [Result("memory", level, f"{avail // 1024 ** 2} MB available")]


def check_orchestrator(cfg: Config, hc) -> list[Result]:
    """The poll loop's heartbeat. Covers a dead process, a hung poll (a stuck network call) and a loop that fails every time."""
    try:
        db = dbm.connect(cfg.db_path)
        st = dbm.get_status(db)
        stuck = db.execute("SELECT repo, issue, started FROM runs WHERE status='running' AND started < ?",
                           (time.time() - 2 * cfg.runner.timeout_seconds,)).fetchall()
        last = [r[0] for r in db.execute("SELECT status FROM runs WHERE finished IS NOT NULL AND status!='interrupted' "
                                         "ORDER BY id DESC LIMIT ?", (hc.failed_runs_warn,))]
        db.close()
    except Exception as e:
        return [Result("database", "crit", f"cannot read {cfg.db_path}: {type(e).__name__}: {str(e)[:120]}")]
    out = [Result("database", "ok", "readable")]
    ok = float((st.get("last_poll_ok") or {}).get("value") or 0)
    limit = max(hc.stale_poll_minutes * 60, 3 * cfg.poll_seconds)
    age = time.time() - ok
    err = (st.get("last_error") or {}).get("value", "")
    if not ok:
        out.append(Result("orchestrator", "crit", "has never completed a poll" + (f"; last error: {err}" if err else "")))
    elif age > limit:
        out.append(Result("orchestrator", "crit", f"no completed poll for {int(age // 60)} min (limit {int(limit // 60)})"
                                                  + (f"; last error: {err}" if err else "; it may be hung or stopped")))
    else:
        out.append(Result("orchestrator", "ok", f"last poll {int(age)}s ago"))
    if stuck:
        out.append(Result("stuck-runs", "warn", ", ".join(f"{r}#{i} for {int((time.time() - s) // 60)} min" for r, i, s in stuck[:5])
                                                + f" still running past twice the {cfg.runner.timeout_seconds}s timeout"))
    else:
        out.append(Result("stuck-runs", "ok", "none"))
    if len(last) >= hc.failed_runs_warn and all(s == "failed" for s in last):
        out.append(Result("run-failures", "warn", f"the last {len(last)} runs all failed (an expired Claude token, an API outage "
                                                  "or a broken image look like this)"))
    else:
        out.append(Result("run-failures", "ok", "no failure streak"))
    return out


def check_engine(cfg: Config, hc) -> list[Result]:
    eng = cfg.runner.engine
    if not shutil.which(eng):
        return [Result("engine", "crit", f"{eng} is not installed or not on PATH")]
    try:
        r = subprocess.run([eng, "info"], capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        return [Result("engine", "crit", f"`{eng} info` did not answer in 30s")]
    if r.returncode:
        return [Result("engine", "crit", f"`{eng} info` failed: {r.stderr.decode(errors='replace').strip()[-150:]}")]
    return [Result("engine", "ok", f"{eng} responds")]


def check_proxy(cfg: Config, hc) -> list[Result]:
    sock = cfg.runner.proxy_socket
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(5)
    try:
        s.connect(sock)
    except OSError as e:
        return [Result("proxy", "crit", f"egress proxy socket {sock} does not accept connections ({e.strerror or e}); "
                                        "agents cannot reach the API")]
    finally:
        s.close()
    return [Result("proxy", "ok", "accepts connections")]


def check_secrets(cfg: Config, hc) -> list[Result]:
    missing = [p for p in (cfg.token_file, cfg.runner.claude_env_file) if p and not Path(p).is_file()]
    return [Result("secrets", "crit", "missing: " + ", ".join(missing)) if missing else Result("secrets", "ok", "token files present")]


def check_github(cfg: Config, hc) -> list[Result]:
    """/rate_limit does not count against the limit. 401 means the token lapsed; a low remainder means polling will stall."""
    if not cfg.token_file or not Path(cfg.token_file).is_file():
        return []                                    # reported by check_secrets
    req = urllib.request.Request("https://api.github.com/rate_limit", headers={
        "Authorization": f"Bearer {Path(cfg.token_file).read_text().strip()}", "User-Agent": "software-factory",
        "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            core = json.load(r)["resources"]["core"]
    except urllib.error.HTTPError as e:
        return [Result("github", "crit" if e.code in (401, 403) else "warn", f"GitHub answered HTTP {e.code}"
                                                                           + (": the token is invalid or expired" if e.code == 401 else ""))]
    except Exception as e:
        return [Result("github", "warn", f"cannot reach GitHub: {type(e).__name__}")]
    left = core["remaining"]
    return [Result("github", "warn" if left < hc.github_rate_warn else "ok", f"{left}/{core['limit']} API calls left this hour")]


CHECKS = (check_disk, check_memory, check_orchestrator, check_engine, check_proxy, check_secrets, check_github)


def run_checks(cfg: Config, hc=None) -> list[Result]:
    hc = hc or cfg.health
    out: list[Result] = []
    for fn in CHECKS:
        try:
            out += fn(cfg, hc)
        except Exception as e:
            log.exception("check %s crashed", fn.__name__)
            out.append(Result(fn.__name__.removeprefix("check_"), "warn", f"the check itself failed: {type(e).__name__}: {str(e)[:120]}"))
    return out


def decide(prev: dict, results: list[Result], now: float, repeat_seconds: float) -> tuple[list[str], dict]:
    """(messages, new state). prev/new: name -> {level, since, alerted}. A message goes out when a check turns bad or worse, again
    every repeat_seconds while it stays bad, and once when it is fine again. A check that stops being reported is forgotten."""
    msgs, new = [], {}
    for r in results:
        p = prev.get(r.name, {"level": "ok", "since": now, "alerted": 0})
        cur = LEVELS[r.level]
        if cur == 0:
            if LEVELS[p["level"]] > 0 and p.get("alerted"):
                msgs.append(f"✅ Recovered: {r.name}: {r.detail}")
            new[r.name] = {"level": "ok", "since": now if p["level"] != "ok" else p["since"], "alerted": 0}
            continue
        worse = cur > LEVELS[p["level"]]
        due = now - p.get("alerted", 0) >= repeat_seconds
        if worse or due:
            icon = "🔴" if r.level == "crit" else "🟠"
            since = "" if worse or p["level"] == "ok" else f" (since {time.strftime('%H:%M', time.localtime(p['since']))})"
            msgs.append(f"{icon} {r.name}: {r.detail}{since}")
        new[r.name] = {"level": r.level, "since": p["since"] if p["level"] != "ok" else now,
                       "alerted": now if (worse or due) else p.get("alerted", 0)}
    return msgs, new


def load_state(path: Path) -> dict:
    try:
        s = json.loads(path.read_text())
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path: Path, state: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(path)


def heartbeat(url: str) -> None:
    """Dead-man's switch (healthchecks.io style): pinged only while nothing is critical, so silence means this machine or the
    watchdog is down, which nothing local can report."""
    try:
        urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "software-factory"}), timeout=15).close()
    except Exception as e:
        log.warning("heartbeat ping failed: %s", e)


def format_report(results: list[Result]) -> str:
    return "\n".join(f"{ {'ok': 'ok  ', 'warn': 'WARN', 'crit': 'CRIT'}[r.level] } {r.name:<28} {r.detail}" for r in results)


def run(cfg: Config, send=None, now: float | None = None) -> list[Result]:
    """One pass: check, store the results for the UI/ctl, alert on changes. `send(text)` defaults to the configured Telegram bot."""
    now = now or time.time()
    results = run_checks(cfg)
    state_dir = Path(cfg.db_path).parent
    msgs, state = decide(load_state(state_dir / "health.json"), results, now, cfg.health.repeat_hours * 3600)
    try:
        save_state(state_dir / "health.json", state)
        db = dbm.connect(cfg.db_path)
        dbm.set_status(db, "health", json.dumps([[r.name, r.level, r.detail] for r in results if r.level != "ok"]))
        dbm.set_status(db, "health_checked", str(now))
        db.close()
    except Exception:
        log.exception("could not store health results")
    if msgs:
        text = "Factory health\n" + "\n".join(msgs)
        log.warning("%s", text)
        if send is None:
            send = telegram_sender(cfg)
        if send and event_enabled(cfg.telegram_verbosity, "health", cfg.telegram_events):
            send(text)
    if cfg.health.heartbeat_url and not any(r.level == "crit" for r in results):
        heartbeat(cfg.health.heartbeat_url)
    return results


def telegram_sender(cfg: Config):
    tf = cfg.telegram_token_file
    if not (tf and cfg.telegram_chat_id and Path(tf).is_file()):
        return None
    from .telegram import Telegram
    tg = Telegram(Path(tf).read_text().strip(), cfg.telegram_chat_id, str(Path(cfg.db_path).parent), cfg.db_path, cfg.repos)
    return tg.send


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.environ.get("FACTORY_CONFIG", "/srv/factory/config.toml"))
    ap.add_argument("--print", action="store_true", help="print every check and exit 1 if any is critical; sends nothing")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load(args.config)
    if args.print:
        results = run_checks(cfg)
        print(format_report(results))
        sys.exit(1 if any(r.level == "crit" for r in results) else 0)
    if cfg.health.enabled:
        run(cfg)


if __name__ == "__main__":
    main()
