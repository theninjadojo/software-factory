"""The Workers page: verification workers, the job queue and each job's log and screenshots. Read-only (the orchestrator's database is
opened read-only). Everything a worker sent is untrusted text: it is escaped here, and screenshots are served only by the UI behind its login."""
import socket
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlencode

from .. import jobs, version, workerupdate
from ..verify import ONLINE_SECONDS
from .views import badge, csrf_field, dur, esc, ago, ticket_link, ts

INSTALL_URL = "https://github.com/theninjadojo/software-factory/releases/latest/download/install-worker.sh"

NOT_RUNNING = ("The UI starts it within a few seconds of workers being turned on; if this stays, the UI's log says why "
               "(for example, a [workers] setting it cannot use).")
JOB_BADGE = {"passed": "good", "queued": "warn", "claimed": "warn", "failed": "bad", "error": "bad", "cancelled": ""}


def _rows(db) -> tuple[list, list, bool]:
    try:
        return jobs.online_workers(db, 0, float("inf")), jobs.recent(db, 30), True
    except sqlite3.OperationalError:
        return [], [], False                     # the orchestrator has not created the tables yet


def api_up(listen: str) -> bool:
    """Whether something accepts connections on the worker API address (a TCP connect, nothing is sent)."""
    host, _, port = listen.rpartition(":")
    try:
        with socket.create_connection((host or "127.0.0.1", int(port)), timeout=1):
            return True
    except (OSError, ValueError):
        return False


def install_command(listen: str, token: str, recipes: str = "web") -> str:
    """The one-liner to run on the worker machine. The URL is the tunnel's local end when the API is loopback-only."""
    port = listen.rpartition(":")[2]
    return f'curl -fsSL {INSTALL_URL} | FACTORY_URL=http://127.0.0.1:{port} WORKER_TOKEN={token} WORKER_RECIPES="{recipes}" bash'


def add_form(csrf: str) -> str:
    return ('<h2>Add a worker</h2><form method="post" action="/workers/add" class="row">' + csrf_field(csrf) +
            '<label>Name <input name="name" value="my-worker" pattern="[a-z0-9][a-z0-9-]{0,40}" required></label> '
            '<label>Recipes <input name="recipes" value="web" pattern="[a-z ]{1,60}" title="web, screens, android, ios"></label> '
            '<button>Create token and show install command</button></form>')


def created_page(cfg, name: str, token: str, recipes: str) -> str:
    port = cfg.workers.listen.rpartition(":")[2]
    up = api_up(cfg.workers.listen)
    status = (badge("running", "good") + f' the worker API answers on <code>{esc(cfg.workers.listen)}</code>.' if up else
              badge("not running", "bad") + f' nothing answers on <code>{esc(cfg.workers.listen)}</code> yet. ' + NOT_RUNNING)
    cmd = install_command(cfg.workers.listen, token, recipes)
    return (f'<h2>Worker {esc(name)} created</h2><p><strong>Copy this now: the token is shown once.</strong></p>'
            f'<p>Worker API: {status}</p>'
            f'<p>1. On the worker machine, open a tunnel to this host (the API listens on loopback only):</p>'
            f'<pre>ssh -N -L {esc(port)}:127.0.0.1:{esc(port)} &lt;user&gt;@&lt;this-host&gt;</pre>'
            f'<p>2. On the worker machine, install and connect it:</p><pre>{esc(cmd)}</pre>'
            f'<p class="muted">The worker appears in the list below within seconds of its first poll.</p>'
            '<p><a href="/workers">← back to workers</a></p>')


def progress_note(j: dict, now) -> str:
    """What a running job's worker last said it was doing, and how long ago (the text is untrusted)."""
    if j["status"] != "claimed" or not j.get("progress"):
        return ""
    return f'<div class="muted">{esc(j["progress"])} · {esc(ago(j["progress_at"], now))}</div>'


