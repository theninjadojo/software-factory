"""Roadmap: a repository's open tickets in milestones, in the order the factory works through them. No dates: a ticket's step comes
from what it waits for (plan.steps), and within a step higher priority goes first. Selecting a ticket traces what it waits for
and what waits for it."""
import sqlite3
import time

from .. import plan
from ..tracker import display
from . import board, labels as L, views
from .board import PIN_ICON, TICKET_WORD, prio_chip
from .plancard import waits_for
from .views import csrf_field, esc

MAX_STEPS = 8
BAR = {"done": "done", "working": "run", "prs": "run", "needs": "wait", "failed": "fail", "new": "none"}
NO_MILESTONE = "No milestone"


def _bodies(db, repo: str) -> dict:
    try:
        return {n: b for n, b in db.execute("SELECT number, body FROM local_tickets WHERE repo=?", (repo,))}
    except sqlite3.OperationalError:
        return {}


def model(db, rows: list[dict], repo: str) -> dict:
    """What the page draws for one repository: tickets (open ones, and done ones that belong to a milestone), each ticket's step,
    the milestones in order and the queue. rows: board.ticket_rows."""
    names, assigned, bodies = plan.milestones(db, repo), plan.ticket_milestones(db, repo), _bodies(db, repo)
    tickets = []
    for r in rows:
        done = r["state"] == "done" or bool(r.get("closed"))
        if r["repo"] != repo or (done and r["issue"] not in assigned):
            continue
        tickets.append({"issue": r["issue"], "title": r["title"], "state": "done" if done else r["state"], "done": done,
                        "priority": r.get("priority", "normal"), "source": r.get("prio_src", ""),
                        "milestone": assigned.get(r["issue"], "") if assigned.get(r["issue"]) in names else "",
                        "waits": waits_for(db, repo, r["issue"], bodies.get(r["issue"], ""))})
    shown = {t["issue"] for t in tickets}
    for t in tickets:
        t["waits"] = [b for b in t["waits"] if b in shown]
    step = plan.steps({t["issue"]: t["waits"] for t in tickets})
    for t in tickets:
        t["step"] = step.get(t["issue"], 1)
    return {"tickets": tickets, "milestones": names, "queue": plan.queue(tickets, step),
            "steps": min(MAX_STEPS, max([t["step"] for t in tickets], default=1))}


def _bar(t: dict, repo: str, focus, lit: set, n_steps: int) -> str:
    col = min(t["step"], n_steps)
    after = ("after " + ", ".join(display(b) for b in t["waits"])) if t["waits"] else "can start now"
    on = focus == t["issue"]
    href = f'/roadmap?{board._qs(repo=repo, focus="" if on else t["issue"])}#chart'
    label = f'{display(t["issue"])}, step {t["step"]}, {TICKET_WORD[t["state"]].lower()}, {after}'
    return (f'<div class="rm-row rm-n{n_steps}{" dim" if focus and t["issue"] not in lit else ""}">'
            f'<div class="rm-name"><a class="mono muted" href="/ticket?{esc(board._qs(repo=repo, n=t["issue"]))}">{esc(display(t["issue"]))}</a>'
            f'<span class="rm-title">{esc(t["title"])}</span>{prio_chip(t["priority"], t["source"])}</div>'
            f'<a class="rm-bar {BAR[t["state"]]} rm-c{col}{" on" if on else ""}" href="{esc(href)}" aria-label="{esc(label)}"'
            f'{" aria-current=true" if on else ""}><b>{esc(display(t["issue"]))}</b><span>{esc(after)}</span></a></div>')


