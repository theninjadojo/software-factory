"""HTML for the dashboard. Everything dynamic goes through esc(): ticket text, agent output and logs are untrusted."""
import html
import json
import re
import time
from urllib.parse import urlencode

from .. import db as dbm
from .. import designfiles
from .. import version
from ..sanitize import md_render

GH_URL = re.compile(r"^https://github\.com/[\w.-]+/[\w.-]+/(pull|issues)/\d+$")
REPO = re.compile(r"^[\w.-]+/[\w.-]+$")

NAV = [("/", "Factory"), ("/tickets", "Tickets"), ("/events", "Events"), ("/settings", "Settings")]
# Needs you, Runs and PRs & CI are part of Tickets: those pages light up the Tickets tab, and the needs count sits on it.
TICKET_PAGES = {"/needs", "/runs", "/prs", "/ticket", "/ticket/doc", "/ticket/images", "/ticket/review", "/labels", "/labels/issue"}
ICON = '<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 16 16\'%3E%3Ccircle cx=\'8\' cy=\'8\' r=\'6\' fill=\'%23e0a030\'/%3E%3C/svg%3E">'
BRAND = ('<svg class="brand-i" viewBox="0 0 24 24" aria-hidden="true"><path d="M3 21V10l6 4V10l6 4V6h6v15z"/></svg>')
MENU_ICON = '<svg class="ph-menu-i" viewBox="0 0 20 20" aria-hidden="true"><path d="M3 5h14M3 10h14M3 15h14"/></svg>'
PRIMARY = 3         # the first three stay on the phone tab bar (with More: four buttons); the rest sit behind "More"

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


def tok(n) -> str:
    """A token count in short form (1.2k, 3.4M); a dash where the harness reported none."""
    if n is None:
        return "—"
    return str(n) if n < 1000 else f"{n / 1000:.1f}k" if n < 1_000_000 else f"{n / 1_000_000:.1f}M"


def tokens_in_out(r: dict) -> str:
    t = dbm.run_tokens(r)
    return "—" if t["in"] is None and t["out"] is None else f'{tok(t["in"])} in · {tok(t["out"])} out'


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


def mockup_img(f) -> str:
    """The stored preview image of a recorded design file, inline (served by the UI itself, behind the login)."""
    if not designfiles.link_ok(f) or "/previews/" not in f["path"] or not f["path"].endswith(".png"):
        return ""
    src = "/mockup?" + urlencode({"repo": f["repo"], "path": f["path"]})
    return f'<a href="{esc(src)}" target="_blank"><img class="mockup" src="{esc(src)}" alt="{esc(f["path"])}" loading="lazy"></a>'


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


UPDATE = {"tag": "", "url": ""}      # set by the server from the cached release check; the tag and url are validated there


def update_banner() -> str:
    tag, url = UPDATE.get("tag", ""), UPDATE.get("url", "")
    if not tag or not url.startswith("https://github.com/") or not re.fullmatch(r"v?\d{1,4}\.\d{1,4}\.\d{1,4}", tag):
        return ""
    return (f'<div class="flash ok" role="status">Shikumi {esc(tag)} is available (you have {esc(version.current())}). '
            f'<a href="{esc(url)}" rel="noopener noreferrer" target="_blank">Release notes</a>. To update, run <code>./scripts/update.sh</code> on the host.</div>')


def page(title: str, body: str, active: str, csrf: str, nav=None, flash: str | None = None, flash_kind: str = "ok", wide: bool = False, badges: dict | None = None, side: str = "",
         bare: bool = False) -> str:
    """bare: the body draws its own heading (the Factory and Tickets screens)."""
    items = list(nav or NAV)
    if active in TICKET_PAGES or active.startswith("/runs/"):
        active = "/tickets"
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
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            f'<meta name="color-scheme" content="dark"><title>{esc(title)} · Shikumi</title>{ICON}<link rel="stylesheet" href="/static/style.css"></head><body>'
            f'<header><a class="brand" href="/">{BRAND}Software factory</a>{nav_html}'
            f'<form method="post" action="/logout" class="signout">{csrf_field(csrf)}<button class="link">Sign out</button></form>'
            f'<details class="ph-menu"><summary aria-label="Menu">{MENU_ICON}</summary><div class="ph-menu-list">'
            + "".join(f'<a href="{p}"{" class=active" if p == active else ""}>{esc(n)}{count(p)}</a>' for p, n in items)
            + f'<form method="post" action="/logout">{csrf_field(csrf)}<button class="link">Sign out</button></form></div></details></header>'
            f'<main{" class=wide" if wide else ""}>{update_banner()}{note}{"" if bare else f"<h1>{esc(title)}</h1>"}{body}<p class="muted ver">Shikumi {esc(version.current())}</p></main><script src="/static/app.js" defer></script></body></html>')


