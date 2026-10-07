"""Start a new project: the interview with an agent, its recommended pattern, and accepting it.

The UI only writes: a message becomes a pending turn that the orchestrator's interview lane answers (factory.newproject). The plan a
person accepts is re-validated against the catalogue from the form's choices, never taken from the page. Every handler runs behind the
session, Host and CSRF checks of the server."""
import logging
import sqlite3

from .. import newproject as N
from .. import pause
from ..sanitize import md_render
from . import forms
from . import labels as L
from . import views
from .views import ago, badge, csrf_field, esc

log = logging.getLogger("factory.ui")
KEY = "new_project"


def _write(h, fn):
    db = sqlite3.connect(h.app.cfg().db_path, timeout=10)
    try:
        N.ensure_tables(db)
        return fn(db)
    finally:
        db.close()


def _read(h, fn):
    db = h.app.ro_db()
    try:
        return fn(db) if db is not None else fn(None)
    finally:
        if db is not None:
            db.close()


def _page(h, title: str, body: str, csrf: str, status: int = 200) -> None:
    shown = L.flash_pop(csrf)
    msg, kind = shown if shown else (None, "ok")
    try:
        cfg = h.app.cfg()
    except Exception:  # noqa: BLE001 - the menu then shows no state markers
        cfg = None
    crumb = ('<nav class="crumb" aria-label="Breadcrumb"><a href="/settings">Settings</a> <span aria-hidden="true">›</span> '
             f'{esc(forms.section_group(KEY))}</nav>')
    h._send(status, views.page(title, f"{crumb}<h1>{esc(title)}</h1>{body}{forms.related(KEY)}", "/settings", csrf, flash=msg, flash_kind=kind,
                               side=forms.side_list(KEY, cfg), bare=True))


def _back(h, csrf: str, where: str, msg: str, kind: str = "ok") -> None:
    L.flash_set(csrf, msg, kind)
    h._redirect(where)


def _why_not(cfg, state_dir) -> str:
    """Why a message cannot be answered right now, or ''."""
    if not cfg.new_projects.enabled:
        return "New-project interviews are switched off (Settings, New project interviews)."
    if cfg.dry_run:
        return "The factory is in dry-run, so the interviewer will not reply. Go live on the Factory page first."
    if pause.paused(state_dir):
        return "The factory is paused, so the interviewer will not reply until it resumes."
    return ""


def _id(value) -> int:
    if not isinstance(value, str) or not value.isdigit() or len(value) > 12:
        raise L.Refused("That draft is not valid.")
    return int(value)


STATUS_WORD = {"interviewing": ("Interviewing", "warn"), "planned": ("Plan ready", "good"), "accepted": ("Accepted", "good"),
               "abandoned": ("Abandoned", "")}


def list_get(h, q: dict, csrf: str) -> None:
    cfg = h.app.cfg()
    rows = _read(h, lambda db: N.drafts(db) if db is not None else [])
    why = _why_not(cfg, h.app.state_dir())
    start = (f'<form method="post" action="/projects/new/start" class="card settings">{csrf_field(csrf)}'
             '<div class="field"><label for="np-title">Working title</label><input id="np-title" name="title" required '
             f'maxlength="{N.MAX_TITLE}" placeholder="Team rota"></div>'
             '<div class="field"><label for="np-brief">What do you want to build?</label><textarea id="np-brief" name="brief" rows="6" required '
             f'maxlength="{N.MAX_TEXT}" placeholder="Who is it for, what will they do with it, and anything you already know about how it should work."></textarea></div>'
             f'{f"<p class=muted>{esc(why)}</p>" if why else ""}<button>Start the interview</button></form>')
    intro = ('<p class="muted">Describe what you want to build. An agent asks a few rounds of questions, then recommends a pattern: the '
             'architecture, a modern stack, tests, CI and where it deploys. You can change any part of it before you accept. Nothing is '
             'created on GitHub until you do. <a href="/settings?section=new_projects">Interview settings</a></p>')
    table = ""
    if rows:
        trs = "".join(
            f'<tr><td data-l="Project"><a href="/projects/draft?id={int(d["id"])}">{esc(d["title"])}</a></td>'
            f'<td data-l="State">{badge(*STATUS_WORD.get(d["status"], (d["status"], "")))}</td>'
            f'<td data-l="Pattern">{esc(N.CATALOGUE[d["plan"]["pattern"]].title) if d["plan"] and d["plan"].get("pattern") in N.CATALOGUE else "—"}</td>'
            f'<td data-l="Updated">{esc(ago(d["updated"]))}</td></tr>' for d in rows)
        table = f'<h2>Drafts</h2><table class="stack"><thead><tr><th>Project</th><th>State</th><th>Pattern</th><th>Updated</th></tr></thead><tbody>{trs}</tbody></table>'
    _page(h, "New project", intro + start + table, csrf)