def chart(m: dict, repo: str, focus) -> str:
    n = m["steps"]
    waits = {t["issue"]: t["waits"] for t in m["tickets"]}
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
        mine.sort(key=lambda t: (t["step"], plan.RANK[t["priority"]], t["issue"]))
        last = min(n, max(t["step"] for t in mine))
        done = sum(t["done"] for t in mine)
        reach = (f'<div class="rm-reach rm-c{last}"><span>reached after step {last}</span><i class="rm-diamond{" done" if done == len(mine) else ""}" aria-hidden="true"></i></div>'
                 if name != NO_MILESTONE else "")
        body += (f'<div class="rm-row rm-ms rm-n{n}"><div class="rm-name"><strong>{esc(name)}</strong>'
                 f'<span class="mono muted">{done} of {len(mine)} done</span></div>{reach}</div>'
                 + "".join(_bar(t, repo, focus, lit, n) for t in mine))
    return f'<div class="rm-scroll" id="chart"><div class="rm-chart">{head}{body}</div></div>'


def _focus_line(m: dict, repo: str, focus) -> str:
    by = {t["issue"]: t for t in m["tickets"]}
    if focus not in by:
        return '<p class="muted rm-focus">Select a ticket to trace what it waits for and what waits for it.</p>'
    t = by[focus]
    held = [x["issue"] for x in m["tickets"] if focus in x["waits"]]
    return (f'<p class="rm-focus" role="status">Tracing <a href="/ticket?{esc(board._qs(repo=repo, n=focus))}">{esc(display(focus))} {esc(t["title"])}</a>: '
            f'{"waits for " + esc(", ".join(display(b) for b in t["waits"])) if t["waits"] else "waits for nothing"}, '
            f'and holds up {esc(", ".join(display(b) for b in held)) if held else "nothing"}. '
            f'<a class="btn secondary" href="/roadmap?{esc(board._qs(repo=repo))}#chart">Show all</a></p>')


def _queue(m: dict, repo: str) -> str:
    if not m["queue"]:
        return '<p class="muted">Nothing is waiting to be worked on.</p>'
    items = "".join(f'<li><a href="/ticket?{esc(board._qs(repo=repo, n=t["issue"]))}"><span class="rm-pos mono">{i:02d}</span>'
                    f'<span class="mono muted">{esc(display(t["issue"]))}</span> <span>{esc(t["title"])}</span></a></li>'
                    for i, t in enumerate(m["queue"][:8], 1))
    return f'<ol class="rm-queue">{items}</ol>'


def _milestones(m: dict, repo: str, csrf: str) -> str:
    hidden = f'{csrf_field(csrf)}<input type="hidden" name="repo" value="{esc(repo)}">'
    rows = ""
    for i, name in enumerate(m["milestones"]):
        btn = lambda op, word, off=False: (f'<button class="secondary" name="op" value="{op}"{" disabled" if off else ""}>{word}</button>')
        rows += (f'<li><form method="post" action="/roadmap/milestones" class="rm-msrow">{hidden}<input type="hidden" name="name" value="{esc(name)}">'
                 f'<span class="rm-msname">{i + 1}. {esc(name)}</span>{btn("up", "Move up", i == 0)}{btn("down", "Move down", i == len(m["milestones"]) - 1)}'
                 f'{btn("delete", "Delete")}</form></li>')
    return (f'<section class="sd-card" id="milestones" aria-labelledby="ms-h"><div class="sd-cardhead"><h3 id="ms-h">Milestones</h3></div>'
            '<p class="muted">Milestones run top to bottom. Put a ticket in one from the Priority card on its page. Deleting a milestone '
            'keeps its tickets; they move to No milestone.</p>'
            + (f'<ol class="rm-mslist">{rows}</ol>' if rows else "")
            + f'<form method="post" action="/roadmap/milestones" class="rm-msadd">{hidden}<input type="hidden" name="op" value="add">'
            f'<label>New milestone <input name="name" required maxlength="{plan.MAX_NAME}"></label><button>Add milestone</button></form></section>')


