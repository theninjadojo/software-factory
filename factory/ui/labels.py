"""The Labels page: add, remove and swap labels on issues, as the factory's GitHub account (read on the server, never sent to the browser).
Only labels that already exist in the repository are accepted: GitHub's add-labels call would otherwise create them."""
import logging
import re
import urllib.error

from .. import db as dbm
from ..github import GitHub
from . import integrations as I, views
from .views import badge, csrf_field, esc

log = logging.getLogger("factory.ui")
NUM = re.compile(r"^\d{1,9}$")
STATES = ("open", "closed", "all")
FLASH = {"started": "Started. The factory picks it up at its next poll (usually within a minute).", "added": "Label added.", "removed": "Label removed.", "replaced": "Label replaced."}
VERBS = {"analyst": "Analyze", "designer": "Design", "architect": "Architect"}
NO_TOKEN = "Save a GitHub token on the Credentials page first."


class Refused(Exception):
    """A request we reject with a message safe to show."""


def _github_error(e: Exception) -> str:
    """Fixed messages only: exception text could carry URLs or tokens."""
    if isinstance(e, urllib.error.HTTPError):
        if e.code == 429 or (e.code == 403 and e.headers.get("X-RateLimit-Remaining") == "0"):
            return "GitHub rate limit reached. Try again in a few minutes."
        if e.code in (401, 403):
            return f"GitHub refused the change ({e.code}). Check the GitHub token under Credentials."
        if e.code == 404:
            return "The issue was not found or is not in a configured repository."
        return f"GitHub rejected the request ({e.code})."
    return "GitHub did not answer."


def factory_labels(cfg) -> dict[str, tuple[str, str]]:
    """name -> (css kind, hint) for the labels the factory acts on or sets itself."""
    out = {cfg.trigger_label: ("warn", "starts work"), cfg.auto_label: ("warn", "starts work"), cfg.review.label: ("warn", "starts work")}
    for r in cfg.roles:
        out[r.label] = ("warn", "starts work")
        out[r.done_label] = ("good", "stage done")
    out[cfg.review.done_label] = ("good", "stage done")
    for name in ("factory:working", "factory:pr-open", "factory:failed"):
        out[name] = ("", "managed by the factory")
    return out


def chip(name: str, cfg) -> str:
    kind, hint = factory_labels(cfg).get(name, ("", ""))
    return f'<span class="badge {kind}" title="{esc(hint)}">{esc(name)}</span>' if hint else badge(name, "")


def _names(issue: dict) -> list[str]:
    return [l["name"] if isinstance(l, dict) else str(l) for l in issue.get("labels", [])]


def _repo(cfg, value: str) -> str:
    if value not in cfg.repos:
        raise Refused("That repository is not configured.")
    return value


def _number(value: str) -> int:
    if not NUM.match(value or ""):
        raise Refused("That is not a valid issue number.")
    return int(value)


def _existing(gh: GitHub, repo: str, *names: str) -> None:
    have = {l["name"] for l in gh.repo_labels(repo)}
    for n in names:
        if not n or len(n) > 50 or n not in have:
            raise Refused(f"That label does not exist in {repo}.")


def actions_for(cfg, issue: dict) -> tuple[str, list[tuple[str, str, str]]]:
    """What can be started on a ticket right now: (status text, [(action, button text, label to apply)]).
    The mapping is fixed here and built from config; the browser only ever names an action, never a label."""
    names = set(_names(issue))
    if issue.get("state") != "open":
        return "closed", []
    if any(n == "factory:working" or n.startswith("factory:working-") for n in names):
        return "running", []
    triggers = {cfg.trigger_label, cfg.auto_label, *(r.label for r in cfg.roles), *([cfg.review.label] if cfg.review.enabled else [])}
    if names & triggers:
        return "queued", []
    acts = [("auto", "Auto", cfg.auto_label)]
    acts += [(r.name, VERBS.get(r.name, r.name.capitalize()), r.label) for r in cfg.roles if r.done_label not in names]
    acts.append(("build", "Build", cfg.trigger_label))
    if cfg.review.enabled and "factory:pr-open" in names:
        acts.append(("review", "Review", cfg.review.label))
    status = "failed" if "factory:failed" in names else "pr open" if "factory:pr-open" in names else ""
    return status, acts


