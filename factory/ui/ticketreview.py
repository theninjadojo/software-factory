"""The ticket review page: ask for a review of a repository's open and in-progress tickets, then pick which recommendations to apply.

The agent only recommends (see factory/ticketreview.py). Here a person starts a review and multi-selects recommendations to apply or
dismiss. The UI writes decisions to the database; the orchestrator applies them (it holds the GitHub token for that). Every id the
browser sends is checked against the database, and the repository, sources and scope come from fixed sets."""
import re
import time
from urllib.parse import quote

from .. import ticketreview as TR
from .. import pause, tracker
from ..sanitize import md_render
from . import labels as L, views
from .views import csrf_field, esc

SOURCE_CHOICES = (("local,github", "Local and GitHub tickets"), ("local", "Local tickets only"), ("github", "GitHub issues only"))
SCOPE_CHOICES = (("all", "Open and in progress"), ("new", "Not started only"), ("progress", "In progress only"))
VERDICT = {"built": ("Close: already built", "good"), "duplicate": ("Merge", "blue"), "split": ("Split", "warn"), "rerun": ("Failed: re-run", "bad")}
STATUS = {"proposed": "", "queued": "applying at the next poll…", "applied": "applied", "failed": "failed", "rejected": "dismissed", "stale": "out of date:"}
TABS = (("waiting", "Waiting for you"), ("applied", "Applied"), ("dismissed", "Dismissed"), ("outdated", "Out of date"))
WHAT = {"built": "It is closed with a comment naming the evidence.",
        "split": "The split is proposed on the ticket. Nothing is created until you approve it there.",
        "rerun": "It starts again with Auto, as its own Auto button does."}


def _link(repo: str, n) -> str:
    """A ticket of the reviewed repository by its short name (#19, L-3), linked like views.ticket_link."""
    shown = tracker.display(int(n))
    if tracker.is_local(int(n)):
        return f'<a href="/ticket?repo={esc(quote(repo, safe=""))}&amp;n={int(n)}">{esc(shown)}</a>'
    return views.gh_link(f"https://github.com/{repo}/issues/{int(n)}", shown) if views.REPO.match(repo) else esc(shown)


def _what(repo: str, p: dict) -> str:
    if p["verdict"] == "duplicate":
        keep, close = esc(tracker.display(p["target"])), esc(tracker.display(p["issue"]))
        return (f"{close}'s description and the addresses of its images are added to {keep} as a comment, so the work there "
                f"covers it. {close} is closed with a link to {keep}.")
    return WHAT[p["verdict"]]


def _detail(repo: str, p: dict) -> str:
    if p["verdict"] == "built":
        return '<p><span class="muted">Evidence:</span> ' + " ".join(f"<code>{esc(e)}</code>" for e in p["evidence"]) + "</p>"
    if p["verdict"] == "duplicate":
        return f'<p><span class="muted">Keep:</span> {_link(repo, p["target"])}</p>'
    if p["verdict"] == "split":
        return ('<ol class="tr-parts">' + "".join(f'<li>{esc(it.get("title", ""))}' + (f' <span class="muted">({esc(it.get("repo", ""))})</span>' if it.get("repo") != repo else "") + "</li>"
                                                  for it in p["evidence"] if isinstance(it, dict)) + "</ol>")
    return ""


def _item(repo: str, p: dict) -> str:
    word, tone = VERDICT.get(p["verdict"], (p["verdict"], ""))
    shown = esc(tracker.display(p["issue"]))
    pick = (f'<input type="checkbox" name="id" value="{int(p["id"])}" aria-label="Select: {esc(word)} {shown}">'
            if p["status"] == "proposed" else "")
    state = STATUS.get(p["status"], "")
    note = f'<p class="muted">⚠ {esc(p["note"])}: closing it does not unblock them.</p>' if p["note"] and p["verdict"] in ("built", "duplicate") else ""
    detail = f' <span class="muted">{esc(p["detail"])}</span>' if p["detail"] else ""
    warn = ""
    if p.get("warn"):
        confirm = (f'<label class="check"><input type="checkbox" name="confirm" value="{int(p["id"])}"> Merge anyway</label>'
                   if p["status"] == "proposed" else "")
        warn = f'<div class="tr-warn" role="note"><p><strong>⚠ {esc(p["warn"])}.</strong></p>{confirm}</div>'
    return (f'<li class="tr-item">{pick}<div class="tr-body"><p class="tr-head"><span class="badge {tone}">{esc(word)}</span> '
            f'{_link(repo, p["issue"])} {esc(p["title"])}</p>{_detail(repo, p)}{warn}'
            f'<p><span class="muted">Why:</span> {esc(p["reason"]) or "(no reason given)"}</p>{note}'
            + (f'<p class="muted">What happens: {_what(repo, p)}</p>' if p["status"] == "proposed" else "")
            + (f'<p class="muted">{esc(state)}{detail}</p>' if state or detail else "") + "</div></li>")


