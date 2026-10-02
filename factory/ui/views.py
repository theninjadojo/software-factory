"""HTML for the dashboard. Everything dynamic goes through esc(): ticket text, agent output and logs are untrusted."""
import html
import json
import re
import time

GH_URL = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+/(pull|issues)/\d+$")
REPO = re.compile(r"^[\w.-]+/[\w.-]+$")

NAV = [("/", "Overview"), ("/runs", "Runs"), ("/tickets", "Tickets"), ("/prs", "PRs & CI"), ("/events", "Events"),
       ("/settings", "Settings"), ("/harnesses", "Harnesses"), ("/credentials", "Credentials"), ("/telegram", "Telegram")]

GOOD = {"pr", "stage", "passed", "success", "closed", "ok"}
WARN = {"running", "watching", "rate-limited", "human", "dry-run", "no-ci", "timed-out", "no-change", "paused"}
BAD = {"failed", "rejected", "interrupted", "error", "failure", "ignored"}


def esc(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


def ts(t) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(float(t))) if t else "—"


def ago(t, now=None) -> str:
    if not t:
        return "never"
    d = max(0, int((now or time.time()) - float(t)))
    return f"{d}s ago" if d < 90 else f"{d // 60}m ago" if d < 5400 else f"{d // 3600}h ago" if d < 172800 else f"{d // 86400}d ago"


def dur(a, b=None) -> str:
    if not a:
        return "—"
    d = int((b or time.time()) - float(a))
    return f"{d}s" if d < 120 else f"{d // 60}m {d % 60}s" if d < 7200 else f"{d // 3600}h {(d % 3600) // 60}m"


def badge(text, kind: str | None = None) -> str:
    t = str(text or "")
    cls = kind or ("good" if t in GOOD else "warn" if t in WARN else "bad" if t in BAD else "")
    return f'<span class="badge {cls}">{esc(t)}</span>'


def gh_link(url: str, label: str | None = None) -> str:
    u = str(url or "")
    return f'<a href="{esc(u)}" rel="noopener noreferrer" target="_blank">{esc(label or u)}</a>' if GH_URL.match(u) else esc(label or u)


def ticket_link(repo: str, issue) -> str:
    label = f"{repo}#{issue}"
    return gh_link(f"https://github.com/{repo}/issues/{int(issue)}", label) if REPO.match(str(repo)) else esc(label)


def pr_links(urls: str) -> str:
    return " ".join(gh_link(u, "#" + u.rsplit("/", 1)[-1]) for u in (urls or "").split() if GH_URL.match(u))


def csrf_field(csrf: str) -> str:
    return f'<input type="hidden" name="csrf" value="{esc(csrf)}">'


def page(title: str, body: str, active: str, csrf: str, nav=None, flash: str | None = None, flash_kind: str = "ok") -> str:
    links = "".join(f'<a href="{p}"{" class=active" if p == active else ""}>{esc(n)}</a>' for p, n in (nav or NAV))
    note = f'<div class="flash {esc(flash_kind)}" role="{"alert" if flash_kind == "bad" else "status"}">{esc(flash)}</div>' if flash else ""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>{esc(title)} · software-factory</title><link rel="stylesheet" href="/static/style.css"></head><body>'
            f'<header><strong class="brand">software-factory</strong><nav>{links}</nav>'
            f'<form method="post" action="/logout" class="signout">{csrf_field(csrf)}<button class="link">Sign out</button></form></header>'
            f'<main>{note}<h1>{esc(title)}</h1>{body}</main><script src="/static/app.js" defer></script></body></html>')


def login_page(error: str | None = None, setup_hint: bool = False) -> str:
    err = f'<div class="flash bad">{esc(error)}</div>' if error else ""
    hint = '<p class="muted">No password is set yet. Run <code>python3 -m factory.ui --set-password</code> on the host.</p>' if setup_hint else ""
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Sign in · software-factory</title><link rel="stylesheet" href="/static/style.css"></head><body class="login"><main>'
            f'<h1>software-factory</h1>{err}{hint}<form method="post" action="/login"><label>Password'
            '<input type="password" name="password" autocomplete="current-password" autofocus required></label>'
            '<button>Sign in</button></form></main></body></html>')