def _gh(h):
    token = I.read_secret(h.app.cfg(), "github")
    return GitHub(token) if token else None


def _page(h, status: int, title: str, body: str, csrf: str, flash=None, kind="ok") -> None:
    h._send(status, views.page(title, body, "/tickets", csrf, flash=flash, flash_kind=kind))


# ------------------------------------------------------------------ pages
def _decisions(h, repo) -> dict | list:
    """The factory's latest decision per ticket: a list (repo=None) or a {(repo, issue): row} map for one repo."""
    db = h.app.ro_db()
    if db is None:
        return [] if repo is None else {}
    try:
        rows = dbm.tickets(db, 500)
    finally:
        db.close()
    return rows if repo is None else {(t["repo"], t["issue"]): t for t in rows if t["repo"] == repo}


def list_get(h, q: dict, csrf: str) -> None:
    cfg = h.app.cfg()
    if not cfg.repos:
        return _page(h, 200, "Tickets", '<p class="muted">No repositories are configured. Add one in <a href="/settings?section=projects">Settings</a>.</p>', csrf)
    repo = q.get("repo") if q.get("repo") in cfg.repos else cfg.repos[0]
    state = q.get("state") if q.get("state") in STATES else "open"
    label, text = (q.get("label") or "")[:50], (q.get("q") or "").strip()[:100]
    page_no = int(q["page"]) if (q.get("page") or "").isdigit() and 1 <= int(q["page"]) <= 1000 else 1
    gh = _gh(h)
    if gh is None:
        return _page(h, 200, "Tickets", views.tickets_page(_decisions(h, None)), csrf, NO_TOKEN + " Showing the factory's decisions only.", "bad")
    try:
        names = sorted(l["name"] for l in gh.repo_labels(repo))
        if text.lstrip("#").isdigit() and len(text.lstrip("#")) <= 9:
            issue = gh.get_issue(repo, int(text.lstrip("#")))
            issues, more = ([] if "pull_request" in issue else [issue]), False
        elif text:
            issues, more = gh.search_issues(repo, text, state, page_no)
        else:
            issues, more = gh.issues(repo, state, label or None, page_no)
    except (urllib.error.URLError, OSError, ValueError) as e:
        return _page(h, 502, "Tickets", filters(cfg, repo, state, label, text, []), csrf, _github_error(e), "bad")
    if label and text:
        issues = [i for i in issues if label in _names(i)]
    _page(h, 200, "Tickets", filters(cfg, repo, state, label, text, names) + table(cfg, repo, issues, _decisions(h, repo), csrf) + pager(repo, state, label, text, page_no, more), csrf, FLASH.get(q.get("ok", "")))


def filters(cfg, repo, state, label, text, names) -> str:
    def opts(values, cur, blank=None):
        return "".join(f'<option value="{esc(v)}"{" selected" if v == cur else ""}>{esc(v or blank)}</option>' for v in values)

    return ('<p class="muted">Changes are made as the factory\'s GitHub account. Trigger labels start work at the next poll.</p>'
            f'<form method="get" class="filters"><select name="repo" aria-label="Repository">{opts(cfg.repos, repo)}</select>'
            f'<select name="state" aria-label="State">{opts(STATES, state)}</select>'
            f'<select name="label" aria-label="Label">{opts(["", *names], label, "any label")}</select>'
            f'<input name="q" placeholder="search title or #number" value="{esc(text)}"><button>Filter</button></form>')


