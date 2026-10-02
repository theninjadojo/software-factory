"""HTML for the dashboard. Everything dynamic goes through esc(): ticket text, agent output and logs are untrusted."""
import html
import json
import re
import time

from .. import designfiles
from ..sanitize import md_render

GH_URL = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+/(pull|issues)/\d+$")
REPO = re.compile(r"^[\w.-]+/[\w.-]+$")

NAV = [("/", "Factory"), ("/needs", "Needs you"), ("/tickets", "Tickets"), ("/runs", "Runs"), ("/prs", "PRs & CI"), ("/events", "Events"),
       ("/settings", "Settings")]
PRIMARY = 4         # the first four stay on the phone tab bar; the rest sit behind "More"

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


def design_links(files) -> str:
    """One new-tab link per design file, only for files that pass designfiles.link_ok (the rows come from the database)."""
    return "<br>".join(f'<a href="{esc(f["url"])}" rel="noopener noreferrer" target="_blank">{esc(f["path"])}</a>'
                       for f in files or [] if designfiles.link_ok(f))


def ticket_link(repo: str, issue) -> str:
    label = f"{repo}#{issue}"
    return gh_link(f"https://github.com/{repo}/issues/{int(issue)}", label) if REPO.match(str(repo)) else esc(label)


def pr_links(urls: str) -> str:
    return " ".join(gh_link(u, "#" + u.rsplit("/", 1)[-1]) for u in (urls or "").split() if GH_URL.match(u))


def csrf_field(csrf: str) -> str:
    return f'<input type="hidden" name="csrf" value="{esc(csrf)}">'


def page(title: str, body: str, active: str, csrf: str, nav=None, flash: str | None = None, flash_kind: str = "ok", wide: bool = False, badges: dict | None = None, side: str = "") -> str:
    items = list(nav or NAV)
    count = lambda p: f' <span class="navbadge">{int(badges[p])}</span>' if badges and badges.get(p) else ""
    link = lambda p, n, extra="": f'<a href="{p}"{" class=\"" + ("active " if p == active else "") + extra + "\"" if (p == active or extra) else ""}>{esc(n)}{count(p)}</a>'
    links = "".join(link(p, n, "sec" if i >= PRIMARY else "") for i, (p, n) in enumerate(items))
    more = ("".join(link(p, n) for p, n in items[PRIMARY:]))
    more_on = " active" if any(p == active for p, _ in items[PRIMARY:]) else ""
    nav_html = (f'<nav aria-label="Main">{links}<details class="more{more_on}"><summary>More</summary><div class="more-list">{more}</div></details></nav>'
                if len(items) > PRIMARY else f"<nav aria-label=Main>{links}</nav>")
    if side:
        body = f'<div class="with-side">{side}<div class="side-body">{body}</div></div>'
    note = f'<div class="flash {esc(flash_kind)}" role="{"alert" if flash_kind == "bad" else "status"}">{esc(flash)}</div>' if flash else ""
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<meta name="color-scheme" content="dark"><title>{esc(title)} · software-factory</title><link rel="stylesheet" href="/static/style.css"></head><body>'
            f'<header><strong class="brand"><i></i>software-factory</strong>{nav_html}'
            f'<form method="post" action="/logout" class="signout">{csrf_field(csrf)}<button class="link">Sign out</button></form></header>'
            f'<main{" class=wide" if wide else ""}>{note}<h1>{esc(title)}</h1>{body}</main><script src="/static/app.js" defer></script></body></html>')


def login_page(error: str | None = None, setup_hint: bool = False) -> str:
    err = f'<div class="flash bad">{esc(error)}</div>' if error else ""
    hint = '<p class="muted">No password is set yet. Run <code>python3 -m factory.ui --set-password</code> on the host.</p>' if setup_hint else ""
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Sign in · software-factory</title><link rel="stylesheet" href="/static/style.css"></head><body class="login"><main>'
            f'<h1>software-factory</h1>{err}{hint}<form method="post" action="/login"><label>Password'
            '<input type="password" name="password" autocomplete="current-password" autofocus required></label>'
            '<button>Sign in</button></form></main></body></html>')



def overview_fragment(d: dict, csrf: str, selected: str | None = None, needs=None, forms=None, flt: str = "") -> str:
    """The Floor page body (see floor.py)."""
    from . import floor
    return floor.render(d, csrf, selected, needs, forms, flt)


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


