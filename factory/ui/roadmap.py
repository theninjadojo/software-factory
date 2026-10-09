"""Roadmap: a project's tickets as features in milestones, for a business reader. The overview lists the milestones top to bottom
(each one line saying what users can do once it is done) with their progress; a milestone opens to its features by category, and
the Categories view groups the same features by the part of the business they touch. Every feature shows its short name (the
project manager's, or a person's, or else the ticket's title) and opens to the full title, its state and its links.

The step chart (?view=chart) keeps the build order: a ticket's step comes from what it waits for (plan.steps), within a step
higher priority goes first, and selecting a ticket traces what it waits for and what waits for it. No dates anywhere."""
import sqlite3
import time

from .. import plan
from ..tracker import display
from . import board, labels as L, views
from .board import PIN_ICON, TICKET_WORD, prio_chip
from .plancard import _hidden, waits_for
from .views import csrf_field, esc

MAX_STEPS = 8
BAR = {"done": "done", "working": "run", "prs": "run", "needs": "wait", "failed": "fail", "new": "none"}
NO_MILESTONE = "No milestone"
OTHER = "Other"
KIND = {"done": "done", "working": "built", "prs": "built", "needs": "needs", "failed": "needs", "new": "new"}
WORD = {"done": "Done", "built": "Being built", "needs": "Needs you", "new": "Not started"}
CHEVRON = ('<svg class="rp-chev" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" '
           'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M9 6l6 6-6 6"></path></svg>')


def _bodies(db, repo: str) -> dict:
    try:
        return {n: b for n, b in db.execute("SELECT number, body FROM local_tickets WHERE repo=?", (repo,))}
    except sqlite3.OperationalError:
        return {}


def model(db, rows: list[dict], scope: str, repos: list[str]) -> dict:
    """What the page draws for one scope (a project and its repositories, or a repository in none): tickets (open ones, and done
    ones that belong to a milestone), each ticket's step, the milestones in order and the queue. rows: board.ticket_rows. A
    ticket's key is (repo, issue); what it waits for is in its own repository."""
    names, assigned = plan.milestones(db, scope), plan.ticket_milestones(db, scope)
    owner, towner = plan.milestone_owners(db, scope), plan.ticket_owners(db, repos)
    bodies, named = {r: _bodies(db, r) for r in repos}, plan.features(db, repos)
    tickets = []
    for r in rows:
        k = (r["repo"], r["issue"])
        done = r["state"] == "done" or bool(r.get("closed"))
        if r["repo"] not in repos or (done and k not in assigned):
            continue
        tickets.append({"key": k, "repo": r["repo"], "issue": r["issue"], "title": r["title"], "state": "done" if done else r["state"],
                        "done": done, "priority": r.get("priority", "normal"), "source": r.get("prio_src", ""),
                        "milestone": assigned.get(k, "") if assigned.get(k) in names else "",
                        "placed_by_pm": towner.get(k) == "pm", "kind": KIND["done" if done else r["state"]],
                        "short": (named.get(k) or {}).get("short") or "", "category": (named.get(k) or {}).get("category") or OTHER,
                        "waits": [(r["repo"], b) for b in waits_for(db, r["repo"], r["issue"], bodies[r["repo"]].get(r["issue"], ""))]})
    shown = {t["key"] for t in tickets}
    for t in tickets:
        t["waits"] = [b for b in t["waits"] if b in shown]
    step = plan.steps({t["key"]: t["waits"] for t in tickets})
    for t in tickets:
        t["step"] = step.get(t["key"], 1)
    return {"scope": scope, "repos": repos, "multi": len(repos) > 1, "tickets": tickets, "milestones": names,
            "pm_made": {n for n in names if owner.get(n) == "pm"}, "queue": plan.queue(tickets, step),
            "steps": min(MAX_STEPS, max([t["step"] for t in tickets], default=1))}


def _name(m: dict, k) -> str:
    """A ticket as shown: L-3 or #12, with its repository's short name first when the project has several."""
    return views.ref(k[0], k[1], short=True) if m["multi"] else display(k[1])


def _fkey(k) -> str:
    return f"{k[0]}#{k[1]}"