REVIEW_STATE = {"requested": ("Waiting to start", "warn"), "running": ("Running", "blue"), "done": ("Done", "good"), "failed": ("Failed", "bad")}
SCOPE_NAME, SOURCE_NAME = dict(SCOPE_CHOICES), dict(SOURCE_CHOICES)
EMPTY = {"waiting": "Nothing is waiting for you.", "applied": "Nothing was applied yet.", "dismissed": "Nothing was dismissed.",
         "outdated": "Nothing is out of date."}


def _url(repo: str, rid=None, view: str = "") -> str:
    return (f'/tickets/review?repo={esc(quote(repo, safe=""))}' + (f"&amp;review={int(rid)}" if rid else "")
            + (f"&amp;view={esc(view)}" if view else ""))


def _run(r: dict, text: str = "") -> str:
    return f'<a href="/runs/{int(r["run_id"])}">{esc(text or "Open run #%d" % int(r["run_id"]))}</a>' if r.get("run_id") else ""


def history(cfg, db, selected: dict | None, now: float) -> str:
    """Every recent review of the configured repositories, newest first. Each opens its own write-up and recommendations."""
    rows = TR.reviews(db, cfg.repos) if db is not None else []
    if not rows:
        return ""
    out = []
    for r in rows:
        word, tone = REVIEW_STATE.get(r["status"], (r["status"], ""))
        on = selected is not None and r["id"] == selected["id"]
        found = f'<span class="muted">{int(r["found"])} recommendation(s)</span>' if r["status"] == "done" else ""
        out.append(f'<li{" class=on" if on else ""}><a href="{_url(r["repo"], r["id"])}"{" aria-current=true" if on else ""}>'
                   f'<b>Review #{int(r["id"])}</b><span>{esc(r["repo"].split("/")[-1])}</span>'
                   f'<span class="muted" title="{esc(views.ts(r["created"]))}">{esc(views.ago(r["created"], now))}</span>'
                   f'<span class="badge {tone}">{esc(word)}</span>{found}</a></li>')
    return f'<h2>Reviews</h2><ul class="tr-hist">{"".join(out)}</ul>'


def state(cfg, r: dict, found: int, now: float, paused: bool = False) -> str:
    """What the review is doing, in words: waiting, running (and for how long), done with what, or failed and why."""
    word, tone = REVIEW_STATE.get(r["status"], (r["status"], ""))
    notes = [x for x in r["detail"].split("; ") if x and not re.match(r"\d+ (recommendation|proposal)\(s\) from |reading \d+ ticket|no open tickets to review$", x)]
    detail = f'<p class="muted">{esc("; ".join(notes))}</p>' if notes else ""      # what was left out; the counts are in the headline
    if r["status"] == "requested":
        why = ("The factory is in dry-run, so it will not start." if cfg.dry_run else
               "The factory is paused, so it starts when you resume it." if paused else
               "The factory starts it at its next poll, usually within a minute. This page updates by itself.")
        head, text = "Waiting to start.", why
    elif r["status"] == "running":
        head = f"Running for {views.dur(r['created'], now)}."
        text = ((f"An agent is reading {int(r['tickets'])} ticket(s) and the code. " if r["tickets"] else "Gathering the tickets. ")
                + "It changes nothing. The recommendations show up here when it is done; this page updates by itself.")
        detail = ""
    elif r["status"] == "failed":
        head, text = "The review failed.", "No recommendations were made. Run it again, or open the run to see what went wrong."
    elif not r["tickets"]:
        head, text = "Done: there were no tickets to review.", "Nothing matched the tickets and sources you chose."
    elif not found:
        head, text = f"Done: no recommendations from {int(r['tickets'])} ticket(s).", "The agent found nothing to close, merge, split or re-run. Its write-up below says what it checked."
    else:
        head, text = f"Done: {found} recommendation(s) from {int(r['tickets'])} ticket(s).", "Pick the ones to apply below. Nothing changes until you do."
    run = _run(r, "Watch the run" if r["status"] == "running" else "")
    took = f" Took {views.dur(r['created'], r['finished'])}." if r["finished"] else ""
    return (f'<div class="tr-state {tone}" role="status"><p class="tr-state-head"><span class="badge {tone}">{esc(word)}</span> <strong>{esc(head)}</strong></p>'
            f'<p>{esc(text)}{esc(took)}</p>{detail}' + (f"<p>{run}</p>" if run else "") + "</div>")