def workers_page(cfg, db, now: float | None = None, csrf: str = "") -> str:
    now = time.time() if now is None else now
    w = cfg.workers
    known, recent, ready = _rows(db)
    if not w.enabled:
        head = ('<p class="muted">Verification workers are off. A worker is a machine you own (a Mac, a networked Linux box) that builds and tests an agent\'s patch '
                'before it is pushed. <a href="/settings?section=workers">Turn them on in Settings</a> (see <code>docs/workers.md</code>).</p>')
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
            have = version.current()
            try:
                req = {r[0] for r in db.execute("SELECT worker FROM worker_updates")}
            except sqlite3.OperationalError:
                req = set()                                  # an older database the orchestrator has not migrated yet

            def version_cell(k: dict) -> str:
                v = k.get("app_version") or ""
                if not v:
                    return "—"
                if not workerupdate.behind(v, have):
                    return esc(v)
                if k["name"] in req:
                    return f'{esc(v)} {badge("updating", "warn")}'
                return (f'{esc(v)} <form method="post" action="/workers/update" class="inline">{csrf_field(csrf)}'
                        f'<input type="hidden" name="worker" value="{esc(k["name"])}"><button>Update to {esc(have)}</button></form>')
            rows = "".join(
                f'<tr><td data-l="Worker">{esc(k["name"])}</td><td data-l="Platform">{esc(k["platform"])}</td>'
                f'<td data-l="Version">{version_cell(k)}</td>'
                f'<td data-l="Recipes">{esc(k["recipes"].replace(",", ", ") or "—")}</td><td data-l="Last seen">{esc(ago(k["last_seen"], now))}</td>'
                f'<td data-l="State">{badge("online", "good") if now - k["last_seen"] <= ONLINE_SECONDS else badge("offline", "bad")}</td></tr>' for k in known)
            auto = workerupdate.auto(Path(cfg.db_path).parent)
            online = ("<h2>Workers</h2><table class=stack><thead><tr><th>Worker</th><th>Platform</th><th>Version</th><th>Recipes</th><th>Last seen</th><th>State</th></tr></thead>"
                      f"<tbody>{rows}</tbody></table>"
                      f'<form method="post" action="/workers/auto-update" class="card">{csrf_field(csrf)}'
                      f'<label class="check"><input type="checkbox" name="on" value="1"{" checked" if auto else ""}> Update workers by themselves when they are behind the factory</label>'
                      '<div class="muted">A worker follows the release the factory runs. When it is idle it asks, downloads the files through the factory (it needs no GitHub '
                      'access), installs them with its own update script, checks them (rolling back if they fail) and restarts through its service. '
                      'A worker installed before this feature needs one manual update first.</div><button>Save</button></form>')
        else:
            online = '<h2>Workers</h2><p class="muted">No worker has connected yet. Add one below.</p>'
        online += add_form(csrf) + ("" if api_up(w.listen) else f'<p class="muted">{badge("not running", "bad")} the worker API is not answering on <code>{esc(w.listen)}</code>. {NOT_RUNNING}</p>')
    jobs_html = ""
    if recent:
        rows = "".join(
            f'<tr><td data-l="Job"><a href="/workers/job?id={int(j["id"])}">#{int(j["id"])}</a></td><td data-l="Ticket">{ticket_link(j["repo"], j["issue"]) if j["issue"] else esc(j["repo"])}</td>'
            f'<td data-l="Recipe"><code>{esc(j["recipe"])}</code></td><td data-l="Status">{badge(j["status"], JOB_BADGE.get(j["status"], ""))}{progress_note(j, now)}</td>'
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
    meta = [("Status", badge(j["status"], JOB_BADGE.get(j["status"], ""))), ("Ticket", ticket_link(j["repo"], j["issue"]) if j["issue"] else esc(j["repo"])),
            ("Recipe", f'<code>{esc(j["recipe"])}</code> on <code>{esc(j["platform"])}</code>'), ("Base commit", f'<code>{esc(j["base_sha"][:12])}</code>'),
            ("Worker", esc(j["worker"] or "—")), ("Attempts", esc(j["attempts"])), ("Queued", esc(ts(j["created"]))),
            ("Took", esc(dur(j["claimed"], j["finished"]) if j["claimed"] else "—")), ("Exit code", esc("—" if j["exit_code"] is None else j["exit_code"]))]
    if j.get("progress"):
        meta.insert(1, ("Progress" if j["status"] == "claimed" else "Last step", esc(j["progress"])))
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