def _bar(m: dict, t: dict, focus, lit: set, n_steps: int) -> str:
    col = min(t["step"], n_steps)
    after = ("after " + ", ".join(_name(m, b) for b in t["waits"])) if t["waits"] else "can start now"
    on = focus == t["key"]
    href = f'/roadmap?{board._qs(project=m["scope"], focus="" if on else _fkey(t["key"]))}#chart'
    label = f'{_name(m, t["key"])}, step {t["step"]}, {TICKET_WORD[t["state"]].lower()}, {after}'
    return (f'<div class="rm-row rm-n{n_steps}{" dim" if focus and t["key"] not in lit else ""}">'
            f'<div class="rm-name"><a class="mono muted" href="/ticket?{esc(board._qs(repo=t["repo"], n=t["issue"]))}">{esc(_name(m, t["key"]))}</a>'
            f'<span class="rm-title">{esc(t["title"])}</span>{prio_chip(t["priority"], t["source"])}</div>'
            f'<a class="rm-bar {BAR[t["state"]]} rm-c{col}{" on" if on else ""}" href="{esc(href)}" aria-label="{esc(label)}"'
            f'{" aria-current=true" if on else ""}><b>{esc(_name(m, t["key"]))}</b><span>{esc(after)}</span></a></div>')


def chart(m: dict, focus) -> str:
    n = m["steps"]
    waits = {t["key"]: t["waits"] for t in m["tickets"]}
    lit = plan.chain(waits, focus) if focus in waits else set()
    focus = focus if focus in waits else None
    head = (f'<div class="rm-row rm-head rm-n{n}"><div class="rm-name">Ticket</div>'
            + "".join(f'<div class="rm-step">Step {i}{"+" if i == MAX_STEPS and any(t["step"] > MAX_STEPS for t in m["tickets"]) else ""}</div>'
                      for i in range(1, n + 1)) + "</div>")
    body = ""
    for name in m["milestones"] + [NO_MILESTONE]:
        mine = [t for t in m["tickets"] if (t["milestone"] or NO_MILESTONE) == name]
        if not mine:
            continue
        mine.sort(key=lambda t: (t["step"], plan.RANK[t["priority"]], t["key"]))
        last = min(n, max(t["step"] for t in mine))
        done = sum(t["done"] for t in mine)
        reach = (f'<div class="rm-reach rm-c{last}"><span>reached after step {last}</span><i class="rm-diamond{" done" if done == len(mine) else ""}" aria-hidden="true"></i></div>'
                 if name != NO_MILESTONE else "")
        by = ' <span class="rm-by">by the project manager</span>' if name in m["pm_made"] else ""
        body += (f'<div class="rm-row rm-ms rm-n{n}"><div class="rm-name"><strong>{esc(name)}</strong>{by}'
                 f'<span class="mono muted">{done} of {len(mine)} done</span></div>{reach}</div>'
                 + "".join(_bar(m, t, focus, lit, n) for t in mine))
    return f'<div class="rm-scroll" id="chart"><div class="rm-chart">{head}{body}</div></div>'


def _focus_line(m: dict, focus) -> str:
    by = {t["key"]: t for t in m["tickets"]}
    if focus not in by:
        return '<p class="muted rm-focus">Select a ticket to trace what it waits for and what waits for it.</p>'
    t = by[focus]
    held = [x["key"] for x in m["tickets"] if focus in x["waits"]]
    return (f'<p class="rm-focus" role="status">Tracing <a href="/ticket?{esc(board._qs(repo=t["repo"], n=t["issue"]))}">{esc(_name(m, focus))} {esc(t["title"])}</a>: '
            f'{"waits for " + esc(", ".join(_name(m, b) for b in t["waits"])) if t["waits"] else "waits for nothing"}, '
            f'and holds up {esc(", ".join(_name(m, b) for b in held)) if held else "nothing"}. '
            f'<a class="btn secondary" href="/roadmap?{esc(board._qs(project=m["scope"]))}#chart">Show all</a></p>')


def _queue(m: dict) -> str:
    if not m["queue"]:
        return '<p class="muted">Nothing is waiting to be worked on.</p>'
    items = "".join(f'<li><a href="/ticket?{esc(board._qs(repo=t["repo"], n=t["issue"]))}"><span class="rm-pos mono">{i:02d}</span>'
                    f'<span class="mono muted">{esc(_name(m, t["key"]))}</span> <span>{esc(t["title"])}</span></a></li>'
                    for i, t in enumerate(m["queue"][:8], 1))
    return f'<ol class="rm-queue">{items}</ol>'