def login_page(error: str | None = None, setup_hint: bool = False) -> str:
    err = f'<div class="flash bad">{esc(error)}</div>' if error else ""
    hint = '<p class="muted">No password is set yet. Run <code>python3 -m factory.ui --set-password</code> on the host.</p>' if setup_hint else ""
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            f'<title>Sign in · Shikumi</title>{ICON}<link rel="stylesheet" href="/static/style.css"></head><body class="login"><main>'
            f'<h1>Shikumi <span class="muted">仕組み</span></h1>{err}{hint}<form method="post" action="/login"><label>Password'
            '<input type="password" name="password" autocomplete="current-password" autofocus required></label>'
            '<button>Sign in</button></form></main></body></html>')



def overview_fragment(d: dict, csrf: str, selected: str | None = None, needs=None, forms=None, flt: str = "", view: str = "", ask: bool = False) -> str:
    """The Floor page body (see floor.py)."""
    from . import floor
    return floor.render(d, csrf, selected, needs, forms, flt, view, ask)


CARD = "stack"     # tables with this class turn into stacked cards on phones (each cell shows its column from data-l)
RUN_HEADS = ["Run", "Started", "Kind", "Ticket", "Model", "Status", "Took", "Tokens", "PRs"]
EVENT_HEADS = ["When", "Kind", "Ticket", "Message"]
PR_HEADS = ["PR", "Ticket", "CI", "Fix rounds", "Summary", "Updated"]
TICKET_HEADS = ["Ticket", "Decision", "Why", "Runs", "When", "Steps", "Labels"]


def cards_table(heads: list[str], rows: str) -> str:
    return (f'<div class="scroll"><table class="{CARD}"><thead><tr>{"".join(f"<th>{esc(h)}</th>" for h in heads)}</tr></thead>'
            f'<tbody>{rows}</tbody></table></div>')


def trow(heads: list[str], cells: list, attrs: str = "") -> str:
    """One row for cards_table. A cell is escaped HTML or an (html, attributes) pair; the column name goes in data-l for the phone layout."""
    out = ""
    for h, c in zip(heads, cells):
        c, extra = c if isinstance(c, tuple) else (c, "")
        out += f'<td data-l="{esc(h)}"{extra}>{c}</td>'
    return f"<tr{attrs}>{out}</tr>"


def runs_table(runs: list[dict]) -> str:
    if not runs:
        return '<p class="muted">No runs yet.</p>'
    h = RUN_HEADS
    rows = "".join(trow(h, [
        f'<a href="/runs/{int(r["id"])}">#{int(r["id"])}</a>', (esc(ago(r["started"])), f' title="{esc(ts(r["started"]))}"'),
        esc(r["kind"]) + (" · " + esc(r["stage"]) if r["stage"] else ""), f'{ticket_link(r["repo"], r["issue"])}<br><span class="muted">{esc(r["title"])}</span>',
        f'{esc(r["model"])} <span class="muted">{esc(r["effort"])}</span>', badge(r["status"]), esc(dur(r["started"], r["finished"])), esc(tokens_in_out(r)), pr_links(r["pr_urls"])]) for r in runs)
    return cards_table(h, rows)


def events_table(events: list[dict]) -> str:
    if not events:
        return '<p class="muted">No events yet.</p>'
    h = EVENT_HEADS

    def row(e: dict) -> str:
        bad = e["kind"].startswith(("error", "alert:failure"))
        ticket = ticket_link(e["repo"], e["issue"]) if e["repo"] and e["issue"] else ""
        run = " " + f'<a href="/runs/{int(e["run_id"])}">run</a>' if e["run_id"] else ""
        return trow(h, [(esc(ago(e["ts"])), f' class="nowrap" title="{esc(ts(e["ts"]))}"'), badge(e["kind"], "bad" if bad else ""), ticket + run,
                        (esc(e["message"]), ' class="wrap"')])

    return cards_table(h, "".join(row(e) for e in events))


def prs_table(prs: list[dict]) -> str:
    if not prs:
        return '<p class="muted">No pull requests are being tracked yet.</p>'
    h = PR_HEADS
    rows = "".join(trow(h, [
        gh_link(f"https://github.com/{p['repo']}/pull/{int(p['number'])}", p["repo"] + "#" + str(int(p["number"]))) if REPO.match(p["repo"]) else esc(p["repo"]),
        ticket_link(p["issue_repo"], p["issue_num"]), badge(p["status"]), esc(p["rounds"]), (esc(p["summary"]), ' class="wrap"'),
        (esc(ago(p["updated"])), f' title="{esc(ts(p["updated"]))}"')]) for p in prs)
    return cards_table(h, rows)


