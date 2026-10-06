"""The Server and Worker pages: what a machine is doing right now (disks, memory, load), rated by the health watchdog's thresholds.
The server is read live; a worker's numbers are what it sent the last time it polled."""
import json
import socket
import time
from urllib.parse import quote

from .. import machines
from ..verify import ONLINE_SECONDS
from .views import ago, badge, dur, esc

GB = 1024 ** 3
KIND = {"ok": "good", "warn": "warn", "crit": "bad"}
WORD = {"ok": "OK", "warn": "Low", "crit": "Critical"}


def size(n) -> str:
    n = float(n or 0)
    return f"{n / GB:.1f} GB" if n >= GB / 10 else f"{n / 1024 ** 2:.0f} MB"


def meter(used_pct: float, level: str, label: str) -> str:
    """A bar for a share used (0 to 100); the level colours it, and the text beside it says the same in words. An SVG, because the page's
    content security policy refuses style attributes."""
    pct = max(0, min(100, round(used_pct)))
    return (f'<svg class="mt {esc(level)}" viewBox="0 0 100 8" preserveAspectRatio="none" role="meter" aria-label="{esc(label)}" '
            f'aria-valuemin="0" aria-valuemax="100" aria-valuenow="{pct}"><rect class="mt-bg" width="100" height="8"/><rect class="mt-fg" width="{pct}" height="8"/></svg>')


def tile(label: str, value: str, note: str = "", level: str = "ok") -> str:
    return (f'<div class="card mt-tile {esc(level)}"><div class="muted">{esc(label)}</div><div class="mt-v">{esc(value)}</div>'
            f'<div class="muted">{esc(note)}</div></div>')


def up(seconds) -> str:
    if seconds is None:
        return "—"
    d = int(seconds)
    return f"{d // 86400}d {d % 86400 // 3600}h" if d >= 86400 else f"{d // 3600}h {d % 3600 // 60}m" if d >= 3600 else f"{max(1, d // 60)}m"


def inodes(d: dict) -> str:
    return "—" if d["inodes_free_pct"] is None else "%.0f%%" % d["inodes_free_pct"]


def rating(level: str) -> str:
    return badge(WORD[level], KIND[level])


def disk_rows(disks: list[dict]) -> str:
    rows = "".join(
        f'<tr><td data-l="Path"><code>{esc(d["path"])}</code></td><td data-l="Holds">{esc(d["holds"])}</td>'
        f'<td data-l="Used">{meter(100 * (1 - d["free"] / max(d["total"], 1)), d["level"], "Disk used on " + d["path"])}</td>'
        f'<td data-l="Free">{esc(size(d["free"]))} of {esc(size(d["total"]))}</td>'
        f'<td data-l="Inodes free">{inodes(d)}</td><td data-l="State">{rating(machines.worst([d["level"], d["inodes_level"]]))}</td></tr>'
        for d in disks)
    return ("<table class=stack><thead><tr><th>Path</th><th>Holds</th><th>Used</th><th>Free</th><th>Inodes free</th><th>State</th></tr></thead>"
            f"<tbody>{rows}</tbody></table>")


def server_page(cfg, db, now: float | None = None) -> str:
    now = time.time() if now is None else now
    s = machines.server_stats(cfg)
    hc = cfg.health
    mem, tiles = s["mem"], []
    worst_disk = min(s["disks"], key=lambda d: d["free"] / max(d["total"], 1), default=None)
    if worst_disk:
        tiles.append(tile("Fullest disk", f'{100 * worst_disk["free"] / max(worst_disk["total"], 1):.0f}% free',
                          f'{size(worst_disk["free"])} left on {worst_disk["path"]}', worst_disk["level"]))
    if mem:
        tiles.append(tile("Memory available", size(mem["avail"]), f'of {size(mem["total"])}', mem["level"]))
    if s["load"] is not None:
        tiles.append(tile("Load average", f'{s["load"]:.1f}', f'{s["cpus"]} cores'))
    tiles.append(tile("Up", up(s["uptime"]), socket.gethostname()))
    head = (f'<p><a href="/">← Factory</a></p><p>{badge(WORD[s["level"]], KIND[s["level"]])} '
            f'<span class="muted">the machine the factory runs on, read just now</span></p><div class="mt-tiles">{"".join(tiles)}</div>')
    disks = "<h2>Disks</h2>" + (disk_rows(s["disks"]) if s["disks"] else '<p class="muted">No disk could be read.</p>')
    disks += (f'<p class="muted">Warns under {hc.disk_warn_percent}% or {hc.disk_warn_gb} GB free, critical under {hc.disk_crit_percent}% or {hc.disk_crit_gb} GB '
              f'(and the same for memory under {hc.mem_warn_mb} MB and {hc.mem_crit_mb} MB). Set under <code>[health]</code>; '
              'the watchdog alerts on Telegram or Slack when one turns bad.</p>')
    problems, checked = [], None
    if db is not None:
        try:
            st = {k: v for k, v in db.execute("SELECT key, value FROM status WHERE key IN ('health','health_checked')")}
            problems, checked = json.loads(st.get("health") or "[]"), st.get("health_checked")
        except Exception:
            pass                                             # no status table yet, or an older value
    if checked is None:
        watch = '<p class="muted">The health watchdog has not run yet (<code>factory-health.timer</code>), so there are no alerts to show.</p>'
    elif problems:
        rows = "".join(f'<tr><td data-l="Check"><code>{esc(n)}</code></td><td data-l="State">{badge(WORD.get(lv, lv), KIND.get(lv, ""))}</td><td data-l="Detail">{esc(t)}</td></tr>'
                       for n, lv, t in problems)
        watch = (f'<p class="muted">Last checked {esc(ago(float(checked), now))}.</p><table class=stack><thead><tr><th>Check</th><th>State</th><th>Detail</th></tr></thead>'
                 f"<tbody>{rows}</tbody></table>")
    else:
        watch = f'<p>{badge("all checks pass", "good")} <span class="muted">last checked {esc(ago(float(checked), now))}</span></p>'
    return head + disks + "<h2>Watchdog</h2>" + watch + workers_table(db, hc, now)