def _milestones(cfg, m: dict, csrf: str) -> str:
    hidden = f'{csrf_field(csrf)}<input type="hidden" name="project" value="{esc(m["scope"])}">'
    rows = ""
    for i, name in enumerate(m["milestones"]):
        btn = lambda op, word, off=False: (f'<button class="secondary" name="op" value="{op}"{" disabled" if off else ""}>{word}</button>')
        by = '<span class="rm-by">by the project manager</span>' if name in m["pm_made"] else ""
        rows += (f'<li><form method="post" action="/roadmap/milestones" class="rm-msrow">{hidden}<input type="hidden" name="name" value="{esc(name)}">'
                 f'<span class="rm-msname">{i + 1}. {esc(name)} {by}</span>{btn("up", "Move up", i == 0)}{btn("down", "Move down", i == len(m["milestones"]) - 1)}'
                 f'{btn("delete", "Delete")}</form>'
                 f'<details class="disclose rm-msedit"><summary>Reword</summary><form method="post" action="/roadmap/milestones" class="rm-msadd">'
                 f'{hidden}<input type="hidden" name="op" value="rename"><input type="hidden" name="name" value="{esc(name)}">'
                 f'<label>Milestone <input name="new" required maxlength="{plan.MAX_NAME}" value="{esc(name)}"></label>'
                 '<button>Save</button></form></details></li>')
    pm_note = (' The project manager adds milestones and puts tickets in them on its sweeps. Anything you add, reword, move or '
               'delete here, and any ticket you place yourself, is yours: it never changes it again.') if cfg.pm.enabled else ""
    return (f'<section class="sd-card" id="milestones" aria-labelledby="ms-h"><div class="sd-cardhead"><h3 id="ms-h">Milestones</h3></div>'
            '<p class="muted">Milestones run top to bottom. Write each as what the product\'s users can do once it is done, for example '
            '“Parents can pay all their invoices in one checkout”. Put a ticket in one from the Priority card on its page. Deleting a '
            f'milestone keeps its tickets; they move to No milestone.{pm_note}</p>'
            + (f'<ol class="rm-mslist">{rows}</ol>' if rows else "")
            + f'<form method="post" action="/roadmap/milestones" class="rm-msadd">{hidden}<input type="hidden" name="op" value="add">'
            f'<label>New milestone <input name="name" required maxlength="{plan.MAX_NAME}" placeholder="Who can do what, in plain words"></label>'
            '<button>Add milestone</button></form></section>')


def _url(m: dict, **kw) -> str:
    return "/roadmap?" + board._qs(project=m["scope"], **kw)


def _w(part: int, whole: int) -> str:
    """A width class (rm-w0 .. rm-w100, in steps of 5): the page's CSP allows no style attributes."""
    return f"rm-w{int(round(100 * part / whole / 5)) * 5 if whole else 0}"


def _counts(ts: list[dict]) -> dict:
    c = dict.fromkeys(WORD, 0)
    for t in ts:
        c[t["kind"]] += 1
    return c


def _flags(c: dict) -> str:
    return ((f'<span class="rp-flag needs">● {c["needs"]} need{"s" if c["needs"] == 1 else ""} you</span>' if c["needs"] else "")
            + (f'<span class="rp-flag built">● {c["built"]} being built</span>' if c["built"] else ""))


def _progress(ts: list[dict]) -> str:
    if not ts:                              # an empty bar would read as 0% done
        return '<span class="rp-prog"><span class="rp-count rp-empty">No tickets yet</span></span><span class="rp-flags"></span>'
    c = _counts(ts)
    return (f'<span class="rp-prog"><span class="rp-track"><span class="rp-fill {_w(c["done"], len(ts))}"></span></span>'
            f'<span class="rp-count mono">{c["done"]} of {len(ts)} done</span></span><span class="rp-flags">{_flags(c)}</span>')


def _short(t: dict) -> str:
    return t["short"] or t["title"]