def page(cfg, m: dict, repo: str, focus, csrf: str) -> str:
    pick = ""
    if len(cfg.repos) > 1:
        opts = "".join(f'<option value="{esc(r)}"{" selected" if r == repo else ""}>{esc(r)}</option>' for r in cfg.repos)
        pick = (f'<form method="get" action="/roadmap" class="sd-form" data-autosubmit><label class="sd-lab">Repository '
                f'<select name="repo">{opts}</select></label><button class="secondary sd-apply">Show</button></form>')
    off = ("" if cfg.pm.enabled else '<p class="muted sd-scope" role="status">The project manager is off: the order comes from the '
           '<code>Blocked by</code> lines in descriptions and the priorities you pin, and the factory does not hold a build back for '
           'its blockers. <a href="/settings">Turn it on in Settings</a></p>')
    legend = ('<p class="rm-legend"><span><i class="rm-key done"></i>Done</span><span><i class="rm-key run"></i>Working</span>'
              '<span><i class="rm-key wait"></i>Needs you</span><span><i class="rm-key fail"></i>Failed</span>'
              f'<span><i class="rm-key none"></i>Not started</span><span>{PIN_ICON}Priority pinned by you</span>'
              '<span><i class="rm-diamond" aria-hidden="true"></i>Milestone reached</span></p>')
    chart_html = chart(m, repo, focus) if m["tickets"] else '<div class="sd-empty sd-emptybox"><p><strong>No open tickets.</strong></p></div>'
    return (f'<div class="rm-page"><div class="sd-h1row"><h1>Roadmap</h1>{pick}</div>'
            f'<p class="muted sd-lede">The order the factory works through {esc(repo)}. A ticket\'s step comes from what it waits for; '
            'within a step, higher priority goes first. No dates, only what comes before what.</p>' + off
            + f'<section aria-labelledby="q-h" class="rm-next"><h3 id="q-h">Up next in the queue</h3>{_queue(m, repo)}</section>'
            + legend + chart_html + _focus_line(m, repo, focus) + _milestones(m, repo, csrf) + "</div>")


# ---------------------------------------------------------------- routes
def _repo(cfg, q: dict) -> str:
    return q.get("repo") if q.get("repo") in cfg.repos else (cfg.repos[0] if cfg.repos else "")


def get(h, q: dict, csrf: str) -> None:
    cfg = h.app.cfg()
    repo = _repo(cfg, q)
    db = h.app.ro_db()
    if db is None or not repo:
        body = '<p class="muted">The orchestrator has not created its database yet.</p>' if repo else '<p class="muted">No repository is configured.</p>'
        return h._send(200, views.page("Roadmap", body, "/roadmap", csrf))
    try:
        rows = board.ticket_rows(db, L.needs_cached(), L.titles_cached(), time.time(), cfg.github_issues_enabled)
        m = model(db, rows, repo)
    finally:
        db.close()
    focus = int(q["focus"]) if (q.get("focus") or "").isdigit() and len(q["focus"]) < 10 else None
    shown = L.flash_pop(csrf)
    h._send(200, views.page("Roadmap", page(cfg, m, repo, focus, csrf), "/roadmap", csrf, wide=True, bare=True,
                            flash=shown[0] if shown else None, flash_kind=shown[1] if shown else "ok"))


def milestones_post(h, form, csrf: str) -> None:
    cfg = h.app.cfg()
    repo, op, name = form.get("repo", ""), form.get("op", ""), plan.clean_name(form.get("name", ""))
    back = f"/roadmap?{board._qs(repo=repo)}#milestones"
    if repo not in cfg.repos or op not in ("add", "up", "down", "delete"):
        L.flash_set(csrf, "That is not a valid change.", "bad")
        return h._redirect(back)
    db = sqlite3.connect(cfg.db_path, timeout=10)
    try:
        plan.ensure_tables(db)
        if op == "add":
            ok = plan.add_milestone(db, repo, name)
            msg = f"Added {name}." if ok else "Enter a new name (at most 30 milestones)."
        elif op == "delete":
            ok = plan.delete_milestone(db, repo, name)
            msg = f"Deleted {name}. Its tickets have no milestone now." if ok else "That milestone no longer exists."
        else:
            ok = plan.move_milestone(db, repo, name, -1 if op == "up" else 1)
            msg = f"Moved {name} {op}." if ok else "Nothing moved."
    finally:
        db.close()
    L.flash_set(csrf, msg, "ok" if ok else "bad")
    h._redirect(back)