def workers_table(db, hc, now: float) -> str:
    """The workers and how their machines are doing, each opening its own page."""
    from .. import jobs
    try:
        known = jobs.online_workers(db, 0, float("inf")) if db is not None else []
    except Exception:
        known = []
    if not known:
        return ""
    rows = []
    for k in known:
        try:
            stats = machines.clean_stats(json.loads(k.get("stats") or "{}"))
        except ValueError:
            stats = None
        on = now - k["last_seen"] <= ONLINE_SECONDS
        level = machines.worker_level(stats, hc)
        disk = f'{size(stats["disk_free"])} free' if stats and stats.get("disk_total") else "—"
        rows.append(f'<tr><td data-l="Worker"><a href="/workers/machine?name={esc(quote(k["name"]))}">{esc(k["name"])}</a></td>'
                    f'<td data-l="State">{badge("online", "good") if on else badge("offline", "bad")}</td><td data-l="Disk">{esc(disk)}</td>'
                    f'<td data-l="Rating">{badge(WORD[level], KIND[level]) if stats else "—"}</td></tr>')
    return ('<h2>Workers</h2><table class=stack><thead><tr><th>Worker</th><th>State</th><th>Disk</th><th>Rating</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table>')


def worker_page(cfg, db, name: str, now: float | None = None) -> str | None:
    """None when no worker of that name has been seen."""
    from .. import jobs
    now = time.time() if now is None else now
    row = next((k for k in jobs.online_workers(db, 0, float("inf")) if k["name"] == name), None)
    if row is None:
        return None
    hc = cfg.health
    try:
        stats = machines.clean_stats(json.loads(row.get("stats") or "{}"))
    except ValueError:
        stats = None
    on = now - row["last_seen"] <= ONLINE_SECONDS
    level = machines.worker_level(stats, hc)
    state = badge("online", "good") if on else badge("offline", "bad")
    head = (f'<p><a href="/">← Factory</a> · <a href="/workers">Workers</a></p><p>{state} '
            f'{badge(WORD[level], KIND[level]) if stats else ""} <span class="muted">{esc(row["platform"])} · version {esc(row["app_version"] or "unknown")} · '
            f'seen {esc(ago(row["last_seen"], now))}</span></p>')
    if not stats:
        body = ('<p class="muted">This worker has not reported its disk and memory yet. Workers send them each time they poll; one installed '
                'before this feature needs an update first (<a href="/workers">Workers</a>).</p>')
    else:
        tiles = []
        if stats.get("disk_total"):
            pct = 100 * stats["disk_free"] / stats["disk_total"]
            tiles.append(tile("Work disk free", f"{pct:.0f}%", f'{size(stats["disk_free"])} of {size(stats["disk_total"])}', machines.worker_level({"disk_free": stats["disk_free"], "disk_total": stats["disk_total"]}, hc)))
        if stats.get("mem_avail") is not None:
            tiles.append(tile("Memory available", size(stats["mem_avail"]), f'of {size(stats["mem_total"])}' if stats.get("mem_total") else "",
                              machines.worker_level({"mem_avail": stats["mem_avail"]}, hc)))
        if "load" in stats:
            tiles.append(tile("Load average", f'{stats["load"]:.1f}', f'{int(stats["cpus"])} cores' if stats.get("cpus") else ""))
        meters = ""
        if stats.get("disk_total"):
            meters = (f'<h2>Work disk</h2>{meter(100 * (1 - stats["disk_free"] / stats["disk_total"]), machines.worker_level({"disk_free": stats["disk_free"], "disk_total": stats["disk_total"]}, hc), "Work disk used")}'
                      f'<p class="muted">Warns under {hc.disk_warn_percent}% or {hc.disk_warn_gb} GB free, critical under {hc.disk_crit_percent}% or {hc.disk_crit_gb} GB. '
                      f'{"" if on else "These are the numbers from when it was last seen."}</p>')
        body = f'<div class="mt-tiles">{"".join(tiles)}</div>{meters}'
    recent = db.execute("SELECT id, repo, issue, recipe, status, created, claimed, finished FROM verify_jobs WHERE worker=? ORDER BY id DESC LIMIT 10", (name,)).fetchall()
    jobs_html = ""
    if recent:
        rows = "".join(f'<tr><td data-l="Job"><a href="/workers/job?id={int(j[0])}">#{int(j[0])}</a></td><td data-l="Ticket">{esc(j[1])}{f" #{int(j[2])}" if j[2] else ""}</td>'
                       f'<td data-l="Recipe"><code>{esc(j[3])}</code></td><td data-l="Status">{badge(j[4])}</td><td data-l="Took">{esc(dur(j[6], j[7]) if j[6] else "—")}</td></tr>' for j in recent)
        jobs_html = ('<h2>Its recent jobs</h2><table class=stack><thead><tr><th>Job</th><th>Ticket</th><th>Recipe</th><th>Status</th><th>Took</th></tr></thead>'
                     f"<tbody>{rows}</tbody></table>")
    facts = (f'<h2>About this machine</h2><table class="meta"><tr><th>Recipes</th><td>{esc((row["recipes"] or "—").replace(",", ", "))}</td></tr>'
             f'<tr><th>Platform</th><td>{esc(row["platform"] or "—")}</td></tr></table>')
    return head + body + jobs_html + facts
