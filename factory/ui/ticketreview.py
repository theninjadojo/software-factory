"""The ticket review page: ask for a review of a repository's open tickets, then pick which proposals to close.

The agent only proposes (see factory/ticketreview.py). Here a person starts a review and multi-selects proposals to close or dismiss.
The UI writes decisions to the database; the orchestrator closes the tickets (it holds the GitHub token for that). Every id the
browser sends is checked against the database, and the repository and sources come from fixed sets."""
import time

from .. import ticketreview as TR
from .. import tracker
from . import labels as L, views
from .views import badge, csrf_field, esc

SOURCE_CHOICES = (("local,github", "Local and GitHub tickets"), ("local", "Local tickets only"), ("github", "GitHub issues only"))
VERDICT = {"built": "Already built", "duplicate": "Duplicate"}
STATUS = {"proposed": "", "queued": "closing…", "applied": "closed", "failed": "failed"}


def _evidence(p: dict) -> str:
    if p["verdict"] == "built":
        return " ".join(f"<code>{esc(e)}</code>" for e in p["evidence"])
    return "keep " + views.ticket_link(p["repo"], p["target"])


def _row(repo: str, p: dict) -> str:
    p = dict(p, repo=repo)
    pick = (f'<input type="checkbox" name="id" value="{int(p["id"])}" aria-label="Select {esc(tracker.display(p["issue"]))}">'
            if p["status"] == "proposed" else "")
    state = STATUS.get(p["status"], "")
    note = f'<br><span class="muted">⚠ {esc(p["note"])}: closing it does not unblock them.</span>' if p["note"] else ""
    detail = f'<br><span class="muted">{esc(p["detail"])}</span>' if p["detail"] else ""
    return (f'<tr><td data-l="">{pick}</td><td data-l="Ticket">{views.ticket_link(repo, p["issue"])}<br>{esc(p["title"])}</td>'
            f'<td data-l="Proposal">{badge(VERDICT[p["verdict"]], "")}<br>{_evidence(p)}</td>'
            f'<td data-l="Why">{esc(p["reason"])}{note}</td><td data-l="State">{esc(state)}{detail}</td></tr>')


def page(cfg, db, repo: str, csrf: str, now: float | None = None) -> str:
    now = time.time() if now is None else now
    picker = "".join(f'<option value="{esc(r)}"{" selected" if r == repo else ""}>{esc(r)}</option>' for r in cfg.repos)
    sources = "".join(f'<option value="{v}">{esc(t)}</option>' for v, t in SOURCE_CHOICES)
    start = (f'<form method="post" action="/tickets/review/run" class="inline">{csrf_field(csrf)}'
             f'<label>Repository <select name="repo">{picker}</select></label> '
             f'<label>Tickets <select name="sources">{sources}</select></label> <button type="submit">Review tickets</button></form>')
    head = ('<p class="muted">A read-only agent reads every open ticket and the code, and proposes which tickets are already built '
            'and which are duplicates. It changes nothing. You choose what to close: a closed ticket gets a comment pointing to the '
            'evidence or to the ticket to keep. At most %d at a time.</p>' % tracker.MAX_BULK)
    last = TR.latest(db, repo) if db is not None else None
    if last:
        when = views.ago(last["created"], now)
        status = {"requested": "waiting for the factory's next poll", "running": "running", "done": "done", "failed": "failed"}[last["status"]]
        info = f'<p class="muted">Last review of {esc(repo)}: {esc(status)}, asked {esc(when)}. {esc(last["detail"])}</p>'
    else:
        info = f'<p class="muted">{esc(repo)} has not been reviewed yet.</p>'
    props = TR.proposals(db, repo) if db is not None else []
    if not props:
        return head + start + info
    rows = "".join(_row(repo, p) for p in props)
    table = (f'<form method="post" action="/tickets/review/apply">{csrf_field(csrf)}<input type="hidden" name="repo" value="{esc(repo)}">'
             '<div class="scroll"><table class=stack><thead><tr><th></th><th>Ticket</th><th>Proposal</th><th>Why</th><th>State</th></tr></thead>'
             f'<tbody>{rows}</tbody></table></div>'
             '<button type="submit">Close selected</button> '
             '<button type="submit" class="secondary" formaction="/tickets/review/reject">Dismiss selected</button></form>')
    return head + start + info + table


def review_get(h, q: dict, csrf: str) -> None:
    cfg = h.app.cfg()
    if not cfg.repos:
        return L._page(h, 200, "Ticket review", '<p class="muted">No repositories are configured.</p>', csrf)
    repo = q.get("repo") if q.get("repo") in cfg.repos else cfg.repos[0]
    db = h.app.ro_db()
    try:
        body = page(cfg, db, repo, csrf)
    finally:
        if db is not None:
            db.close()
    shown = L.flash_pop(csrf)
    L._page(h, 200, "Ticket review", body, csrf, *(shown or (None, "ok")))


def _back(h, csrf: str, repo: str, msg: str, kind: str = "ok") -> None:
    L.flash_set(csrf, msg, kind)
    h._redirect("/tickets/review?repo=" + views.quote(repo, safe="/"))


def run_post(h, form, csrf: str) -> None:
    cfg = h.app.cfg()
    repo, sources = form.get("repo", ""), form.get("sources", "")
    if repo not in cfg.repos or sources not in TR.SOURCES or not cfg.ticket_review.enabled:
        return h._send(400, "bad request", "text/plain")
    if h.app.request_ticket_review(repo, sources):
        return _back(h, csrf, repo, "Review requested. The factory starts it within a poll (up to a minute); nothing runs in dry-run or while paused.")
    _back(h, csrf, repo, "A review of this repository is already waiting or running.", "bad")


def _decide(h, form, csrf: str, accept: bool) -> None:
    cfg = h.app.cfg()
    repo = form.get("repo", "")
    if repo not in cfg.repos:
        return h._send(400, "bad request", "text/plain")
    ids = [int(v) for v in form.getall("id") if v.isdigit() and len(v) <= 9]
    if not ids:
        return _back(h, csrf, repo, "Select at least one proposal.", "bad")
    if len(ids) > tracker.MAX_BULK:
        return _back(h, csrf, repo, f"Select at most {tracker.MAX_BULK} proposals at a time. Nothing was changed.", "bad")
    n = h.app.decide_proposals(repo, ids, accept)
    if accept:
        return _back(h, csrf, repo, f"Closing {n} ticket(s). The factory does it at its next poll." if n else "Nothing to close: those proposals were already decided.", "ok" if n else "bad")
    _back(h, csrf, repo, f"Dismissed {n} proposal(s). They will not be proposed again." if n else "Nothing to dismiss.", "ok" if n else "bad")


def apply_post(h, form, csrf: str) -> None:
    _decide(h, form, csrf, True)


def reject_post(h, form, csrf: str) -> None:
    _decide(h, form, csrf, False)