def _head(cfg, m: dict, view: str, line: str) -> str:
    choices = plan.scopes(cfg)
    pick = ""
    if len(choices) > 1:
        opts = "".join(f'<option value="{esc(v)}"{" selected" if v == m["scope"] else ""}>{esc(w)}</option>' for v, w in choices)
        keep = f'<input type="hidden" name="view" value="{esc(view)}">' if view else ""
        pick = (f'<form method="get" action="/roadmap" class="sd-form" data-autosubmit>{keep}<label class="sd-lab">Project '
                f'<select name="project">{opts}</select></label><button class="secondary sd-apply">Show</button></form>')
    tab = lambda v, word: (f'<a href="{esc(_url(m, view=v))}"{" aria-current=page" if v == view else ""}>{word}</a>')
    switch = f'<nav class="rp-switch" aria-label="View by">{tab("", "Milestones")}{tab("categories", "Categories")}</nav>'
    return (f'<div class="rp-head"><div><h1>Roadmap</h1><p class="muted rp-line">{line}</p></div>'
            f'<div class="rp-tools">{pick}{switch}</div></div>')


def _summary(m: dict, *parts: str) -> str:
    open_ = [t for t in m["tickets"] if not t["done"]]
    needs = sum(t["kind"] == "needs" for t in open_)
    need = f'<a href="/tickets?stage=needs">{needs} need{"s" if needs == 1 else ""} you</a>' if needs else "nothing needs you"
    return " · ".join([esc(m["scope"]), *parts, f"{len(open_)} open feature{'' if len(open_) == 1 else 's'}", need])


def overview(cfg, m: dict, csrf: str) -> str:
    n = len(m["milestones"])
    rows = ""
    for i, name in enumerate(m["milestones"], 1):
        ts = [t for t in m["tickets"] if t["milestone"] == name]
        rows += (f'<li><a class="rp-row" href="{esc(_url(m, milestone=name))}"><span class="rp-pos mono">{i}</span>'
                 f'<span class="rp-name">{esc(name)}</span>{_progress(ts)}</a></li>')
    rows = (f'<ol class="rp-list" aria-label="Milestones in order">{rows}</ol>' if rows else
            '<div class="sd-empty sd-emptybox"><p><strong>No milestones yet.</strong> '
            + ('The project manager makes them on its next sweep, or ' if cfg.pm.enabled else "") + 'add one below.</p></div>')
    loose = [t for t in m["tickets"] if not t["milestone"] and not t["done"]]
    unplanned = ""
    if loose:
        names = " · ".join(esc(_short(t)) for t in loose[:2]) + (f" · {len(loose) - 2} more" if len(loose) > 2 else "")
        unplanned = (f'<section class="rp-loose"><b>Not planned yet</b><span class="muted">{names}</span>'
                     f'<a href="{esc(_url(m, unplanned=1))}">See them</a></section>')
    line = _summary(m, f"{n} milestone{'' if n == 1 else 's'}, worked on top to bottom" if n else "no milestones yet")
    return (f'<div class="rp-page">{_head(cfg, m, "", line)}{rows}{unplanned}'
            f'<p class="muted rp-more">Need the build order? <a href="{esc(_url(m, view="chart"))}">Show the step chart</a></p>'
            + _milestones(cfg, m, csrf) + "</div>")


def categories(cfg, m: dict) -> str:
    by: dict[str, list] = {}
    for t in m["tickets"]:
        by.setdefault(t["category"], []).append(t)
    order = sorted(by, key=lambda c: (c == OTHER, -len(by[c]), c.lower()))
    tiles = ""
    for c in order:
        ts, k = by[c], _counts(by[c])
        part = [n for n in m["milestones"] if any(t["milestone"] == n for t in ts)] + ([] if all(t["milestone"] for t in ts) else ["not planned yet"])
        tiles += (f'<a class="rp-tile" href="{esc(_url(m, category=c))}"><span class="rp-tilehead"><span class="rp-cat">{esc(c)}</span>'
                  f'<span class="mono muted">{len(ts)}</span></span>'
                  f'<span class="rp-stack" aria-label="{k["done"]} done, {k["built"]} being built, {k["needs"]} need you, {k["new"]} not started">'
                  + "".join(f'<span class="rp-seg {kind} {_w(k[kind], len(ts))}"></span>' for kind in ("done", "built", "needs"))
                  + f'</span><span class="rp-tops">{esc(" · ".join(_short(t) for t in ts[:3]))}</span>'
                  f'<span class="rp-part">Part of: {esc(", ".join(part))}</span></a>')
    legend = ('<p class="rp-legend">' + "".join(f'<span><i class="rp-key {k}"></i>{w}</span>' for k, w in WORD.items()) + "</p>")
    line = _summary(m, f"{len(order)} categor{'y' if len(order) == 1 else 'ies'}")
    body = (f'<div class="rp-tiles">{tiles}</div>{legend}' if tiles else
            '<div class="sd-empty sd-emptybox"><p><strong>No open tickets.</strong></p></div>')
    return (f'<div class="rp-page">{_head(cfg, m, "categories", line)}'
            '<p class="muted">The same features, grouped by the part of the business they touch. Each bar shows done, being built and '
            f'waiting for you; the rest is not started.</p>{body}</div>')


