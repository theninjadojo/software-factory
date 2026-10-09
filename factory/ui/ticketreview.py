"""The ticket review page: ask for a review of a repository's open and in-progress tickets, then pick which recommendations to apply.

The agent only recommends (see factory/ticketreview.py). Here a person starts a review and multi-selects recommendations to apply or
dismiss. The UI writes decisions to the database; the orchestrator applies them (it holds the GitHub token for that). Every id the
browser sends is checked against the database, and the repository, sources and scope come from fixed sets."""
import time
from urllib.parse import quote

from .. import ticketreview as TR
from .. import tracker
from . import labels as L, views
from .views import csrf_field, esc

SOURCE_CHOICES = (("local,github", "Local and GitHub tickets"), ("local", "Local tickets only"), ("github", "GitHub issues only"))
SCOPE_CHOICES = (("all", "Open and in progress"), ("new", "Not started only"), ("progress", "In progress only"))
VERDICT = {"built": ("Close: already built", "good"), "duplicate": ("Merge", "blue"), "split": ("Split", "warn"), "rerun": ("Failed: re-run", "bad")}
STATUS = {"proposed": "", "queued": "applying at the next poll…", "applied": "applied", "failed": "failed", "rejected": "dismissed"}
TABS = (("waiting", "Waiting for you"), ("applied", "Applied"), ("dismissed", "Dismissed"))
WHAT = {"built": "It is closed with a comment naming the evidence.",
        "split": "The split is proposed on the ticket. Nothing is created until you approve it there.",
        "rerun": "It starts again with Auto, as its own Auto button does."}


def _what(repo: str, p: dict) -> str:
    if p["verdict"] == "duplicate":
        keep, close = esc(tracker.ref(repo, p["target"])), esc(tracker.ref(repo, p["issue"]))
        return (f"{close}'s description and the addresses of its images are added to {keep} as a comment, so the work there "
                f"covers it. {close} is closed with a link to {keep}.")
    return WHAT[p["verdict"]]