STAGE_WHAT = {"analyst": "Analyst", "designer": "Designer", "architect": "Architect"}


def run_what(r: dict) -> str:
    """Plain-language label for a run; unknown kinds fall back to the raw kind."""
    if r["kind"] == "build":
        return "Build opened PR" if r["pr_urls"] else "Build"
    if r["kind"] == "fix":
        return "CI fix round"
    if r["kind"] == "stage" and r["stage"] in STAGE_WHAT:
        return STAGE_WHAT[r["stage"]]
    return str(r["kind"]) + (" " + str(r["stage"]) if r["stage"] else "")


def runs_summary_line(s: dict | None) -> str:
    if not s or not s["total"]:
        return ""
    parts = [f'{int(s["total"])} today', f'{int(s["passed"])} passed', f'{int(s["failed"])} failed']
    if s["avg"] is not None:
        parts.append(f'average {dur(0.001, 0.001 + float(s["avg"]))}')
    t = s.get("tokens") or {}
    if t.get("in") is not None or t.get("out") is not None:
        parts.append(f'tokens {tok(t.get("in"))} in · {tok(t.get("out"))} out')
    return '<p class="summary">' + esc(" · ".join(parts)) + "</p>"


def runs_list(runs: list[dict]) -> str:
    if not runs:
        return '<p class="muted">No runs yet.</p>'
    rows = "".join(
        f'<tr><td data-l="Run"><a href="/runs/{int(r["id"])}">Run #{int(r["id"])}</a><br><span class="muted">{ticket_link(r["repo"], r["issue"])}</span></td>'
        f'<td data-l="What">{esc(run_what(r))}<br><span class="muted">{esc(r["title"])}</span></td><td data-l="Status">{badge(r["status"])}</td><td data-l="Model">{esc(r["model"])}</td>'
        f'<td data-l="Took">{esc(dur(r["started"], r["finished"]))}</td><td data-l="Tokens">{esc(tokens_in_out(r))}</td><td data-l="Started" title="{esc(ts(r["started"]))}">{esc(ago(r["started"]))}</td></tr>' for r in runs)
    return ('<div class="scroll"><table class="stack"><thead><tr><th>Run</th><th>What</th><th>Status</th><th>Model</th><th>Took</th><th>Tokens</th><th>Started</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>')


def chips(options, current: str, href) -> str:
    """Filter chips as plain links; `href(value)` builds the query string. The current one is marked with aria-current."""
    def chip(v: str, label: str) -> str:
        on = v == current
        return f'<a class="chip{" current" if on else ""}"{" aria-current=true" if on else ""} href="{esc(href(v))}">{esc(label)}</a>'

    return '<p class="chips">' + " ".join(chip(v, label) for v, label in options) + "</p>"


def events_list(events: list[dict]) -> str:
    if not events:
        return '<p class="muted">Nothing important has happened yet.</p>'

    def row(e: dict) -> str:
        k = e["kind"]
        state, cls = ("failed", "bad") if k.startswith(("error", "alert:failure")) else ("needs attention", "warn") if k.startswith("alert") else ("ok", "good")
        ticket = ticket_link(e["repo"], e["issue"]) if e["repo"] and e["issue"] else ""
        return (f'<li><span class="nowrap muted" title="{esc(ts(e["ts"]))}">{esc(ago(e["ts"]))}</span>'
                f'<span class="dot {cls}" role="img" aria-label="{state}" title="{state}"></span>'
                f'<span class="wrap">{esc(e["message"])}</span><span class="nowrap">{ticket}</span></li>')

    return '<ul class="evlist">' + "".join(row(e) for e in events) + "</ul>"


def prs_summary_line(prs: list[dict]) -> str:
    open_ = [p for p in prs if p["status"] != "closed"]
    if not open_:
        return ""
    return (f'<p class="summary">{len(open_)} open · {sum(p["status"] == "passed" for p in open_)} passing · '
            f'{sum(p["status"] == "failed" for p in open_)} failing</p>')


def pr_round_line(p: dict, limit: int) -> str:
    n, st = int(p["rounds"] or 0), p["status"]
    if st == "failed":
        return f"Fix round {n} of {limit} failed" if n else "CI failed; no fix rounds used"
    if st == "watching":
        return f"Fix round {n} of {limit} running" if n else "Waiting for CI"
    return f"Fix round {n} of {limit} needed" if n else "No fix rounds needed"