def _feature(m: dict, t: dict, csrf: str, back: str) -> str:
    by = {x["key"]: x for x in m["tickets"]}
    waits = [] if t["done"] else [by[b] for b in t["waits"] if b in by and not by[b]["done"]]
    after = (f'<span class="rp-after">after {esc(_short(waits[0]))}{f" +{len(waits) - 1}" if len(waits) > 1 else ""}</span>' if waits else "")
    status = WORD[t["kind"]] + (f' · waits for {", ".join(_short(w) for w in waits)}' if waits else
                                "" if t["done"] else " · can start now")
    hidden = _hidden(t["repo"], t["issue"], csrf, back)
    opts = (f'<option value="">{esc(NO_MILESTONE)}</option>'
            + "".join(f'<option value="{esc(n)}"{" selected" if n == t["milestone"] else ""}>{esc(n)}</option>' for n in m["milestones"]))
    move = (f'<form method="post" action="/tickets/milestone" class="rp-move">{hidden}<label>Milestone '
            f'<select name="milestone">{opts}</select></label><button class="secondary">Move</button></form>') if m["milestones"] else ""
    rename = (f'<details class="disclose rp-rename"><summary>Rename</summary><form method="post" action="/roadmap/feature" class="rp-renform">'
              f'{csrf_field(csrf)}<input type="hidden" name="project" value="{esc(m["scope"])}"><input type="hidden" name="repo" value="{esc(t["repo"])}">'
              f'<input type="hidden" name="n" value="{int(t["issue"])}"><input type="hidden" name="back" value="{esc(back)}">'
              f'<label>Short name <input name="short" maxlength="{plan.MAX_SHORT}" value="{esc(t["short"])}" placeholder="{esc(t["title"][:plan.MAX_SHORT])}"></label>'
              f'<label>Category <input name="category" maxlength="{plan.MAX_CATEGORY}" value="{esc(t["category"] if t["category"] != OTHER else "")}" placeholder="{OTHER}"></label>'
              '<button>Save</button></form></details>')
    return (f'<details class="rp-feat"><summary>{CHEVRON}<span class="rp-short">{esc(_short(t))}</span>{after}'
            f'<span class="rp-chip {t["kind"]}">{WORD[t["kind"]]}</span></summary><div class="rp-body">'
            f'<p class="rp-full">{esc(t["title"])}</p><p class="muted">{esc(status)}</p>'
            f'<p class="rp-links"><a href="/ticket?{esc(board._qs(repo=t["repo"], n=t["issue"]))}">Open ticket '
            f'<span class="mono">{esc(_name(m, t["key"]))}</span></a></p>{move}{rename}</div></details>')


def drill(cfg, m: dict, csrf: str, title: str, crumb: str, ts: list[dict], group: str, back: str) -> str:
    """One milestone (group by category), one category (group by milestone) or the unplanned features."""
    groups: dict[str, list] = {}
    for t in sorted(ts, key=lambda t: (t["done"], t["step"], plan.RANK[t["priority"]], t["key"])):
        groups.setdefault(t[group] or NO_MILESTONE if group == "milestone" else t[group], []).append(t)
    if group == "milestone":
        order = [n for n in m["milestones"] if n in groups] + ([NO_MILESTONE] if NO_MILESTONE in groups else [])
    else:
        order = sorted(groups, key=lambda c: (c == OTHER, -len(groups[c]), c.lower()))
    body = ""
    for g in order:
        body += (f'<section class="rp-group"><h2>{esc(g)} <span class="mono muted">· {len(groups[g])}</span></h2>'
                 + "".join(_feature(m, t, csrf, back) for t in groups[g]) + "</section>")
    body = body or '<div class="sd-empty sd-emptybox"><p><strong>Nothing here yet.</strong></p></div>'
    return (f'<div class="rp-page"><p class="rp-crumb"><a href="{esc(_url(m))}">← Roadmap</a> <span class="muted">/ {esc(crumb)}</span></p>'
            f'<h1 class="rp-title">{esc(title)}</h1><div class="rp-progline">{_progress(ts)}</div>{body}'
            '<p class="muted">Short names and categories are written by the project manager. Rename any of them and it keeps yours.</p></div>')