def _detail(repo: str, p: dict) -> str:
    if p["verdict"] == "built":
        return '<p><span class="muted">Evidence:</span> ' + " ".join(f"<code>{esc(e)}</code>" for e in p["evidence"]) + "</p>"
    if p["verdict"] == "duplicate":
        return f'<p><span class="muted">Keep:</span> {views.ticket_link(repo, p["target"])}</p>'
    if p["verdict"] == "split":
        return ('<ol class="tr-parts">' + "".join(f'<li>{esc(it.get("title", ""))} <span class="muted">({esc(it.get("repo", ""))})</span></li>'
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
    return (f'<li class="tr-item">{pick}<div class="tr-body"><p class="tr-head"><span class="badge {tone}">{esc(word)}</span> '
            f'{views.ticket_link(repo, p["issue"])} {esc(p["title"])}</p>{_detail(repo, p)}'
            f'<p><span class="muted">Why:</span> {esc(p["reason"]) or "(no reason given)"}</p>{note}'
            + (f'<p class="muted">What happens: {_what(repo, p)}</p>' if p["status"] == "proposed" else "")
            + (f'<p class="muted">{esc(state)}{detail}</p>' if state or detail else "") + "</div></li>")


def page(cfg, db, repo: str, csrf: str, now: float | None = None, view: str = "waiting") -> str:
    now = time.time() if now is None else now
    view = view if view in TR.VIEWS else "waiting"
    picker = "".join(f'<option value="{esc(r)}"{" selected" if r == repo else ""}>{esc(r)}</option>' for r in cfg.repos)
    sources = "".join(f'<option value="{v}">{esc(t)}</option>' for v, t in SOURCE_CHOICES)
    scopes = "".join(f'<option value="{v}">{esc(t)}</option>' for v, t in SCOPE_CHOICES)
    start = (f'<form method="post" action="/tickets/review/run" class="tr-run">{csrf_field(csrf)}'
             f'<label>Repository <select name="repo">{picker}</select></label>'
             f'<label>Tickets <select name="scope">{scopes}</select></label>'
             f'<label>From <select name="sources">{sources}</select></label><button type="submit">Review tickets</button></form>')
    head = ('<p class="muted">An agent reads your open and in-progress tickets and the code, and recommends what to do with each one: '
            'close one that is already built, merge a duplicate into the ticket to keep, split one that is too large, or re-run one '
            'that failed. Finished work (a pull request is open) is left out. It changes nothing: you pick the recommendations to '
            'apply, at most %d at a time.</p>' % tracker.MAX_BULK)
    last = TR.latest(db, repo) if db is not None else None
    if last:
        when = views.ago(last["created"], now)
        status = {"requested": "waiting for the factory's next poll", "running": "running", "done": "done", "failed": "failed"}[last["status"]]
        info = f'<p class="muted" role="status">Last review of {esc(repo)}: {esc(status)}, asked {esc(when)}. {esc(last["detail"])}</p>'
    else:
        info = f'<p class="muted" role="status">{esc(repo)} has not been reviewed yet.</p>'
    n = TR.counts(db, repo) if db is not None else {}
    tabs = "".join(f'<a class="sd-chip{" on" if k == view else ""}" href="/tickets/review?repo={esc(quote(repo, safe=""))}&amp;view={k}"'
                   f'{" aria-current=page" if k == view else ""}>{esc(t)} <b class="mono">{int(n.get(k, 0))}</b></a>' for k, t in TABS)
    nav = f'<nav class="sd-chips tr-tabs" aria-label="Recommendations">{tabs}</nav>'
    props = TR.proposals(db, repo, view) if db is not None else []
    if not props:
        empty = {"waiting": "Nothing is waiting for you.", "applied": "Nothing was applied yet.", "dismissed": "Nothing was dismissed."}[view]
        return head + start + info + nav + f'<p class="muted">{empty}</p>'
    items = f'<ul class="tr-list">{"".join(_item(repo, p) for p in props)}</ul>'
    if view != "waiting":
        return head + start + info + nav + items
    form = (f'<form method="post" action="/tickets/review/apply">{csrf_field(csrf)}<input type="hidden" name="repo" value="{esc(repo)}">'
            f'{items}<p class="tr-acts"><button type="submit">Apply selected</button> '
            '<button type="submit" class="secondary" formaction="/tickets/review/reject">Dismiss selected</button></p></form>')
    return head + start + info + nav + form


def link(cfg, project: str, csrf: str) -> str:
    """The Tickets header's way in: for the repository of the project being shown (its first configured one), else the first."""
    if not cfg.ticket_review.enabled or not csrf or not cfg.repos:
        return ""
    mine = [r.repo for p in cfg.projects if p.name == project for r in p.repos if r.repo in cfg.repos]
    repo = (mine or cfg.repos)[0]
    return f'<a class="btn secondary" href="/tickets/review?repo={esc(quote(repo, safe=""))}">Review tickets</a>'


def review_get(h, q: dict, csrf: str) -> None:
    cfg = h.app.cfg()
    if not cfg.repos:
        return L._page(h, 200, "Ticket review", '<p class="muted">No repositories are configured.</p>', csrf)
    repo = q.get("repo") if q.get("repo") in cfg.repos else cfg.repos[0]
    db = h.app.ro_db()
    try:
        body = page(cfg, db, repo, csrf, view=q.get("view") or "waiting")
    finally:
        if db is not None:
            db.close()
    shown = L.flash_pop(csrf)
    L._page(h, 200, "Ticket review", '<p><a href="/tickets">← Tickets</a></p>' + body, csrf, *(shown or (None, "ok")))


def _back(h, csrf: str, repo: str, msg: str, kind: str = "ok") -> None:
    L.flash_set(csrf, msg, kind)
    h._redirect("/tickets/review?repo=" + views.quote(repo, safe="/"))


def run_post(h, form, csrf: str) -> None:
    cfg = h.app.cfg()
    repo, sources, scope = form.get("repo", ""), form.get("sources", ""), form.get("scope") or "all"
    if repo not in cfg.repos or sources not in TR.SOURCES or scope not in TR.SCOPES or not cfg.ticket_review.enabled:
        return h._send(400, "bad request", "text/plain")
    if h.app.request_ticket_review(repo, sources, scope):
        return _back(h, csrf, repo, "Review requested. The factory starts it within a poll (up to a minute); nothing runs in dry-run or while paused.")
    _back(h, csrf, repo, "A review of this repository is already waiting or running.", "bad")


def _decide(h, form, csrf: str, accept: bool) -> None:
    cfg = h.app.cfg()
    repo = form.get("repo", "")
    if repo not in cfg.repos:
        return h._send(400, "bad request", "text/plain")
    ids = [int(v) for v in form.getall("id") if v.isdigit() and len(v) <= 9]
    if not ids:
        return _back(h, csrf, repo, "Select at least one recommendation.", "bad")
    if len(ids) > tracker.MAX_BULK:
        return _back(h, csrf, repo, f"Select at most {tracker.MAX_BULK} recommendations at a time. Nothing was changed.", "bad")
    n = h.app.decide_proposals(repo, ids, accept)
    if accept:
        return _back(h, csrf, repo, f"Applying {n} recommendation(s). The factory does it at its next poll." if n else "Nothing to apply: those were already decided.", "ok" if n else "bad")
    _back(h, csrf, repo, f"Dismissed {n} recommendation(s). They will not be recommended again." if n else "Nothing to dismiss.", "ok" if n else "bad")


def apply_post(h, form, csrf: str) -> None:
    _decide(h, form, csrf, True)


def reject_post(h, form, csrf: str) -> None:
    _decide(h, form, csrf, False)