def prs_cards(prs: list[dict], fix_rounds: int = 1) -> str:
    if not prs:
        return '<p class="muted">No pull requests are being tracked yet.</p>'

    def card(p: dict) -> str:
        cls = "good" if p["status"] in GOOD else "bad" if p["status"] in BAD else "warn"
        ref = f'{p["repo"]}#{int(p["number"])}'
        url = f'https://github.com/{p["repo"]}/pull/{int(p["number"])}'
        button = (f'<a class="button" href="{esc(url)}" rel="noopener noreferrer" target="_blank">Open on GitHub</a>'
                  if REPO.match(p["repo"]) and GH_URL.match(url) else "")
        return (f'<li class="prcard {cls}"><h3>{esc(p.get("title") or ref)}</h3>'
                f'<p class="muted">{esc(ref)} · {esc(ago(p["watch_started"] or p["updated"]))} · ticket {ticket_link(p["issue_repo"], p["issue_num"])}</p>'
                f'<p>CI: {badge(p["status"], cls)}</p><p>{esc(pr_round_line(p, fix_rounds))}</p>{button}</li>')

    return '<ul class="prcards">' + "".join(card(p) for p in prs) + "</ul>"


def runs_page(runs: list[dict], status: str, repo: str, page_no: int, has_more: bool, summary: dict | None = None) -> str:
    opts = "".join(f'<option value="{esc(v)}"{" selected" if v == status else ""}>{esc(v or "any status")}</option>'
                   for v in ("", "running", "passed", "pr", "stage", "failed", "rejected", "no-change", "rate-limited", "interrupted"))
    bar = chips((("", "All"), ("running", "Running"), ("passed", "Passed"), ("failed", "Failed")), status,
                lambda v: "/runs?" + urlencode({"status": v, "repo": repo}))
    form = (f'<form method="get" class="filters"><select name="status">{opts}</select>'
            f'<input name="repo" placeholder="owner/repo" value="{esc(repo)}"><button>Filter</button></form>')
    def link(label: str, p: int) -> str:
        return f'<a href="{esc("/runs?" + urlencode({"status": status, "repo": repo, "page": p}))}">{label}</a>'

    nav = '<p class="pager">' + (link("← newer", page_no - 1) if page_no > 0 else "") + " " + (link("older →", page_no + 1) if has_more else "") + "</p>"
    return runs_summary_line(summary) + bar + form + runs_list(runs) + nav


def run_images_html(rid: int, images) -> str:
    """Screenshots a run kept: what it built, and where that differs from the baselines. Names and kinds are checked first."""
    cells = ""
    for i in images or []:
        if i.get("kind") in ("built", "diff", "verify") and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,80}", str(i.get("name", ""))):
            src = "/runimg?" + urlencode({"run": int(rid), "kind": i["kind"], "name": i["name"]})
            cells += (f'<figure><a href="{esc(src)}" target="_blank"><img class="mockup" src="{esc(src)}" alt="{esc(i["kind"])} {esc(i["name"])}" loading="lazy"></a>'
                      f'<figcaption class="muted">{esc(i["kind"])}: {esc(i["name"])}</figcaption></figure>')
    return f'<h2>Screens</h2><div class="mockups">{cells}</div>' if cells else ""


