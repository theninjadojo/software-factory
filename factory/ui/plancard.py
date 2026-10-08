"""The Priority card on a ticket's page: the priority a person pins (which the project manager never changes), what the project
manager thinks, and the ticket's milestone. See factory/plan.py."""
import logging
import sqlite3
import urllib.error

from .. import db as dbm
from .. import plan
from .. import pm
from .. import tracker
from . import labels as L
from .board import PIN_ICON, PRIO_WORD
from .views import ago, csrf_field, esc

log = logging.getLogger("factory.ui")
CHOICES = (("high", "High"), ("normal", "Normal"), ("low", "Low"), ("pm", "Let the PM decide"))


def _hidden(repo: str, n: int, csrf: str, back: str) -> str:
    return (f'{csrf_field(csrf)}<input type="hidden" name="repo" value="{esc(repo)}"><input type="hidden" name="n" value="{int(n)}">'
            f'<input type="hidden" name="back" value="{esc(back)}">')


def _refs(repo: str, nums) -> str:
    return ", ".join(tracker.display(int(x)) for x in nums)


def waits_for(db, repo: str, n: int, body: str = "") -> list[int]:
    """What the ticket waits for: the project manager's blockers and the `Blocked by #N` lines of its description."""
    try:
        inferred = dbm.pm_blocked_by(db, repo, n)
    except sqlite3.OperationalError:
        inferred = []
    return sorted((set(inferred) | set(pm.explicit_blockers(body))) - {n})


def card(cfg, db, repo: str, n: int, labels: list[str] | None, body: str, csrf: str, back: str) -> str:
    """labels: the ticket's labels when known (a local ticket's); body: its description, for `Blocked by` lines."""
    priority, source = plan.who_set(db, repo, n, labels)
    try:
        a = dbm.pm_assessment(db, repo, n)
    except sqlite3.OperationalError:
        a = None
    hidden = _hidden(repo, n, csrf, back)
    current = priority if source == "you" else "pm"
    buttons = "".join(f'<button name="priority" value="{v}" class="pc-opt{" on" if v == current else ""}" aria-pressed="{"true" if v == current else "false"}">{esc(w)}</button>'
                      for v, w in CHOICES)
    if source == "you":
        pinned = plan.pin(db, repo, n) or {}
        owner = (f'<div class="pc-owner mine">{PIN_ICON}<div><strong>Pinned by you · {esc(PRIO_WORD[priority])}</strong>'
                 '<p>The project manager will not change this. It still ranks the other tickets around it and records what this one waits for.</p>'
                 f'<p class="mono muted">Set {esc(ago(pinned.get("at")))}</p></div></div>')
    elif source == "pm":
        owner = (f'<div class="pc-owner"><div><strong>Set by the project manager · {esc(PRIO_WORD[priority])}</strong>'
                 f'<p>It may change on the next sweep (every {int(cfg.pm.interval_minutes)} minutes at most, when the tickets changed). '
                 'Pick a priority above to pin it.</p></div></div>')
    elif source == "label":
        owner = (f'<div class="pc-owner"><div><strong>{esc(PRIO_WORD[priority])}, from a priority label</strong>'
                 '<p>The project manager leaves it alone because a person changed the label. Pick a priority above to pin it here.</p></div></div>')
    else:
        owner = ('<div class="pc-owner"><div><strong>Normal · nobody set a priority yet</strong>'
                 '<p>' + ('The project manager sets one on its next sweep unless you pin one.' if cfg.pm.enabled
                          else 'The project manager is off, so only a priority you pin changes the order.') + '</p></div></div>')
    if a:
        waits = [x for x in dbm.pm_blocked_by(db, repo, n)]
        view = (f'<div class="pc-pm"><h4>The project manager\'s view</h4><p class="pc-tags"><span class="pr-chip {esc(a["priority"])}">Suggests {esc(PRIO_WORD.get(a["priority"], a["priority"]))}</span>'
                + (f'<span class="pr-chip wait">Waits for {esc(_refs(repo, waits))}</span>' if waits else "")
                + f'<span class="mono muted">assessed {esc(ago(a["assessed"]))}</span></p>'
                + (f'<p class="muted">“{esc(a["reason"])}”</p>' if a["reason"] else "") + "</div>")
    elif cfg.pm.enabled:
        view = '<div class="pc-pm"><h4>The project manager\'s view</h4><p class="muted">Not assessed yet. It looks at the open factory tickets on its next sweep.</p></div>'
    else:
        view = '<div class="pc-pm"><h4>The project manager\'s view</h4><p class="muted">The project manager is off. <a href="/settings">Turn it on in Settings</a> to have it rank tickets and find what each one waits for.</p></div>'
    names = plan.milestones(db, repo)
    mine = plan.ticket_milestones(db, repo).get(n, "")
    waits = waits_for(db, repo, n, body)
    road = f'/roadmap?repo={esc(repo)}&amp;focus={int(n)}'
    if names:
        opts = '<option value="">No milestone</option>' + "".join(f'<option value="{esc(m)}"{" selected" if m == mine else ""}>{esc(m)}</option>' for m in names)
        milestone = (f'<form method="post" action="/tickets/milestone" class="pc-ms">{hidden}<label>Milestone <select name="milestone">{opts}</select></label>'
                     '<button class="secondary">Save</button></form>')
    else:
        milestone = f'<p class="muted">No milestones yet. <a href="/roadmap?repo={esc(repo)}#milestones">Add them on the roadmap</a>.</p>'
    plan_line = ('Waits for ' + esc(_refs(repo, waits)) + '.' if waits else 'Waits for nothing.') + \
        f' To make it wait for another ticket yourself, add a line like <code>Blocked by {"L-2" if tracker.is_local(n) else "#12"}</code> to its description.'
    return (f'<section class="sd-card pc-card" aria-labelledby="pc-h"><div class="sd-cardhead"><h3 id="pc-h">Priority</h3>'
            f'<a href="{road}">See it on the roadmap</a></div>'
            f'<form method="post" action="/tickets/priority" class="pc-opts" aria-label="Priority">{hidden}{buttons}</form>'
            f'{owner}{view}<div class="pc-plan"><h4>Plan</h4>{milestone}<p class="muted">{plan_line}</p></div></section>')