def overview_fragment(d: dict, csrf: str) -> str:
    st, now = d["status"], time.time()
    cfg = d["cfg"]
    last_ok = float(st["last_poll_ok"]["value"]) if "last_poll_ok" in st else None
    stale = last_ok is None or now - last_ok > max(120, 3 * cfg["poll_seconds"])
    paused = d["paused"]
    err = (st.get("last_error") or {}).get("value", "")
    # Runs happen on worker threads and polling goes on during them, so a missed poll means trouble even while a run is going.
    health = badge("orchestrator not reporting" if stale else "orchestrator healthy", "bad" if stale else "good")
    mode = badge("LIVE" if cfg["live"] else "dry-run", "warn" if cfg["live"] else "")
    pause_btn = ('<form method="post" action="/action/resume" class="inline">' + csrf_field(csrf) + '<button>Resume</button></form>'
                 if paused else '<form method="post" action="/action/pause" class="inline">' + csrf_field(csrf) + '<button>Pause</button></form>')
    cards = (
        f'<div class="cards"><div class="card"><h3>Health</h3>{health}<p class="muted">last poll {esc(ago(last_ok))}</p>'
        f'{f"<p class=bad-text>last error: {esc(err)}</p>" if err else ""}</div>'
        f'<div class="card"><h3>Mode</h3>{mode} <span class="muted">every {esc(cfg["poll_seconds"])}s</span></div>'
        f'<div class="card"><h3>Queue control</h3>{badge("paused: " + paused, "warn") if paused else badge("running", "good")} {pause_btn}</div>'
        f'<div class="card"><h3>Classifier</h3>{esc(cfg["classifier"])}<p class="muted">Telegram: {esc(cfg["telegram"])} · CI watch: {esc(cfg["ci"])}</p></div></div>')
    running, queued = d["running"], d["queued"]
    now_html = f'<p class="muted">{len(running)} of {esc(cfg["max_parallel"])} slot(s) in use · {len(queued)} queued</p>'
    now_html += "".join(
        f'<p>{badge("running")} <a href="/runs/{int(run["id"])}">{esc(run["kind"])}{" " + esc(run["stage"]) if run["stage"] else ""}</a> on '
        f'{ticket_link(run["repo"], run["issue"])} — {esc(run["title"])}<br><span class="muted">{esc(run["model"])} ({esc(run["effort"])}) · '
        f'started {esc(ago(run["started"]))} · running for {esc(dur(run["started"]))}</span></p>' for run in running)
    now_html += "".join(
        f'<p>{badge("queued")} {esc(q.get("kind", ""))} on {ticket_link(q.get("repo", ""), q["issue"])} — {esc(q.get("title", ""))}'
        '<br><span class="muted">waiting for a free slot</span></p>' for q in queued)
    if not running and not queued:
        now_html += "<p class=muted>Nothing is running.</p>"
    out = cards + f"<h2>Now</h2>{now_html}<h2>Recent runs</h2>{runs_table(d['runs'])}"
    if d["prs"]:
        out += f"<h2>PRs being watched</h2>{prs_table(d['prs'])}"
    out += f'<h2>Timeline</h2>{events_table(d["events"])}<p><a href="/events">All events →</a></p>'
    return out


def runs_table(runs: list[dict]) -> str:
    if not runs:
        return '<p class="muted">No runs yet.</p>'
    rows = "".join(
        f'<tr><td><a href="/runs/{int(r["id"])}">#{int(r["id"])}</a></td><td title="{esc(ts(r["started"]))}">{esc(ago(r["started"]))}</td>'
        f'<td>{esc(r["kind"])}{" · " + esc(r["stage"]) if r["stage"] else ""}</td><td>{ticket_link(r["repo"], r["issue"])}<br><span class="muted">{esc(r["title"])}</span></td>'
        f'<td>{esc(r["model"])} <span class="muted">{esc(r["effort"])}</span></td><td>{badge(r["status"])}</td>'
        f'<td>{esc(dur(r["started"], r["finished"]))}</td><td>{pr_links(r["pr_urls"])}</td></tr>' for r in runs)
    return ('<div class="scroll"><table><thead><tr><th>Run</th><th>Started</th><th>Kind</th><th>Ticket</th><th>Model</th><th>Status</th>'
            f'<th>Took</th><th>PRs</th></tr></thead><tbody>{rows}</tbody></table></div>')


def events_table(events: list[dict]) -> str:
    if not events:
        return '<p class="muted">No events yet.</p>'

    def row(e: dict) -> str:
        bad = e["kind"].startswith(("error", "alert:failure"))
        ticket = ticket_link(e["repo"], e["issue"]) if e["repo"] and e["issue"] else ""
        run = " " + f'<a href="/runs/{int(e["run_id"])}">run</a>' if e["run_id"] else ""
        return (f'<tr><td class="nowrap" title="{esc(ts(e["ts"]))}">{esc(ago(e["ts"]))}</td>'
                f'<td>{badge(e["kind"], "bad" if bad else "")}</td><td>{ticket}{run}</td><td class="wrap">{esc(e["message"])}</td></tr>')

    rows = "".join(row(e) for e in events)
    return f'<div class="scroll"><table><thead><tr><th>When</th><th>Kind</th><th>Ticket</th><th>Message</th></tr></thead><tbody>{rows}</tbody></table></div>'