def run_detail(r: dict, files=()) -> str:
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
    shown = [f for f in files or [] if designfiles.link_ok(f)]
    if shown:
        rows = "".join(f'<tr><td>{design_links([f])}</td><td>{esc(f["repo"])}</td><td>{pr_links(f.get("pr") or "") or "—"}</td></tr>' for f in shown)
        out += ("<h2>Design files</h2><table><thead><tr><th>File (opens on GitHub)</th><th>Repo</th><th>Draft PR</th></tr></thead>"
                f"<tbody>{rows}</tbody></table>")
    if cls:
        out += f"<h2>Classification</h2><pre>{esc(cls)}</pre>"
    if r["output"] and r["stage"] in DOC_LABEL and REPO.match(str(r["repo"])):
        out += f'<p>{doc_link(r["repo"], r["issue"], r["stage"], "Open the latest " + DOC_NOUN[r["stage"]] + " as a page")}</p>'
    if r["output"]:
        out += f"<h2>Output document</h2><pre class=\"doc\">{esc(r['output'])}</pre>"
    if r["log_tail"]:
        out += f"<h2>Agent log (tail)</h2><pre>{esc(r['log_tail'])}</pre>"
    return out + '<p><a href="/runs">← all runs</a></p>'


def tickets_page(rows: list[dict], counts: dict | None = None) -> str:
    counts = counts or {}
    if not rows:
        return '<p class="muted">The factory has not looked at any ticket yet.</p>'
    body = "".join(
        f'<tr><td>{ticket_link(t["repo"], t["issue"])}<br><span class="muted">{esc(t["title"])}</span></td><td>{badge(t["outcome"])}</td>'
        f'<td class="wrap">{esc(t["detail"])}</td><td>{esc(t["runs"])}{" · " + badge(t["last_run"]) if t["last_run"] else ""}</td>'
        f'<td title="{esc(ts(t["decided_at"]))}">{esc(ago(t["decided_at"]))}</td>'
        f'<td>{steps_cell(t, counts)}</td>'
        f'<td><a href="/labels/issue?repo={esc(t["repo"])}&amp;n={int(t["issue"])}">Edit labels</a></td></tr>' for t in rows)
    return ('<p class="muted">The latest decision for each ticket. <em>ignored</em> means the label was not applied by someone with write access.</p>'
            '<div class="scroll"><table><thead><tr><th>Ticket</th><th>Decision</th><th>Why</th><th>Runs</th><th>When</th><th>Steps</th><th>Labels</th></tr></thead>'
            f'<tbody>{body}</tbody></table></div>')


def events_page(events: list[dict], kind: str, older: int | None) -> str:
    opts = "".join(f'<option value="{esc(v)}"{" selected" if v == kind else ""}>{esc(l)}</option>'
                   for v, l in (("", "all events"), ("alert", "alerts"), ("decision", "decisions"), ("run", "runs"), ("error", "errors"), ("startup", "startups"), ("restart", "restarts")))
    form = f'<form method="get" class="filters"><select name="kind">{opts}</select><button>Filter</button></form>'
    more = f'<p class="pager"><a href="/events?kind={esc(kind)}&amp;before={int(older)}">older →</a></p>' if older else ""
    return form + events_table(events) + more


STEP_TITLES = {"analyze": "Analyze", "design": "Design", "architect": "Architect", "implement": "Implement", "review": "Review", "ci-fix": "CI fix"}
STEP_GLYPH = {"done": "✓", "running": "●", "failed": "✕", "queued": "○"}
STEP_BADGE = {"done": "good", "running": "warn", "failed": "bad", "queued": ""}


STEP_STAGE = {"analyze": "analyst", "design": "designer", "architect": "architect"}
DOC_LABEL = {"analyst": "Analysis", "designer": "Design", "architect": "Architecture"}
DOC_NOUN = {"analyst": "analysis", "designer": "design", "architect": "architecture"}


def doc_url(repo: str, issue, stage: str) -> str:
    return f"/ticket/doc?repo={esc(repo)}&amp;n={int(issue)}&amp;stage={esc(stage)}"


def doc_link(repo: str, issue, stage: str, label: str, new_tab: bool = False) -> str:
    """A link to a stage document page; nothing when the repo or stage is not one the page accepts."""
    if not REPO.match(str(repo)) or stage not in DOC_LABEL:
        return ""
    tab = ' target="_blank" rel="noopener"' if new_tab else ""
    return f'<a href="{doc_url(repo, issue, stage)}"{tab}>{esc(label)}<span aria-hidden="true">{" ↗" if new_tab else ""}</span></a>'


def step_summary(counts) -> str:
    done, total = counts
    return f"{int(done)} of {int(total)} steps done"


def step_files(s: dict, files_by_run: dict | None) -> str:
    links = design_links([f for i in s["run_ids"] for f in (files_by_run or {}).get(i, [])])
    return f'<br><span class="muted">Design files:</span><br>{links}' if links else ""