# ---------------------------------------------------------------- actions
def _write(h):
    db = sqlite3.connect(h.app.cfg().db_path, timeout=10)
    plan.ensure_tables(db)
    return db


def priority_post(h, form, csrf: str) -> None:
    """Pin a priority (its label changes to match) or hand it back to the project manager. The labels change first: if that fails,
    nothing is pinned."""
    cfg, choice = h.app.cfg(), form.get("priority", "")
    try:
        repo, n = L._repo(cfg, form.get("repo", "")), L._number(form.get("n", ""))
        if choice not in (*plan.PRIORITIES, "pm"):
            raise L.Refused("Pick a priority.")
    except L.Refused as e:
        return L._done(h, form, csrf, str(e), "bad")
    gh = L._gh_for(h, cfg, n)
    if gh is None:
        return L._done(h, form, csrf, L.NO_TOKEN, "bad")
    try:
        names = [lb.get("name", "") for lb in gh.get_issue(repo, n).get("labels", [])]
        if choice != "pm":
            target = plan.LABEL_FOR[choice]
            for x in names:
                if x.strip().lower().startswith("priority:") and x.strip().lower() != target:
                    gh.remove_label(repo, n, x)
            if target and target not in {x.strip().lower() for x in names}:
                gh.add_labels(repo, n, [target])
    except (LookupError, urllib.error.URLError, OSError) as e:
        log.warning("priority: %s#%d could not be changed", repo, n)
        return L._done(h, form, csrf, L._github_error(e) if not isinstance(e, LookupError) else "There is no such ticket.", "bad")
    db = _write(h)
    try:
        if choice == "pm":
            plan.release(db, repo, n, names)
        else:
            plan.set_pin(db, repo, n, choice)
    finally:
        db.close()
    log.info("priority: %s %s#%d", choice, repo, n)
    L._done(h, form, csrf, "The project manager decides this ticket's priority again." if choice == "pm"
            else f"Pinned at {PRIO_WORD[choice]}. The project manager will not change it.")


def milestone_post(h, form, csrf: str) -> None:
    cfg = h.app.cfg()
    try:
        repo, n = L._repo(cfg, form.get("repo", "")), L._number(form.get("n", ""))
    except L.Refused as e:
        return L._done(h, form, csrf, str(e), "bad")
    name = plan.clean_name(form.get("milestone", ""))
    db = _write(h)
    try:
        ok = plan.set_ticket_milestone(db, repo, n, name)
    finally:
        db.close()
    L._done(h, form, csrf, (f"Moved to {name}." if name else "Taken out of its milestone.") if ok else "That milestone no longer exists.",
            "ok" if ok else "bad")