def prs_table(prs: list[dict]) -> str:
    if not prs:
        return '<p class="muted">No pull requests are being tracked yet.</p>'
    rows = "".join(
        f'<tr><td>{gh_link(f"https://github.com/{p["repo"]}/pull/{int(p["number"])}", p["repo"] + "#" + str(int(p["number"]))) if REPO.match(p["repo"]) else esc(p["repo"])}</td>'
        f'<td>{ticket_link(p["issue_repo"], p["issue_num"])}</td><td>{badge(p["status"])}</td><td>{esc(p["rounds"])}</td>'
        f'<td class="wrap">{esc(p["summary"])}</td><td title="{esc(ts(p["updated"]))}">{esc(ago(p["updated"]))}</td></tr>' for p in prs)
    return ('<div class="scroll"><table><thead><tr><th>PR</th><th>Ticket</th><th>CI</th><th>Fix rounds</th><th>Summary</th><th>Updated</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>')


def runs_page(runs: list[dict], status: str, repo: str, page_no: int, has_more: bool) -> str:
    opts = "".join(f'<option value="{esc(v)}"{" selected" if v == status else ""}>{esc(v or "any status")}</option>'
                   for v in ("", "running", "pr", "stage", "failed", "rejected", "no-change", "rate-limited", "interrupted"))
    form = (f'<form method="get" class="filters"><select name="status">{opts}</select>'
            f'<input name="repo" placeholder="owner/repo" value="{esc(repo)}"><button>Filter</button></form>')
    def link(label: str, p: int) -> str:
        return f'<a href="/runs?status={esc(status)}&amp;repo={esc(repo)}&amp;page={p}">{label}</a>'

    nav = '<p class="pager">' + (link("← newer", page_no - 1) if page_no > 0 else "") + " " + (link("older →", page_no + 1) if has_more else "") + "</p>"
    return form + runs_table(runs) + nav


def run_detail(r: dict) -> str:
    try:
        cls = json.dumps(json.loads(r["classification"]), indent=2) if r["classification"] else ""
    except ValueError:
        cls = r["classification"]
    meta = [("Status", badge(r["status"])), ("Kind", esc(r["kind"] + (" · " + r["stage"] if r["stage"] else ""))),
            ("Ticket", ticket_link(r["repo"], r["issue"]) + " — " + esc(r["title"])), ("Agent", esc(f'{r["harness"]} · {r["model"]} · effort {r["effort"]}')),
            ("Started", esc(ts(r["started"]))), ("Took", esc(dur(r["started"], r["finished"]))), ("Detail", esc(r["detail"])),
            ("Pull requests", pr_links(r["pr_urls"]) or "—")]
    table = "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in meta)
    out = f'<table class="meta">{table}</table>'
    if cls:
        out += f"<h2>Classification</h2><pre>{esc(cls)}</pre>"
    if r["output"]:
        out += f"<h2>Output document</h2><pre class=\"doc\">{esc(r['output'])}</pre>"
    if r["log_tail"]:
        out += f"<h2>Agent log (tail)</h2><pre>{esc(r['log_tail'])}</pre>"
    return out + '<p><a href="/runs">← all runs</a></p>'


def tickets_page(rows: list[dict]) -> str:
    if not rows:
        return '<p class="muted">The factory has not looked at any ticket yet.</p>'
    body = "".join(
        f'<tr><td>{ticket_link(t["repo"], t["issue"])}<br><span class="muted">{esc(t["title"])}</span></td><td>{badge(t["outcome"])}</td>'
        f'<td class="wrap">{esc(t["detail"])}</td><td>{esc(t["runs"])}{" · " + badge(t["last_run"]) if t["last_run"] else ""}</td>'
        f'<td title="{esc(ts(t["decided_at"]))}">{esc(ago(t["decided_at"]))}</td>'
        f'<td><a href="/labels/issue?repo={esc(t["repo"])}&amp;n={int(t["issue"])}">Edit labels</a></td></tr>' for t in rows)
    return ('<p class="muted">The latest decision for each ticket. <em>ignored</em> means the label was not applied by someone with write access.</p>'
            '<div class="scroll"><table><thead><tr><th>Ticket</th><th>Decision</th><th>Why</th><th>Runs</th><th>When</th><th>Labels</th></tr></thead>'
            f'<tbody>{body}</tbody></table></div>')


def events_page(events: list[dict], kind: str, older: int | None) -> str:
    opts = "".join(f'<option value="{esc(v)}"{" selected" if v == kind else ""}>{esc(l)}</option>'
                   for v, l in (("", "all events"), ("alert", "alerts"), ("decision", "decisions"), ("run", "runs"), ("error", "errors"), ("startup", "startups"), ("restart", "restarts")))
    form = f'<form method="get" class="filters"><select name="kind">{opts}</select><button>Filter</button></form>'
    more = f'<p class="pager"><a href="/events?kind={esc(kind)}&amp;before={int(older)}">older →</a></p>' if older else ""
    return form + events_table(events) + more