def pipeline(steps: list[dict], files_by_run: dict | None = None, repo: str = "", issue: int = 0, docs=()) -> str:
    """Read-only belt of stations, one per pipeline step. Every value is escaped; links are checked against GH_URL."""
    if not steps:
        return '<p class="muted">No pipeline steps yet. They appear when the factory starts working on this ticket.</p>'
    items = ""
    for s in steps:
        name, status = STEP_TITLES.get(s["step"], s["step"]), s["status"]
        label = f'{name}, {status}, {s["role"]} agent, {s["model"]}, {int(s["attempts"])} attempt(s)'
        items += (f'<li class="station {esc(status)}"><a class="station-link" href="#step-{esc(s["step"])}" aria-label="{esc(label)}">'
                  f'<span aria-hidden="true">{STEP_GLYPH.get(status, "○")}</span> <strong>{esc(name)}</strong></a><br>'
                  f'{badge(status.capitalize(), STEP_BADGE.get(status, ""))}'
                  f'<p class="muted">{esc(s["role"])} · {esc(s["harness"])} / {esc(s["model"])}<br>{int(s["attempts"])} attempt(s)</p></li>')
    run_links = lambda s: " ".join(f'<a href="/runs/{int(i)}">#{int(i)}</a>' for i in s["run_ids"])
    doc_of = lambda s: (" " + doc_link(repo, issue, STEP_STAGE[s["step"]], "Read document")) if STEP_STAGE.get(s["step"]) in docs else ""
    rows = "".join(
        f'<tr id="step-{esc(s["step"])}"><th>{esc(STEP_TITLES.get(s["step"], s["step"]))}</th><td>{badge(s["status"].capitalize(), STEP_BADGE.get(s["status"], ""))}</td>'
        f'<td>{esc(s["role"])} · {esc(s["harness"])} / {esc(s["model"])} <span class="muted">effort {esc(s["effort"])}</span></td>'
        f'<td>{int(s["attempts"])}</td><td>{run_links(s)}{doc_of(s)}</td>'
        f'<td>{pr_links(s["pr_urls"]) or "—"}{step_files(s, files_by_run)}</td></tr>' for s in steps)
    return (f'<ol class="belt">{items}</ol><div class="scroll"><table><thead><tr><th>Step</th><th>Status</th><th>Agent</th><th>Attempts</th>'
            f'<th>Runs</th><th>PRs</th></tr></thead><tbody>{rows}</tbody></table></div>')


def ticket_detail(repo: str, issue: int, steps: list[dict], files_by_run: dict | None = None, docs=()) -> str:
    done = sum(s["status"] == "done" for s in steps)
    head = f'<p>{ticket_link(repo, issue)} <span class="step-count" role="status">{esc(step_summary((done, len(steps))))}</span></p>'
    return head + "<h2>Pipeline</h2>" + pipeline(steps, files_by_run, repo, issue, docs) + '<p><a href="/tickets">← all tickets</a></p>'


def steps_cell(t: dict, counts: dict) -> str:
    c = counts.get((t["repo"], t["issue"]))
    if not c or not c[1] or not REPO.match(str(t["repo"])):
        return "—"
    return f'<a class="step-count" href="/ticket?repo={esc(t["repo"])}&amp;n={int(t["issue"])}">{int(c[0])}/{int(c[1])}</a>'


def doc_page(repo: str, issue: int, stage: str, have: list[str], doc: dict | None, text: str, source: str, notice: str = "", back: str = "/tickets") -> str:
    """A stage document, rendered from escaped Markdown. `text` is stored or GitHub-posted agent output and is never taken from the request."""
    tabs = " ".join(f'<a href="{doc_url(repo, issue, s)}"{" aria-current=\"page\"" if s == stage else ""}>{esc(DOC_LABEL[s])}</a>' if s in have or s == stage
                    else f'<span class="muted">{esc(DOC_LABEL[s])}</span>' for s in DOC_LABEL)
    head = (f'<p>{ticket_link(repo, issue)} · <a href="/ticket?repo={esc(repo)}&amp;n={int(issue)}">Pipeline</a></p>'
            f'<nav aria-label="Stage documents" class="doc-tabs">{tabs}</nav>')
    if not text:
        return head + ('<p class="muted">No document recorded for this stage yet.</p><p class="muted">The stage has not run, or its output was not kept.</p>'
                       f'{notice}<p><a href="{esc(back)}">← Back to questions</a></p>')
    body, heads = md_render(text)
    meta = " · ".join(x for x in (esc(DOC_LABEL[stage]), f'<a href="/runs/{int(doc["id"])}">run #{int(doc["id"])}</a>' if doc else "",
                                  esc(ago(doc["started"])) if doc else "", esc(source)) if x)
    toc = ("<details class=\"more\"><summary>Contents</summary><ul>" + "".join(f'<li><a href="#{i}">{esc(t)}</a></li>' for i, t in heads) + "</ul></details>") if heads else ""
    back_link = f'<p><a href="{esc(back)}">← Back to questions</a></p>'
    return (f'{head}<p class="muted">{meta}</p>{notice}{toc}<article class="docview">{body}</article>{back_link}')