def start(h, form, csrf: str) -> None:
    cfg = h.app.cfg()
    title = " ".join((form.get("title") or "").split())
    brief = (form.get("brief") or "").replace("\r\n", "\n").strip()
    try:
        if (why := _why_not(cfg, h.app.state_dir())):
            raise L.Refused(why)
        if not title or len(title) > N.MAX_TITLE:
            raise L.Refused(f"Give the project a working title ({N.MAX_TITLE} characters at most).")
        if not brief or len(brief) > N.MAX_TEXT:
            raise L.Refused(f"Describe what you want to build ({N.MAX_TEXT:,} characters at most).")
    except L.Refused as e:
        return _back(h, csrf, "/projects/new", str(e), "bad")
    draft_id = _write(h, lambda db: N.create(db, title, brief))
    log.info("new project: draft %d started", draft_id)
    _back(h, csrf, f"/projects/draft?id={draft_id}", "Started. The interviewer's first questions appear here in a moment.")


# ---- one draft ----

def _turn_html(t: dict) -> str:
    if t["author"] == "agent":
        return (f'<div class="chat-turn chat-agent"><p class="muted">Interviewer · {esc(ago(t["created"]))}</p>'
                f'<div class="md">{md_render(t["body"])[0]}</div></div>')
    note = {"failed": "No reply: ", "refused": "No reply: "}.get(t["status"], "")
    tail = (f'<p class="muted">{esc(note + t["detail"])}</p>' if note and t["detail"] else
            '<p class="muted">Waiting for the interviewer…</p>' if t["status"] in ("pending", "running") else "")
    return (f'<div class="chat-turn chat-person"><p class="muted">You · {esc(ago(t["created"]))}</p>'
            f'<p class="chat-text">{esc(t["body"])}</p>{tail}</div>')


def _questions_html(asked: list) -> str:
    out = []
    for q in asked:
        qid = esc(q["id"])
        opts = "".join(f'<label class="check"><input type="radio" name="q_{qid}" value="{esc(o[0])}"> {esc(o[1])}'
                       f'{" <span class=muted>(suggested)</span>" if o[0] == q["recommended"] else ""}</label>' for o in q["options"])
        out.append(f'<fieldset class="np-q"><legend>{esc(q["question"])}</legend>{opts}'
                   f'<input name="o_{qid}" maxlength="1000" placeholder="Or in your own words" aria-label="Your own answer to: {esc(q["question"])}">'
                   f'<p class="muted">{esc(q["reason"])}</p></fieldset>')
    return "".join(out)