def panel(cfg, db, r: dict, csrf: str, view: str, now: float, paused: bool = False) -> str:
    """One review: its state, the agent's write-up, and its recommendations by view."""
    repo, rid = r["repo"], r["id"]
    n = TR.counts(db, repo, rid) if db is not None else {}
    found = sum(n.values())
    sub = " · ".join((SCOPE_NAME.get(r["scope"], r["scope"]), SOURCE_NAME.get(r["sources"], r["sources"]), "asked " + views.ago(r["created"], now)))
    out = f'<h2>Review #{int(rid)} of {esc(repo)}</h2><p class="muted">{esc(sub)}</p>' + state(cfg, r, found, now, paused)
    doc = TR.write_up(db, r["run_id"]) if db is not None and r["status"] in ("done", "failed") else ""
    if doc:
        body, _ = md_render(doc)
        out += (f'<details class="tr-doc"{" open" if not found else ""}><summary>What the agent found</summary>'
                f'<article class="docview">{body}</article></details>')
    if not found:
        return out
    tabs = "".join(f'<a class="sd-chip{" on" if k == view else ""}" href="{_url(repo, rid, k)}"'
                   f'{" aria-current=page" if k == view else ""}>{esc(t)} <b class="mono">{int(n.get(k, 0))}</b></a>'
                   for k, t in TABS if k != "outdated" or n.get(k) or view == k)
    out += f'<nav class="sd-chips tr-tabs" aria-label="Recommendations">{tabs}</nav>'
    props = TR.proposals(db, repo, view, rid) if db is not None else []
    if not props:
        gone = " A newer review or a change to the tickets replaced them: see Out of date." if view == "waiting" and n.get("outdated") else ""
        return out + f'<p class="muted">{EMPTY[view]}{gone}</p>'
    items = f'<ul class="tr-list">{"".join(_item(repo, p) for p in props)}</ul>'
    if view != "waiting":
        return out + items
    return (out + f'<form method="post" action="/tickets/review/apply">{csrf_field(csrf)}<input type="hidden" name="repo" value="{esc(repo)}">'
            f'<input type="hidden" name="review" value="{int(rid)}">{items}<p class="tr-acts"><button type="submit">Apply selected</button> '
            '<button type="submit" class="secondary" formaction="/tickets/review/reject">Dismiss selected</button></p></form>')


def selected(cfg, db, repo: str, rid=None) -> dict | None:
    """The review asked for (when it is of a configured repository), else the latest of the repository."""
    if db is None:
        return None
    r = TR.review(db, rid) if str(rid or "").isdigit() and len(str(rid)) <= 9 else None
    return r if r is not None and r["repo"] in cfg.repos else TR.latest(db, repo)


def live(cfg, db, repo: str, csrf: str, now: float, view: str = "waiting", rid=None, paused: bool = False) -> str:
    view = view if view in TR.VIEWS else "waiting"
    r = selected(cfg, db, repo, rid)
    body = panel(cfg, db, r, csrf, view, now, paused) if r else f'<p class="muted" role="status">{esc(repo)} has not been reviewed yet.</p>'
    return body + history(cfg, db, r, now)


def page(cfg, db, repo: str, csrf: str, now: float | None = None, view: str = "waiting", rid=None, paused: bool = False) -> str:
    now = time.time() if now is None else now
    r = selected(cfg, db, repo, rid)
    repo = r["repo"] if r else repo
    picker = "".join(f'<option value="{esc(x)}"{" selected" if x == repo else ""}>{esc(x)}</option>' for x in cfg.repos)
    sources = "".join(f'<option value="{v}">{esc(t)}</option>' for v, t in SOURCE_CHOICES)
    scopes = "".join(f'<option value="{v}">{esc(t)}</option>' for v, t in SCOPE_CHOICES)
    start = (f'<form method="post" action="/tickets/review/run" class="tr-run">{csrf_field(csrf)}'
             f'<label>Repository <select name="repo">{picker}</select></label>'
             f'<label>Tickets <select name="scope">{scopes}</select></label>'
             f'<label>From <select name="sources">{sources}</select></label><button type="submit">Review tickets</button></form>')
    head = ('<p class="muted">An agent reads your open and in-progress tickets and the code, and recommends what to do with each one: '
            'close one that is already built, merge a duplicate into the ticket to keep, split one that is too large, or re-run one '
            'that failed. Finished work (a pull request is open) is left out. It changes nothing: you pick the recommendations to '
            'apply, at most %d at a time. Every review is kept below with what it found.</p>' % tracker.MAX_BULK)
    return head + start + f'<div id="live" data-src="/tickets/review/live">{live(cfg, db, repo, csrf, now, view, r["id"] if r else None, paused)}</div>'