def run_detail(r: dict, files=(), images=()) -> str:
    try:
        cls = json.dumps(json.loads(r["classification"]), indent=2) if r["classification"] else ""
    except ValueError:
        cls = r["classification"]
    meta = [("Status", badge(r["status"])), ("Kind", esc(r["kind"] + (" · " + r["stage"] if r["stage"] else ""))),
            ("Ticket", ticket_link(r["repo"], r["issue"]) + " — " + esc(r["title"])), ("Agent", esc(f'{r["harness"]} · {r["model"]} · effort {r["effort"]}')),
            ("Started", esc(ts(r["started"]))), ("Took", esc(dur(r["started"], r["finished"]))), ("Tokens", esc(tokens_in_out(r))), ("Detail", esc(r["detail"])),
            ("Pull requests", pr_links(r["pr_urls"]) or "—")]
    table = "".join(f"<tr><th>{k}</th><td>{v}</td></tr>" for k, v in meta)
    out = f'<table class="meta">{table}</table>'
    shown = [f for f in files or [] if designfiles.link_ok(f)]
    if shown:
        rows = "".join(f'<tr><td data-l="File (opens on GitHub)">{design_links([f])}</td><td data-l="Repo">{esc(f["repo"])}</td><td data-l="Draft PR">{pr_links(f.get("pr") or "") or "—"}</td></tr>' for f in shown)
        imgs = "".join(mockup_img(f) for f in shown)
        out += ("<h2>Design files</h2>" + (f'<div class="mockups">{imgs}</div>' if imgs else "")
                + "<table class=stack><thead><tr><th>File (opens on GitHub)</th><th>Repo</th><th>Draft PR</th></tr></thead>"
                f"<tbody>{rows}</tbody></table>")
    out += run_images_html(r.get("id", 0), images)
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
    h = TICKET_HEADS
    body = "".join(trow(h, [
        f'{ticket_link(t["repo"], t["issue"])}<br><span class="muted">{esc(t["title"])}</span>', badge(t["outcome"]), (esc(t["detail"]), ' class="wrap"'),
        f'{esc(t["runs"])}{" · " + badge(t["last_run"]) if t["last_run"] else ""}', (esc(ago(t["decided_at"])), f' title="{esc(ts(t["decided_at"]))}"'),
        steps_cell(t, counts), f'<a href="/labels/issue?repo={esc(t["repo"])}&amp;n={int(t["issue"])}">Edit labels</a>']) for t in rows)
    return ('<p class="muted">The latest decision for each ticket. <em>ignored</em> means the label was not applied by someone with write access.</p>'
            + cards_table(h, body))


def events_page(events: list[dict], kind: str, older: int | None) -> str:
    kind = kind or "important"
    bar = chips((("important", "Important"), ("decision", "Decisions"), ("alerts", "Alerts"), ("all", "Everything")), kind,
                lambda v: "/events?" + urlencode({"kind": v}))
    more = f'<p class="pager"><a href="{esc("/events?" + urlencode({"kind": kind, "before": int(older)}))}">older →</a></p>' if older else ""
    body = events_list(events) if kind != "all" else events_table(events)
    return bar + body + more


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


def ticket_images_page(repo: str, issue: int, files_by_run: dict, images_by_run: dict) -> str:
    """Every mockup preview and run screenshot kept for a ticket, grouped by run. Each image goes through the same checks as on the run page."""
    out = f'<p>{ticket_link(repo, issue)}</p>'
    for rid in sorted(set(files_by_run) | set(images_by_run)):
        files = [f for f in files_by_run.get(rid, []) if designfiles.link_ok(f)]
        mock = "".join(mockup_img(f) for f in files)
        shots = run_images_html(rid, images_by_run.get(rid, []))
        if mock or shots:
            out += f'<h2><a href="/runs/{int(rid)}">Run #{int(rid)}</a></h2>' + (f'<div class="mockups">{mock}</div>' if mock else "") + shots
    review = f'<p><a class="btn" href="/ticket/review?{esc(urlencode({"repo": repo, "n": int(issue)}))}">Review the screens</a></p>'
    return review + out + f'<p><a href="/ticket?repo={esc(repo)}&amp;n={int(issue)}">← back to the ticket</a></p>'


def steps_cell(t: dict, counts: dict) -> str:
    c = counts.get((t["repo"], t["issue"]))
    if not c or not c[1] or not REPO.match(str(t["repo"])):
        return "—"
    return f'<a class="step-count" href="/ticket?repo={esc(t["repo"])}&amp;n={int(t["issue"])}">{int(c[0])}/{int(c[1])}</a>'


def doc_page(repo: str, issue: int, stage: str, have: list[str], doc: dict | None, text: str, source: str, notice: str = "", back: str = "/tickets", previews=()) -> str:
    """A stage document, rendered from escaped Markdown. `text` is stored or GitHub-posted agent output and is never taken from the request.
    previews: the design run's recorded preview rows, shown as images above the document."""
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
    imgs = "".join(mockup_img(f) for f in previews or [])
    shots = (f'<div class="mockups">{imgs}</div><p><a class="btn" href="/ticket/review?{esc(urlencode({"repo": repo, "n": int(issue)}))}">'
             'Review the screens</a></p>') if imgs else ""
    toc = ("<details class=\"more\"><summary>Contents</summary><ul>" + "".join(f'<li><a href="#{i}">{esc(t)}</a></li>' for i, t in heads) + "</ul></details>") if heads else ""
    back_link = f'<p><a href="{esc(back)}">← Back to questions</a></p>'
    return (f'{head}<p class="muted">{meta}</p>{notice}{shots}{toc}<article class="docview">{body}</article>{back_link}')
