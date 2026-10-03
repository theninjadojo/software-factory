"""The Workers page: verification workers, the job queue and each job's log and screenshots. Read-only (the orchestrator's database is
opened read-only). Everything a worker sent is untrusted text: it is escaped here, and screenshots are served only by the UI behind its login."""
import sqlite3
import time
from urllib.parse import urlencode

from .. import jobs
from ..verify import ONLINE_SECONDS
from .views import badge, dur, esc, ago, ticket_link, ts

JOB_BADGE = {"passed": "good", "queued": "warn", "claimed": "warn", "failed": "bad", "error": "bad", "cancelled": ""}


def _rows(db) -> tuple[list, list, bool]:
    try:
        return jobs.online_workers(db, 0, float("inf")), jobs.recent(db, 30), True
    except sqlite3.OperationalError:
        return [], [], False                     # the orchestrator has not created the tables yet


def workers_page(cfg, db, now: float | None = None) -> str:
    now = time.time() if now is None else now
    w = cfg.workers
    known, recent, ready = _rows(db)
    if not w.enabled:
        head = ('<p class="muted">Verification workers are off. A worker is a machine you own (a Mac, a networked Linux box) that builds and tests an agent\'s patch '
                'before it is pushed. Turn it on with <code>[workers] enabled = true</code> in the config and see <code>docs/workers.md</code>.</p>')
    else:
        head = (f'<table class="meta"><tr><th>Mode</th><td>{badge(w.mode, "bad" if w.mode == "block" else "warn")} '
                f'<span class="muted">{"a failing or missing check fails the run" if w.mode == "block" else "a failing check is noted on the PR, which is still opened"}</span></td></tr>'
                f'<tr><th>Worker API</th><td><code>{esc(w.listen)}</code></td></tr>'
                f'<tr><th>Waits</th><td>claimed within {w.claim_wait_seconds}s, finished within {w.max_wait_seconds}s, heartbeat every {w.lease_seconds}s</td></tr></table>')
    checks = ""
    if w.checks:
        checks = ("<h2>Checks</h2><table class=stack><thead><tr><th>Repository</th><th>Recipe</th><th>Platform</th></tr></thead><tbody>"
                  + "".join(f'<tr><td data-l="Repository">{esc(c.repo)}</td><td data-l="Recipe"><code>{esc(c.recipe)}</code></td><td data-l="Platform">{esc(c.platform)}</td></tr>' for c in w.checks)
                  + "</tbody></table>")
    online = ""
    if w.enabled:
        if known:
            rows = "".join(
                f'<tr><td data-l="Worker">{esc(k["name"])}</td><td data-l="Platform">{esc(k["platform"])}</td>'
                f'<td data-l="Recipes">{esc(k["recipes"].replace(",", ", ") or "—")}</td><td data-l="Last seen">{esc(ago(k["last_seen"], now))}</td>'
                f'<td data-l="State">{badge("online", "good") if now - k["last_seen"] <= ONLINE_SECONDS else badge("offline", "bad")}</td></tr>' for k in known)
            online = ("<h2>Workers</h2><table class=stack><thead><tr><th>Worker</th><th>Platform</th><th>Recipes</th><th>Last seen</th><th>State</th></tr></thead>"
                      f"<tbody>{rows}</tbody></table>")
        else:
            online = ('<h2>Workers</h2><p class="muted">No worker has connected yet. Create a token with <code>python3 -m factory.ctl workers add &lt;name&gt;</code>, '
                      'then start <code>worker/worker.py</code> on the machine.</p>')
    jobs_html = ""
    if recent:
        rows = "".join(
            f'<tr><td data-l="Job"><a href="/workers/job?id={int(j["id"])}">#{int(j["id"])}</a></td><td data-l="Ticket">{ticket_link(j["repo"], j["issue"])}</td>'
            f'<td data-l="Recipe"><code>{esc(j["recipe"])}</code></td><td data-l="Status">{badge(j["status"], JOB_BADGE.get(j["status"], ""))}</td>'
            f'<td data-l="Worker">{esc(j["worker"] or "—")}</td><td data-l="Queued">{esc(ago(j["created"], now))}</td>'
            f'<td data-l="Took">{esc(dur(j["claimed"], j["finished"]) if j["claimed"] else "—")}</td></tr>' for j in recent)
        jobs_html = ("<h2>Recent jobs</h2><table class=stack><thead><tr><th>Job</th><th>Ticket</th><th>Recipe</th><th>Status</th><th>Worker</th><th>Queued</th><th>Took</th></tr></thead>"
                     f"<tbody>{rows}</tbody></table>")
    elif w.enabled and ready:
        jobs_html = '<h2>Recent jobs</h2><p class="muted">No verification job has run yet.</p>'
    edit = '<p><a href="/settings?section=workers">Edit these settings</a></p>'
    return head + edit + checks + online + jobs_html


def job_page(db, job_id: int) -> str | None:
    """The detail of one job, or None if there is no such job. The patch is not shown (it is on the PR); the log is untrusted text."""
    try:
        j = jobs.get(db, job_id)
    except sqlite3.OperationalError:
        return None
    if not j:
        return None
    meta = [("Status", badge(j["status"], JOB_BADGE.get(j["status"], ""))), ("Ticket", ticket_link(j["repo"], j["issue"])),
            ("Recipe", f'<code>{esc(j["recipe"])}</code> on <code>{esc(j["platform"])}</code>'), ("Base commit", f'<code>{esc(j["base_sha"][:12])}</code>'),
            ("Worker", esc(j["worker"] or "—")), ("Attempts", esc(j["attempts"])), ("Queued", esc(ts(j["created"]))),
            ("Took", esc(dur(j["claimed"], j["finished"]) if j["claimed"] else "—")), ("Exit code", esc("—" if j["exit_code"] is None else j["exit_code"]))]
    out = '<table class="meta">' + "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in meta) + "</table>"
    names = sorted(jobs.artifacts(db, job_id))
    if names:
        cells = ""
        for n in names:
            src = esc("/workerimg?" + urlencode({"job": int(job_id), "name": n}))
            cells += (f'<figure><a href="{src}" target="_blank"><img class="mockup" src="{src}" alt="{esc(n)}" loading="lazy"></a>'
                      f'<figcaption class="muted">{esc(n)}</figcaption></figure>')
        out += f'<h2>Screenshots</h2><div class="mockups">{cells}</div>'
    if j["log"]:
        out += f"<h2>Log (tail)</h2><pre>{esc(j['log'])}</pre>"
    return out + '<p><a href="/workers">← all workers and jobs</a></p>'


def artifact_png(db, job_id: int, name: str) -> bytes | None:
    if not jobs.NAME.fullmatch(name or ""):
        return None
    try:
        return jobs.artifacts(db, job_id).get(name)
    except sqlite3.OperationalError:
        return None