def table(cfg, repo, issues, decisions, csrf) -> str:
    if not issues:
        return '<p class="muted">No issues match this filter.</p>'
    def factory_cell(n) -> str:
        d = decisions.get((repo, n))
        if not d:
            return '<span class="muted">not seen</span>'
        run = f' · {d["runs"]} run{"s" if d["runs"] != 1 else ""}' + (f' ({esc(d["last_run"])})' if d["last_run"] else "") if d["runs"] else ""
        return f'{badge(d["outcome"])}{run}<br><span class="muted">{esc(views.ago(d["decided_at"]))}</span>'

    def buttons(i) -> str:
        status, acts = actions_for(cfg, i)
        hidden = f'{csrf_field(csrf)}<input type="hidden" name="repo" value="{esc(repo)}"><input type="hidden" name="n" value="{int(i["number"])}">'
        out = "".join(
            f'<form method="post" action="/tickets/start" class="inline">{hidden}'
            f'<button name="action" value="{esc(a)}" class="{"" if a == "auto" else "secondary"}" title="Applies {esc(lab)}" '
            f'aria-label="{esc(text)} #{int(i["number"])}">{esc(text)}</button></form> ' for a, text, lab in acts)
        note = f'<span class="muted">{esc(status)}</span> ' if status else ""
        return note + out + f'<a href="/labels/issue?repo={esc(repo)}&amp;n={int(i["number"])}">Labels</a>'

    rows = "".join(
        f'<tr><td>{views.ticket_link(repo, i["number"])}<br><span class="muted">{esc(i.get("title"))}</span></td><td>{badge(i.get("state"), "")}</td>'
        f'<td>{" ".join(chip(n, cfg) for n in _names(i)) or "<span class=muted>none</span>"}</td><td>{factory_cell(i["number"])}</td>'
        f'<td class="actions">{buttons(i)}</td></tr>' for i in issues)
    return ('<div class="scroll"><table><thead><tr><th>Issue</th><th>State</th><th>Labels</th><th>Factory</th><th>Start</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>')


def pager(repo, state, label, text, page_no, more) -> str:
    from urllib.parse import urlencode

    def link(text_, p):
        return f'<a href="/tickets?{esc(urlencode({"repo": repo, "state": state, "label": label, "q": text, "page": p}))}">{text_}</a>'

    return '<p class="pager">' + (link("← newer", page_no - 1) if page_no > 1 else "") + " " + (link("older →", page_no + 1) if more else "") + "</p>"


def _edit_body(cfg, repo: str, n: int, issue: dict, all_labels: list[str], csrf: str) -> str:
    have = _names(issue)
    hidden = f'{csrf_field(csrf)}<input type="hidden" name="repo" value="{esc(repo)}"><input type="hidden" name="n" value="{n}">'
    ref = f"{repo}#{n}"
    chips = "".join(
        f'<li>{chip(name, cfg)} <form method="post" action="/labels/remove" class="inline">{hidden}<input type="hidden" name="label" value="{esc(name)}">'
        f'<button aria-label="Remove label {esc(name)} from {esc(ref)}">Remove</button></form></li>' for name in have)
    opt = lambda names: "".join(f'<option value="{esc(x)}">{esc(x)}</option>' for x in names)
    addable = [x for x in all_labels if x not in have]
    return (f'<p>{views.ticket_link(repo, n)} — {esc(issue.get("title"))} {badge(issue.get("state"), "")}</p>'
            '<p class="muted">Changes are made as the factory\'s GitHub account. Trigger labels start work at the next poll.</p>'
            f'<div class="labels"><h2>Current labels</h2>'
            f'{f"<ul class=chip-row aria-label=Labels>{chips}</ul>" if have else "<p class=muted>This issue has no labels.</p>"}'
            f'<h2>Add a label</h2><form method="post" action="/labels/add" class="filters">{hidden}'
            f'<select name="label" aria-label="Label to add" required><option value="">choose an existing label</option>{opt(addable)}</select><button>Add</button></form>'
            + (f'<h2>Replace a label</h2><form method="post" action="/labels/replace" class="filters">{hidden}Replace '
               f'<select name="old" aria-label="Label to replace" required>{opt(have)}</select> with '
               f'<select name="new" aria-label="New label" required><option value="">choose an existing label</option>{opt(addable)}</select><button>Replace</button></form>'
               if have else "") + '</div><p><a href="/tickets">← all tickets</a></p>')


def _render_issue(h, csrf: str, repo: str, n: int, flash=None, kind="ok", status: int = 200) -> None:
    cfg, gh = h.app.cfg(), _gh(h)
    if gh is None:
        return _page(h, 400, "Tickets", "", csrf, NO_TOKEN, "bad")
    try:
        issue = gh.get_issue(repo, n)
        names = sorted(l["name"] for l in gh.repo_labels(repo))
    except (urllib.error.URLError, OSError, ValueError) as e:
        return _page(h, 404 if isinstance(e, urllib.error.HTTPError) and e.code == 404 else 502, "Tickets", "", csrf, _github_error(e), "bad")
    if "pull_request" in issue:
        return h._send(404, "no such issue", "text/plain")
    _page(h, status, f"Edit labels · {repo}#{n}", _edit_body(cfg, repo, n, issue, names, csrf), csrf, flash, kind)