def chart_page(cfg, m: dict, focus) -> str:
    off = ("" if cfg.pm.enabled else '<p class="muted sd-scope" role="status">The project manager is off: the order comes from the '
           '<code>Blocked by</code> lines in descriptions and the priorities you pin, and the factory does not hold a build back for '
           'its blockers. <a href="/settings">Turn it on in Settings</a></p>')
    legend = ('<p class="rm-legend"><span><i class="rm-key done"></i>Done</span><span><i class="rm-key run"></i>Working</span>'
              '<span><i class="rm-key wait"></i>Needs you</span><span><i class="rm-key fail"></i>Failed</span>'
              f'<span><i class="rm-key none"></i>Not started</span><span>{PIN_ICON}Priority pinned by you</span>'
              '<span><i class="rm-diamond" aria-hidden="true"></i>Milestone reached</span></p>')
    chart_html = chart(m, focus) if m["tickets"] else '<div class="sd-empty sd-emptybox"><p><strong>No open tickets.</strong></p></div>'
    what = esc(m["scope"]) + (f' ({esc(", ".join(r.split("/")[1] for r in m["repos"]))})' if m["multi"] else "")
    return (f'<div class="rm-page"><p class="rp-crumb"><a href="{esc(_url(m))}">← Roadmap</a> <span class="muted">/ Step chart</span></p>'
            f'<div class="sd-h1row"><h1>Build order</h1></div>'
            f'<p class="muted sd-lede">The order the factory works through {what}. A ticket\'s step comes from what it waits for; '
            'within a step, higher priority goes first. No dates, only what comes before what.</p>' + off
            + f'<section aria-labelledby="q-h" class="rm-next"><h3 id="q-h">Up next in the queue</h3>{_queue(m)}</section>'
            + legend + chart_html + _focus_line(m, focus) + "</div>")


def page(cfg, m: dict, focus, csrf: str, q: dict | None = None) -> str:
    """The view q asks for: the overview, ?view=chart|categories, ?milestone=, ?category= or ?unplanned=1."""
    q = q or {}
    back = _url(m, **{k: q[k] for k in ("milestone", "category", "unplanned", "view") if q.get(k)})
    if q.get("view") == "chart" or focus is not None:
        return chart_page(cfg, m, focus)
    if q.get("milestone") in m["milestones"]:
        i = m["milestones"].index(q["milestone"])
        ts = [t for t in m["tickets"] if t["milestone"] == q["milestone"]]
        return drill(cfg, m, csrf, q["milestone"], f"Milestone {i + 1} of {len(m['milestones'])}", ts, "category", back)
    if q.get("category") and any(t["category"] == q["category"] for t in m["tickets"]):
        ts = [t for t in m["tickets"] if t["category"] == q["category"]]
        return drill(cfg, m, csrf, q["category"], "Category", ts, "milestone", back)
    if q.get("unplanned"):
        ts = [t for t in m["tickets"] if not t["milestone"] and not t["done"]]
        return drill(cfg, m, csrf, "Not planned yet", "Features in no milestone", ts, "category", back)
    if q.get("view") == "categories":
        return categories(cfg, m)
    return overview(cfg, m, csrf)


# ---------------------------------------------------------------- routes
def _scope(cfg, q: dict) -> str:
    """?project= a project or a repository in none; ?repo= (older links) means that repository's project."""
    known = {v for v, _ in plan.scopes(cfg)}
    if q.get("project") in known:
        return q["project"]
    if q.get("repo") in cfg.repos:
        return plan.scope_of(cfg, q["repo"])
    return next(iter(plan.scopes(cfg)), ("", ""))[0]