def link(cfg, project: str, csrf: str) -> str:
    """The Tickets header's way in: for the repository of the project being shown (its first configured one), else the first."""
    if not cfg.ticket_review.enabled or not csrf or not cfg.repos:
        return ""
    mine = [r.repo for p in cfg.projects if p.name == project for r in p.repos if r.repo in cfg.repos]
    repo = (mine or cfg.repos)[0]
    return f'<a class="btn secondary" href="/tickets/review?repo={esc(quote(repo, safe=""))}">Review tickets</a>'


def _args(h, q: dict):
    cfg = h.app.cfg()
    repo = q.get("repo") if q.get("repo") in cfg.repos else cfg.repos[0]
    return cfg, repo, q.get("view") or "waiting", q.get("review"), pause.paused(h.app.state_dir())


def review_get(h, q: dict, csrf: str) -> None:
    cfg = h.app.cfg()
    if not cfg.repos:
        return L._page(h, 200, "Ticket review", '<p class="muted">No repositories are configured.</p>', csrf)
    cfg, repo, view, rid, paused = _args(h, q)
    db = h.app.ro_db()
    try:
        body = page(cfg, db, repo, csrf, view=view, rid=rid, paused=paused)
    finally:
        if db is not None:
            db.close()
    shown = L.flash_pop(csrf)
    L._page(h, 200, "Ticket review", '<p><a href="/tickets">← Tickets</a></p>' + body, csrf, *(shown or (None, "ok")))


def live_get(h, q: dict, csrf: str) -> None:
    """The live part of the page, refreshed every few seconds so a waiting or running review shows its progress."""
    if not h.app.cfg().repos:
        return h._send(200, "")
    cfg, repo, view, rid, paused = _args(h, q)
    db = h.app.ro_db()
    try:
        html = live(cfg, db, repo, csrf, time.time(), view, rid, paused)
    finally:
        if db is not None:
            db.close()
    h._send(200, html)


def _back(h, csrf: str, repo: str, msg: str, kind: str = "ok", rid: str = "") -> None:
    L.flash_set(csrf, msg, kind)
    h._redirect("/tickets/review?repo=" + views.quote(repo, safe="/") + (f"&review={int(rid)}" if rid.isdigit() and len(rid) <= 9 else ""))


def run_post(h, form, csrf: str) -> None:
    cfg = h.app.cfg()
    repo, sources, scope = form.get("repo", ""), form.get("sources", ""), form.get("scope") or "all"
    if repo not in cfg.repos or sources not in TR.SOURCES or scope not in TR.SCOPES or not cfg.ticket_review.enabled:
        return h._send(400, "bad request", "text/plain")
    if h.app.request_ticket_review(repo, sources, scope):
        return _back(h, csrf, repo, "Review requested. Its progress shows below; the page updates by itself.")
    _back(h, csrf, repo, "A review of this repository is already waiting or running.", "bad")


def _decide(h, form, csrf: str, accept: bool) -> None:
    cfg = h.app.cfg()
    repo = form.get("repo", "")
    if repo not in cfg.repos:
        return h._send(400, "bad request", "text/plain")
    def back(msg: str, kind: str = "ok") -> None:
        _back(h, csrf, repo, msg, kind, form.get("review") or "")
    ids = [int(v) for v in form.getall("id") if v.isdigit() and len(v) <= 9]
    if not ids:
        return back("Select at least one recommendation.", "bad")
    if len(ids) > tracker.MAX_BULK:
        return back(f"Select at most {tracker.MAX_BULK} recommendations at a time. Nothing was changed.", "bad")
    if accept:
        db = h.app.ro_db()
        try:
            unconfirmed = TR.needs_confirm(db, ids) - {int(v) for v in form.getall("confirm") if v.isdigit() and len(v) <= 9} if db is not None else set()
        finally:
            if db is not None:
                db.close()
        if unconfirmed:
            return back("Both tickets of a merge are being built. Tick Merge anyway on it to apply it. Nothing was changed.", "bad")
    n = h.app.decide_proposals(repo, ids, accept)
    if accept:
        return back(f"Applying {n} recommendation(s). The factory does it at its next poll." if n else "Nothing to apply: those were already decided.", "ok" if n else "bad")
    back(f"Dismissed {n} recommendation(s). They will not be recommended again." if n else "Nothing to dismiss.", "ok" if n else "bad")


def apply_post(h, form, csrf: str) -> None:
    _decide(h, form, csrf, True)


def reject_post(h, form, csrf: str) -> None:
    _decide(h, form, csrf, False)