def issue_get(h, q: dict, csrf: str) -> None:
    cfg = h.app.cfg()
    try:
        repo, n = _repo(cfg, q.get("repo", "")), _number(q.get("n", ""))
    except Refused:
        return h._send(404, "no such issue", "text/plain")
    _render_issue(h, csrf, repo, n, FLASH.get(q.get("ok", "")))


# ------------------------------------------------------------------ actions
def _act(h, form, csrf: str, action: str, steps) -> None:
    """Validate, run `steps(gh, repo, n)` against GitHub, then redirect to the re-fetched issue. Nothing reaches GitHub before validation."""
    cfg = h.app.cfg()
    try:
        repo, n = _repo(cfg, form.get("repo", "")), _number(form.get("n", ""))
    except Refused as e:
        return _page(h, 400, "Tickets", "", csrf, str(e), "bad")
    gh = _gh(h)
    if gh is None:
        return _page(h, 400, "Tickets", "", csrf, NO_TOKEN, "bad")
    try:
        steps(gh, repo, n)
    except Refused as e:
        return _render_issue(h, csrf, repo, n, str(e), "bad", 400)
    except (urllib.error.URLError, OSError) as e:
        log.warning("labels: %s %s#%d failed", action, repo, n)
        return _render_issue(h, csrf, repo, n, _github_error(e), "bad", 502)
    h._redirect(f"/labels/issue?repo={repo}&n={n}&ok={action}")


def add(h, form, csrf: str) -> None:
    label = form.get("label", "")

    def steps(gh, repo, n):
        _existing(gh, repo, label)
        gh.add_labels(repo, n, [label])
        log.info("labels: added %r to %s#%d", label, repo, n)

    _act(h, form, csrf, "added", steps)


def remove(h, form, csrf: str) -> None:
    label = form.get("label", "")

    def steps(gh, repo, n):
        if not label or len(label) > 50:
            raise Refused("That label is not valid.")
        gh.remove_label(repo, n, label)
        log.info("labels: removed %r from %s#%d", label, repo, n)

    _act(h, form, csrf, "removed", steps)


def replace(h, form, csrf: str) -> None:
    old, new = form.get("old", ""), form.get("new", "")

    def steps(gh, repo, n):
        if not old or old == new:
            raise Refused("Pick two different labels.")
        _existing(gh, repo, new)
        gh.add_labels(repo, n, [new])                    # add first: if it fails nothing changed
        try:
            gh.remove_label(repo, n, old)
        except (urllib.error.URLError, OSError):
            raise Refused(f"“{new}” was added but “{old}” could not be removed, so both are on the issue.")
        log.info("labels: replaced %r with %r on %s#%d", old, new, repo, n)

    _act(h, form, csrf, "replaced", steps)


def start(h, form, csrf: str) -> None:
    """One-click start: apply the trigger label for a named action. The ticket is re-read first, so a stale page cannot
    start something that is already running or queued, and the action must be one actions_for() offers right now."""
    cfg = h.app.cfg()
    action = form.get("action", "")
    try:
        repo, n = _repo(cfg, form.get("repo", "")), _number(form.get("n", ""))
    except Refused as e:
        return _page(h, 400, "Tickets", "", csrf, str(e), "bad")
    gh = _gh(h)
    if gh is None:
        return _page(h, 400, "Tickets", "", csrf, NO_TOKEN, "bad")
    try:
        issue = gh.get_issue(repo, n)
        if "pull_request" in issue:
            return h._send(404, "no such issue", "text/plain")
        status, acts = actions_for(cfg, issue)
        chosen = next((lab for a, _, lab in acts if a == action), None)
        if chosen is None:
            raise Refused(f"That cannot be started now ({status or 'not available'}).")
        _existing(gh, repo, chosen)
        gh.add_labels(repo, n, [chosen])
    except Refused as e:
        return _render_issue(h, csrf, repo, n, str(e), "bad", 400)
    except (urllib.error.URLError, OSError) as e:
        log.warning("tickets: start %s on %s#%d failed", action, repo, n)
        return _render_issue(h, csrf, repo, n, _github_error(e), "bad", 502)
    log.info("tickets: started %s on %s#%d from the UI", action, repo, n)
    h._redirect(f"/tickets?repo={repo}&ok=started")
