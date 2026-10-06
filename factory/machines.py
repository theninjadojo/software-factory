"""What the machines are doing, for the floor and the Server and Worker pages: the server's disks, memory and load (read live, with
the same thresholds as the health watchdog), and the same few numbers from a worker (it sends them each time it polls). Nothing
is stored for the server; a worker's last report is one JSON column on its row."""
import os
import time
from pathlib import Path

from . import health
from .config import Config

GB = 1024 ** 3
LEVELS = health.LEVELS


def meminfo() -> tuple[int, int] | None:
    """(available, total) bytes from /proc/meminfo, or None where there is none (not Linux)."""
    try:
        info = {l.split(":")[0]: int(l.split()[1]) for l in Path("/proc/meminfo").read_text().splitlines()}
        return info["MemAvailable"] * 1024, info["MemTotal"] * 1024
    except (OSError, ValueError, IndexError, KeyError):
        return None


def uptime_seconds() -> float | None:
    try:
        return float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def worst(levels) -> str:
    return max(levels, key=lambda l: LEVELS.get(l, 0), default="ok")


def mem_level(avail: int, hc) -> str:
    mb = avail // 1024 ** 2
    return "crit" if mb < hc.mem_crit_mb else "warn" if mb < hc.mem_warn_mb else "ok"


def server_stats(cfg: Config) -> dict:
    """{"disks": [...], "mem": {...}|None, "load": float|None, "cpus": int, "uptime": seconds|None, "level": ok|warn|crit}. Read now."""
    hc = cfg.health
    ds = health.disks(cfg, hc)
    mem = meminfo()
    memory = None if mem is None else {"avail": mem[0], "total": mem[1], "level": mem_level(mem[0], hc)}
    try:
        load = os.getloadavg()[0]
    except OSError:
        load = None
    levels = [d["level"] for d in ds] + [d["inodes_level"] for d in ds] + ([memory["level"]] if memory else [])
    return {"disks": ds, "mem": memory, "load": load, "cpus": os.cpu_count() or 1, "uptime": uptime_seconds(), "level": worst(levels)}


def worker_level(stats: dict | None, hc) -> str:
    """The level of what a worker last reported (its work disk and memory), by the same thresholds; ok when it reported nothing."""
    if not stats:
        return "ok"
    out = []
    if stats.get("disk_total"):
        out.append(health.disk_level(stats.get("disk_free", 0), stats["disk_total"], hc))
    if stats.get("mem_avail") is not None:
        out.append(mem_level(stats["mem_avail"], hc))
    return worst(out)


def clean_stats(raw) -> dict | None:
    """A worker's report, checked: only the known numbers, each a non-negative number, else dropped. None when nothing is left."""
    if not isinstance(raw, dict):
        return None
    out = {}
    for k in ("disk_free", "disk_total", "mem_avail", "mem_total", "load", "cpus"):
        v = raw.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v < 1e16:
            out[k] = v
    return out or None


def ago_seconds(t: float, now: float | None = None) -> float:
    return max(0.0, (time.time() if now is None else now) - t)