def _focus(q: dict, repos: list[str]):
    """focus=owner/name#12 (or a bare number: the first repository's) -> (repo, issue), or None."""
    raw = q.get("focus") or ""
    repo, _, n = raw.rpartition("#")
    repo = repo or (q.get("repo") if q.get("repo") in repos else repos[0] if repos else "")
    return (repo, int(n)) if repo in repos and n.isdigit() and len(n) < 10 else None


def get(h, q: dict, csrf: str) -> None:
    cfg = h.app.cfg()
    scope = _scope(cfg, q)
    repos = [r for r in plan.scope_repos(cfg, scope) if r in cfg.repos]
    db = h.app.ro_db()
    if db is None or not repos:
        body = '<p class="muted">The orchestrator has not created its database yet.</p>' if repos else '<p class="muted">No repository is configured.</p>'
        return h._send(200, views.page("Roadmap", body, "/roadmap", csrf))
    try:
        rows = board.ticket_rows(db, L.needs_cached(), L.titles_cached(), time.time(), cfg.github_issues_enabled)
        m = model(db, rows, scope, repos)
    finally:
        db.close()
    shown = L.flash_pop(csrf)
    h._send(200, views.page("Roadmap", page(cfg, m, _focus(q, repos), csrf, q), "/roadmap", csrf, wide=True, bare=True,
                            flash=shown[0] if shown else None, flash_kind=shown[1] if shown else "ok"))


def feature_post(h, form, csrf: str) -> None:
    """A person renames a feature or changes its category: from then on it is theirs and the project manager leaves it alone."""
    cfg = h.app.cfg()
    scope, repo, n = form.get("project", ""), form.get("repo", ""), form.get("n", "")
    if scope not in {v for v, _ in plan.scopes(cfg)} or repo not in plan.scope_repos(cfg, scope) or not n.isdigit() or len(n) > 9:
        L.flash_set(csrf, "That is not a valid change.", "bad")
        return h._redirect(L._back(form))
    db = sqlite3.connect(cfg.db_path, timeout=10)
    try:
        plan.ensure_tables(db)
        cur = plan.features(db, [repo]).get((repo, int(n)), {})
        changed = []
        for f in ("short", "category"):
            v = " ".join((form.get(f) or "").split())
            if v != (cur.get(f) or ""):
                plan.set_feature(db, repo, int(n), f, v, "person")
                changed.append(f)
    finally:
        db.close()
    L.flash_set(csrf, "Saved. The project manager keeps your wording." if changed else "Nothing changed.", "ok")
    h._redirect(L._back(form))


def milestones_post(h, form, csrf: str) -> None:
    cfg = h.app.cfg()
    scope = form.get("project", "") or (plan.scope_of(cfg, form["repo"]) if form.get("repo") in cfg.repos else "")   # repo: older forms
    op, name = form.get("op", ""), plan.clean_name(form.get("name", ""))
    back = f"/roadmap?{board._qs(project=scope)}#milestones"
    if scope not in {v for v, _ in plan.scopes(cfg)} or op not in ("add", "up", "down", "delete", "rename"):
        L.flash_set(csrf, "That is not a valid change.", "bad")
        return h._redirect(back)
    db = sqlite3.connect(cfg.db_path, timeout=10)
    try:
        plan.ensure_tables(db)
        if op == "add":
            ok = plan.add_milestone(db, scope, name)
            msg = f"Added “{name}”." if ok else "Enter a new milestone (at most 30)."
        elif op == "delete":
            ok = plan.delete_milestone(db, scope, name)
            msg = f"Deleted “{name}”. Its tickets have no milestone now." if ok else "That milestone no longer exists."
        elif op == "rename":
            new = plan.clean_name(form.get("new", ""))
            ok = plan.rename_milestone(db, scope, name, new)
            msg, name = (f"Reworded to “{new}”." if ok else "Enter wording no other milestone uses."), new if ok else name
        else:
            ok = plan.move_milestone(db, scope, name, -1 if op == "up" else 1)
            msg = f"Moved “{name}” {op}." if ok else "Nothing moved."
        if ok:                                  # made, reworded, moved or deleted by a person: the project manager leaves it alone
            plan.mark_milestone(db, scope, name, "person")
    finally:
        db.close()
    L.flash_set(csrf, msg, "ok" if ok else "bad")
    h._redirect(back)