def _plan_html(d: dict, csrf: str) -> str:
    plan = d["chosen"] if d["status"] == "accepted" else d["plan"]
    if not plan:
        return ""
    p, dep = N.CATALOGUE[plan["pattern"]], N.DEPLOYS[plan["deploy"]]
    repos = "".join(f'<li><code>{esc(plan["name"] + s)}</code>: {esc(r)}</li>' for s, r in p.repos)
    stack = "".join(f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>" for k, v in p.stack)
    lst = lambda xs: "<ul>" + "".join(f"<li>{esc(x)}</li>" for x in xs) + "</ul>" if xs else ""
    body = (f'<p>{esc(plan["summary"])}</p>'
            f'<table class="meta"><tr><th>Pattern</th><td><strong>{esc(p.title)}</strong></td></tr>{stack}'
            f'<tr><th>Tests</th><td>{esc(p.tests)}</td></tr><tr><th>Deploys to</th><td><strong>{esc(dep.title)}</strong>. {esc(dep.how)}</td></tr></table>'
            f'<h3>Set up from day one</h3>{lst(p.practices)}<h3>Repositories</h3><ul>{repos}</ul>'
            + (f'<h3>Why this pattern</h3>{lst(plan["reasons"])}' if plan.get("reasons") else "")
            + (f'<p class="muted">You chose this over the recommended {esc(N.CATALOGUE[d["plan"]["pattern"]].title)}.</p>'
               if d["status"] == "accepted" and d["plan"] and d["plan"]["pattern"] != plan["pattern"] else "")
            + (f'<h3>First features</h3><ol>{"".join(f"<li>{esc(x)}</li>" for x in plan["first_features"])}</ol>' if plan.get("first_features") else "")
            + (f'<h3>Notes</h3><p>{esc(plan["notes"])}</p>' if plan.get("notes") else ""))
    if d["status"] == "accepted":
        return (f'<section class="card np-plan" aria-labelledby="np-plan-h"><h2 id="np-plan-h">Accepted plan</h2>{body}'
                '<p class="muted">Next the factory walks you through creating these repositories and pushes the first commit: the '
                'skeleton, tests, CI and the deploy workflow. That step is not built yet.</p></section>')
    if d["status"] != "planned":
        return f'<section class="card np-plan"><h2>Last plan</h2>{body}</section>'
    choices = "".join(
        f'<optgroup label="{esc(pp.title)}">' + "".join(
            f'<option value="{esc(pp.id)}|{esc(x)}"{" selected" if (pp.id, x) == (plan["pattern"], plan["deploy"]) else ""}>'
            f'{esc(pp.title)} · {esc(N.DEPLOYS[x].title)}</option>' for x in pp.deploys) + "</optgroup>"
        for pp in N.CATALOGUE.values())
    hidden = f'{csrf_field(csrf)}<input type="hidden" name="id" value="{int(d["id"])}">'
    return (f'<section class="card np-plan" aria-labelledby="np-plan-h"><h2 id="np-plan-h">Recommended plan</h2>{body}'
            f'<form method="post" action="/projects/draft/accept" class="settings">{hidden}'
            f'<div class="field"><label for="np-choice">Pattern and where it deploys</label><select id="np-choice" name="choice">{choices}</select></div>'
            f'<div class="field"><label for="np-name">Repository name</label><input id="np-name" name="name" value="{esc(plan["name"])}" '
            f'pattern="{N.NAME.pattern}" maxlength="40" required></div>'
            '<button>Accept this plan</button></form>'
            '<p class="muted">Not quite right? Say what to change in the interview below and the interviewer revises the plan.</p></section>')


def body(cfg, db, d: dict, csrf: str, state_dir) -> str:
    """The draft's live part: plan, conversation and the reply form. It replaces itself while a reply is awaited (app.js chatTick polls it)."""
    rows = N.turns(db, d["id"]) if db is not None else []
    waiting = any(t["author"] == "person" and t["status"] in ("pending", "running") for t in rows)
    last = next((t for t in reversed(rows) if t["author"] == "agent"), None)
    turns = "".join(_turn_html(t) for t in rows)
    hidden = f'{csrf_field(csrf)}<input type="hidden" name="id" value="{int(d["id"])}">'
    why = _why_not(cfg, state_dir)
    if d["status"] in ("accepted", "abandoned"):
        form = ""
    elif waiting:
        form = '<p class="muted">The interviewer is writing…</p>'
    else:
        asked = last["questions"] if last and last["id"] == rows[-1]["id"] else []
        form = (f'<form method="post" action="/projects/draft/send" class="np-reply">{hidden}{_questions_html(asked)}'
                f'<div class="field"><label for="np-text">{"Anything else" if asked else "Your reply"}</label>'
                f'<textarea id="np-text" name="text" rows="3" maxlength="{N.MAX_TEXT}"></textarea></div>'
                f'{f"<p class=muted>{esc(why)}</p>" if why else ""}<button>Send</button></form>'
                f'<form method="post" action="/projects/draft/abandon" class="inline">{hidden}<button class="link">Abandon this draft</button></form>')
    word, kind = STATUS_WORD.get(d["status"], (d["status"], ""))
    return (f'<div class="np-draft" data-src="/fragment/interview?id={int(d["id"])}" data-pending="{1 if waiting else 0}">'
            f'<p>{badge(word, kind)}</p>{_plan_html(d, csrf)}'
            f'<section class="card" aria-labelledby="np-chat-h"><h2 id="np-chat-h">Interview</h2><div class="chat-turns">{turns}</div>{form}</section></div>')


def _load(h, q: dict):
    try:
        draft_id = _id(q.get("id", ""))
    except L.Refused:
        return None, None
    db = h.app.ro_db()
    return (N.draft(db, draft_id) if db is not None else None), db


def draft_get(h, q: dict, csrf: str) -> None:
    d, db = _load(h, q)
    try:
        if d is None:
            return _page(h, "New project", '<p>That draft does not exist. <a href="/projects/new">All drafts</a></p>', csrf, 404)
        html = body(h.app.cfg(), db, d, csrf, h.app.state_dir())
    finally:
        if db is not None:
            db.close()
    _page(h, d["title"], '<p><a href="/projects/new">‹ All new projects</a></p>' + html, csrf)


def fragment_get(h, q: dict, csrf: str) -> None:
    d, db = _load(h, q)
    try:
        if d is None:
            return h._send(404, "no such draft", "text/plain")
        html = body(h.app.cfg(), db, d, csrf, h.app.state_dir())
    finally:
        if db is not None:
            db.close()
    h._send(200, html)


def send(h, form, csrf: str) -> None:
    cfg = h.app.cfg()
    try:
        draft_id = _id(form.get("id", ""))
    except L.Refused as e:
        return _back(h, csrf, "/projects/new", str(e), "bad")
    here = f"/projects/draft?id={draft_id}"
    try:
        if (why := _why_not(cfg, h.app.state_dir())):
            raise L.Refused(why)

        def add(db):
            d = N.draft(db, draft_id)
            if d is None or d["status"] not in ("interviewing", "planned"):
                raise L.Refused("This draft is closed.")
            if N.busy(db, draft_id):
                raise L.Refused("Wait for the interviewer's reply first.")
            if N.rounds(db, draft_id) >= cfg.new_projects.max_rounds * 2:
                raise L.Refused("This interview is long enough. Accept the plan, or start a new draft.")
            rows = N.turns(db, draft_id)
            asked = rows[-1]["questions"] if rows and rows[-1]["author"] == "agent" else []
            picks = N.answer_text(asked, form.get)
            text = "\n\n".join(x for x in (picks, (form.get("text") or "").replace("\r\n", "\n").strip()) if x)
            if not text:
                raise L.Refused("Answer a question or write a reply first.")
            if len(text) > N.MAX_TEXT:
                raise L.Refused(f"The reply is too long ({N.MAX_TEXT:,} characters at most).")
            return N.add_person(db, draft_id, text)
        _write(h, add)
    except L.Refused as e:
        return _back(h, csrf, here, str(e), "bad")
    _back(h, csrf, here, "Sent. The interviewer's reply appears here in a moment.")


def accept(h, form, csrf: str) -> None:
    try:
        draft_id = _id(form.get("id", ""))
    except L.Refused as e:
        return _back(h, csrf, "/projects/new", str(e), "bad")
    here = f"/projects/draft?id={draft_id}"
    pattern, _, deploy = (form.get("choice") or "").partition("|")
    name = (form.get("name") or "").strip()
    try:
        def take(db):
            d = N.draft(db, draft_id)
            if d is None or d["status"] != "planned" or not d["plan"]:
                raise L.Refused("There is no plan to accept on this draft.")
            kept = {k: v for k, v in d["plan"].items() if v and not (k == "reasons" and pattern != d["plan"]["pattern"])}   # its reasons argued for another pattern
            chosen = N.valid_plan({**kept, "pattern": pattern, "deploy": deploy, "name": name})
            if chosen is None:
                raise L.Refused("Choose a pattern with one of its deploy targets, and a repository name of lower-case letters, digits and dashes.")
            if not N.accept(db, draft_id, chosen):
                raise L.Refused("There is no plan to accept on this draft.")
            return chosen
        chosen = _write(h, take)
    except L.Refused as e:
        return _back(h, csrf, here, str(e), "bad")
    log.info("new project: draft %d accepted (%s, %s)", draft_id, chosen["pattern"], chosen["deploy"])
    _back(h, csrf, here, "Plan accepted.")


def abandon(h, form, csrf: str) -> None:
    try:
        draft_id = _id(form.get("id", ""))
    except L.Refused as e:
        return _back(h, csrf, "/projects/new", str(e), "bad")
    _write(h, lambda db: N.abandon(db, draft_id))
    _back(h, csrf, "/projects/new", "Draft abandoned.")


GET = {"/projects/new": list_get, "/projects/draft": draft_get, "/fragment/interview": fragment_get}
POST = {"/projects/new/start": start, "/projects/draft/send": send, "/projects/draft/accept": accept, "/projects/draft/abandon": abandon}
