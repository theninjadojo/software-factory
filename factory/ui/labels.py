"""The Labels page: add, remove and swap labels on issues, as the factory's GitHub account (read on the server, never sent to the browser).
Only labels that already exist in the repository are accepted: GitHub's add-labels call would otherwise create them."""
import logging
import re
import threading
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor

from .. import db as dbm
from .. import questions as Q
from ..github import GitHub
from ..roles import STAGE_TO_ROLE
from . import integrations as I, views
from .views import badge, csrf_field, esc

log = logging.getLogger("factory.ui")
NUM = re.compile(r"^\d{1,9}$")
STATES = ("open", "closed", "all")
FLASH = {"skipped": "Skipped. The factory will leave this ticket alone until it is labelled again.", "started": "Started. The factory picks it up at its next poll (usually within a minute).", "added": "Label added.", "removed": "Label removed.", "replaced": "Label replaced.",
         "answered": "Answer recorded on the ticket. Other questions still need an answer.",
         "continued": "Answers recorded on the ticket. The factory starts the next stage at its next poll."}
VERBS = {"analyst": "Analyze", "designer": "Design", "architect": "Architect"}
NEEDS_PERSON = "needs a person"
NO_TOKEN = "Save a GitHub token on the Credentials page first."


# After an action the browser goes back to the page the button was on, and the result is shown there once. The message is kept on
# the server per session (never taken from the URL), so a link cannot make the page say something.
_flash: dict = {}
_flash_lock = threading.Lock()
BACK = re.compile(r"^/(?:\?station=[a-z]{1,12}|tickets(?:\?[\w=&%.:/#+-]{0,300})?)?$")


def flash_set(csrf: str, msg: str, kind: str = "ok") -> None:
    with _flash_lock:
        if len(_flash) > 200:
            _flash.clear()
        _flash[csrf] = (msg, kind)


def flash_pop(csrf: str):
    with _flash_lock:
        return _flash.pop(csrf, None)


def _back(form) -> str:
    """Where to return to: only the Floor or the Tickets page, with their own query. Anything else goes to Tickets."""
    b = form.get("back", "")
    return b if BACK.fullmatch(b) and "//" not in b else "/tickets"


def _done(h, form, csrf: str, msg: str, kind: str = "ok") -> None:
    flash_set(csrf, msg, kind)
    h._redirect(_back(form))


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
    out[cfg.conflicts.label] = ("warn", "starts work")
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


# Every GitHub call costs about half a second, and the UI makes a new client per request, so what rarely changes is cached here
# and what is independent is fetched in parallel (a click used to take several seconds).
_logins: dict = {}          # token -> the account's login (never changes for a token)
_label_cache: dict = {}     # repo -> (time, [label names])
LABEL_TTL = 60


def _par(*calls):
    """Run independent GitHub reads at once; results come back in order and the first error is raised."""
    if len(calls) == 1:
        return [calls[0]()]
    with ThreadPoolExecutor(max_workers=min(8, len(calls))) as ex:
        futures = [ex.submit(c) for c in calls]
        return [f.result() for f in futures]


def _login(gh: GitHub) -> str:
    if gh.token not in _logins:
        _logins[gh.token] = gh.login()
    gh._login = _logins[gh.token]
    return gh._login


def label_names(gh: GitHub, repo: str, fresh: bool = False) -> list[str]:
    """The repository's label names, cached for a minute (labels rarely change; a miss is re-checked fresh before refusing)."""
    hit = _label_cache.get(repo)
    if hit and not fresh and time.time() - hit[0] < LABEL_TTL:
        return hit[1]
    names = sorted(l["name"] for l in gh.repo_labels(repo))
    _label_cache[repo] = (time.time(), names)
    return names


def _existing(gh: GitHub, repo: str, *names: str) -> None:
    for n in names:
        if not n or len(n) > 50:
            raise Refused(f"That label does not exist in {repo}.")
    if any(n not in label_names(gh, repo) for n in names):
        have = set(label_names(gh, repo, fresh=True))     # it may have been created in the last minute
        if any(n not in have for n in names):
            raise Refused(f"That label does not exist in {repo}.")


def actions_for(cfg, issue: dict, decision: dict | None = None, approved: bool = False) -> tuple[str, list[tuple[str, str, str | None]]]:
    """What can be started on a ticket right now: (status text, [(action, button text, label to apply)]).
    The mapping is fixed here and built from config; the browser only ever names an action, never a label."""
    names = set(_names(issue))
    if issue.get("state") != "open":
        return "closed", []
    if any(n == "factory:working" or n.startswith("factory:working-") for n in names):
        return "running", []
    if approved:                                  # a person's decision is already queued for the factory
        return "starting", []
    triggers = {cfg.trigger_label, cfg.auto_label, *(r.label for r in cfg.roles), *([cfg.review.label] if cfg.review.enabled else []),
                *([cfg.conflicts.label] if cfg.conflicts.enabled else [])}
    if names & triggers:
        if decision and decision.get("outcome") == "human":       # the factory asked a person (the Telegram Run / Build anyway / Skip prompt)
            return NEEDS_PERSON, human_actions(cfg, names, decision)
        return "queued", []
    acts = [("auto", "Auto", cfg.auto_label)]
    acts += [(r.name, VERBS.get(r.name, r.name.capitalize()), r.label) for r in cfg.roles if r.done_label not in names]
    acts.append(("build", "Build", cfg.trigger_label))
    if cfg.review.enabled and "factory:pr-open" in names:
        acts.append(("review", "Review", cfg.review.label))
    if cfg.conflicts.enabled and "factory:pr-open" in names:
        acts.append(("conflicts", "Resolve conflicts", cfg.conflicts.label))
    status = "failed" if "factory:failed" in names else "pr open" if "factory:pr-open" in names else ""
    return status, acts


def human_actions(cfg, names: set, decision: dict) -> list[tuple[str, str, str | None]]:
    """The same choices the Telegram prompt offers: the recommended stage first, then build, then skip (a None label)."""
    m = re.search(r"stage=(\w+)", decision.get("detail") or "")
    pick = next((r for r in cfg.roles if r.name == STAGE_TO_ROLE.get(m.group(1) if m else "") and r.done_label not in names), None)
    acts = [(f"stage:{pick.name}", f"Run {pick.name}", pick.label)] if pick else []
    return acts + [("build", "Build anyway" if pick else "Build", cfg.trigger_label), ("skip", "Skip", None)]


def approval_for(cfg, action: str) -> str | None:
    """A person's explicit decision to run a ticket now, as the orchestrator's approvals know it (the same words the Telegram
    buttons use): "run" builds, "stage:<role>" runs that stage. Neither asks the classifier again. Anything else: None."""
    if action == "build":
        return "run"
    name = action.removeprefix("stage:")
    return f"stage:{name}" if name in {r.name for r in cfg.roles} else None


def _approved(h) -> set:
    """{(repo, issue)} with a decision already queued for the factory."""
    db = h.app.ro_db()
    if db is None:
        return set()
    try:
        return {(r, n) for r, n, _ in dbm.approvals(db)}
    except Exception:
        return set()
    finally:
        db.close()


def _gh(h):
    token = I.read_secret(h.app.cfg(), "github")
    if not token:
        return None
    gh = GitHub(token)
    gh._login = _logins.get(token)          # already known: no /user call
    return gh


def _page(h, status: int, title: str, body: str, csrf: str, flash=None, kind="ok") -> None:
    h._send(status, views.page(title, body, "/tickets", csrf, flash=flash, flash_kind=kind, wide=True))


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
        def fetch():
            if text.lstrip("#").isdigit() and len(text.lstrip("#")) <= 9:
                issue = gh.get_issue(repo, int(text.lstrip("#")))
                return ([] if "pull_request" in issue else [issue]), False
            if text:
                return gh.search_issues(repo, text, state, page_no)
            return gh.issues(repo, state, label or None, page_no)

        names, (issues, more) = _par(lambda: label_names(gh, repo), fetch)
    except (urllib.error.URLError, OSError, ValueError) as e:
        return _page(h, 502, "Tickets", filters(cfg, repo, state, label, text, []), csrf, _github_error(e), "bad")
    if label and text:
        issues = [i for i in issues if label in _names(i)]
    decisions = _decisions(h, repo)
    from urllib.parse import urlencode
    back = "/tickets?" + urlencode({"repo": repo, "state": state, "label": label, "q": text, "page": page_no})
    if not BACK.fullmatch(back):
        back = "/tickets"
    shown = flash_pop(csrf) or (FLASH.get(q.get("ok", "")), "ok")
    _page(h, 200, "Tickets", filters(cfg, repo, state, label, text, names)
          + table(cfg, repo, issues, decisions, csrf, _asked(h, gh, cfg, repo, issues, decisions), _approved(h), back)
          + pager(repo, state, label, text, page_no, more), csrf, shown[0], shown[1])


def filters(cfg, repo, state, label, text, names) -> str:
    def opts(values, cur, blank=None):
        return "".join(f'<option value="{esc(v)}"{" selected" if v == cur else ""}>{esc(v or blank)}</option>' for v in values)

    return ('<p class="muted">Changes are made as the factory\'s GitHub account. Sorted: waiting for you first, then failed, ready, in progress and open PRs; newest first within each.</p>'
            f'<form method="get" class="filters"><select name="repo" aria-label="Repository">{opts(cfg.repos, repo)}</select>'
            f'<select name="state" aria-label="State">{opts(STATES, state)}</select>'
            f'<select name="label" aria-label="Label">{opts(["", *names], label, "any label")}</select>'
            f'<input name="q" placeholder="search title or #number" value="{esc(text)}"><button>Filter</button></form>')


def action_forms(repo: str, n: int, acts, csrf: str, back: str = "/tickets") -> str:
    """One POST form per available action; the first is the primary button. The browser only names an action."""
    hidden = (f'{csrf_field(csrf)}<input type="hidden" name="repo" value="{esc(repo)}"><input type="hidden" name="n" value="{int(n)}">'
              f'<input type="hidden" name="back" value="{esc(back)}">')
    return "".join(
        f'<form method="post" action="/tickets/start" class="inline">{hidden}'
        f'<button name="action" value="{esc(a)}" class="{"" if k == 0 else "secondary"}" title="{esc("Removes the trigger labels" if lab is None else "Applies " + lab)}" '
        f'aria-label="{esc(text)} #{int(n)}">{esc(text)}</button></form> ' for k, (a, text, lab) in enumerate(acts))


def action_forms_for(need: dict, csrf: str, back: str = "/") -> str:
    """The buttons for one row of the Floor's "Needs you" tray."""
    if need.get("questions"):
        hidden = f'{csrf_field(csrf)}<input type="hidden" name="repo" value="{esc(need["repo"])}"><input type="hidden" name="n" value="{int(need["issue"])}">'
        return (f'<form method="post" action="/tickets/answer" class="inline">{hidden}<button name="accept" value="1" '
                f'aria-label="Accept recommendations for #{int(need["issue"])}">Accept recommendations</button></form> ')
    return action_forms(need["repo"], need["issue"], need["acts"], csrf, back)


_needs_cache: dict = {"at": 0.0, "rows": None}
_needs_lock = threading.Lock()
NEEDS_TTL = 20      # seconds: the Floor refreshes every 5s, GitHub is asked at most this often


def needs_you(h) -> list | None:
    """The tickets waiting for a person, for the Floor: either the factory asked (the Telegram prompt), or a stage left questions.
    Read from GitHub (labels decide, as on the Tickets page) and cached briefly. None when there is no token or GitHub fails."""
    cfg = h.app.cfg()
    gh = _gh(h)
    if gh is None or not cfg.repos:
        return None
    with _needs_lock:
        if _needs_cache["rows"] is not None and time.time() - _needs_cache["at"] < NEEDS_TTL:
            return _needs_cache["rows"]
    db = h.app.ro_db()
    if db is None:
        return None
    try:
        decisions = {(t["repo"], t["issue"]): t for t in dbm.tickets(db, 500)}
        waiting = {repo: dbm.questions_waiting(db, repo) for repo in cfg.repos}
    finally:
        db.close()
    queued = _approved(h)
    rows = []
    try:
        for repo, (issues, _) in zip(cfg.repos, _par(*[(lambda r=r: gh.issues(r, "open", None, 1)) for r in cfg.repos])):
            for i in issues:
                d = decisions.get((repo, i["number"]))
                status, acts = actions_for(cfg, i, d, (repo, i["number"]) in queued)
                if status == NEEDS_PERSON:
                    rows.append({"repo": repo, "issue": i["number"], "title": i.get("title", "")[:120], "acts": acts, "at": (d or {}).get("decided_at", 0),
                                 "reason": ((d or {}).get("detail", "").rsplit(";", 1)[-1].strip() or "needs a person")[:80]})
                elif i["number"] in waiting.get(repo, ()) and status not in ("closed", "running", "queued"):
                    rows.append({"repo": repo, "issue": i["number"], "title": i.get("title", "")[:120], "acts": [], "questions": True,
                                 "at": (decisions.get((repo, i["number"])) or {}).get("decided_at", 0), "reason": "questions waiting for you"})
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
        log.warning("floor: could not read the tickets that need you")
        return None
    rows.sort(key=lambda r: r["at"], reverse=True)
    with _needs_lock:
        _needs_cache.update(at=time.time(), rows=rows)
    return rows


def _asked(h, gh, cfg, repo, issues, decisions) -> dict:
    """{issue number: StageQuestions} for tickets waiting on a person's answers. The database only says which tickets to look
    at; the questions and answers are read from the factory's own comments on GitHub (at most 10 tickets per page)."""
    db = h.app.ro_db()
    if db is None:
        return {}
    try:
        waiting = dbm.questions_waiting(db, repo)
    finally:
        db.close()
    todo = [i for i in issues if i["number"] in waiting
            and actions_for(cfg, i, decisions.get((repo, i["number"])))[0] not in ("closed", "running", "queued")][:10]
    if not todo:
        return {}
    try:
        me = _login(gh)
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
        return {}

    def one(i):
        try:
            return i["number"], Q.latest(Q.from_comments(gh.issue_comments(repo, i["number"]), me))
        except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
            return i["number"], None

    with ThreadPoolExecutor(max_workers=min(8, len(todo))) as ex:
        results = list(ex.map(one, todo))
    return {n: cur for n, cur in results if cur and cur.pending()}


def question_cards(repo: str, n: int, st, csrf: str, back: str = "/tickets") -> str:
    """One card per question: a button per option, a free-text 'other', and Accept all recommendations. The browser only
    names a question and an option id; both are checked against the ticket's questions on GitHub when the answer arrives."""
    hidden = (f'{csrf_field(csrf)}<input type="hidden" name="repo" value="{esc(repo)}"><input type="hidden" name="n" value="{int(n)}">'
              f'<input type="hidden" name="stage" value="{esc(st.stage)}"><input type="hidden" name="back" value="{esc(back)}">')
    cards = []
    for q in st.questions:
        ans = st.answers.get(q.id)
        cls = badge("safe default", "good") if q.safe else badge("needs a person", "warn") + (f' <span class="muted">{esc(q.why)}</span>' if q.why else "")
        qh = hidden + f'<input type="hidden" name="q" value="{esc(q.id)}">'
        buttons = "".join(f'<button name="o" value="{esc(o)}" class="{"" if o == q.recommended else "secondary"}" '
                          f'aria-label="{esc(q.id)}: {esc(lab)}">{esc(lab)}{" (recommended)" if o == q.recommended else ""}</button> '
                          for o, lab in q.options)
        cards.append(
            f'<div class="card question"><p><strong>{esc(q.id)}.</strong> {esc(q.text)} {cls}</p>'
            f'<p class="muted">Recommended: {esc(q.label(q.recommended))}: {esc(q.reason)}</p>'
            f'<p>{esc(Q.describe(q, ans))}</p>'
            f'<form method="post" action="/tickets/answer" class="inline">{qh}{buttons}</form>'
            f'<form method="post" action="/tickets/answer" class="filters">{qh}<input name="other" maxlength="{Q.MAX_OTHER}" required '
            f'placeholder="Other: your own answer" aria-label="Other answer to {esc(q.id)}"><button class="secondary">Answer</button></form></div>')
    accept = (f'<form method="post" action="/tickets/answer" class="inline">{hidden}<button name="accept" value="1" '
              f'title="Records the recommended option for every question not answered yet, then starts the next stage">Accept all recommendations</button></form>')
    return (f'<details><summary>{len(st.pending())} question(s) from the {esc(st.stage)} need an answer</summary>'
            f'<p class="muted">Answers are posted on the ticket as the factory\'s account. When no question needing a person is left, '
            f'the next stage starts.</p><div class="cards one">{"".join(cards)}</div><p>{accept}</p></details>')


def _epoch(iso) -> float:
    try:
        return time.mktime(time.strptime(str(iso), "%Y-%m-%dT%H:%M:%SZ")) - time.timezone
    except ValueError:
        return 0.0


def order(cfg, repo, issues, decisions, asked, approved) -> list:
    """Tickets worth acting on first: those waiting for a person, then failed ones, then ones ready for a next step, then work in
    progress, then open pull requests, then closed. Within each group the most recent activity (the ticket's last update or the
    factory's last decision) comes first."""
    def key(i):
        d = decisions.get((repo, i["number"]))
        status, _ = actions_for(cfg, i, d, (repo, i["number"]) in approved)
        rank = (0 if status == NEEDS_PERSON or i["number"] in asked else 1 if status == "failed" else 5 if status == "closed"
                else 3 if status in ("running", "queued", "starting") else 4 if status == "pr open" else 2)
        return rank, -max(_epoch(i.get("updated_at")), float((d or {}).get("decided_at") or 0))
    return sorted(issues, key=key)


def table(cfg, repo, issues, decisions, csrf, asked: dict | None = None, approved: frozenset | set = frozenset(), back: str = "/tickets") -> str:
    asked = asked or {}
    if not issues:
        return '<p class="muted">No issues match this filter.</p>'
    issues = order(cfg, repo, issues, decisions, asked, approved)

    def factory_cell(n) -> str:
        d = decisions.get((repo, n))
        if not d:
            return '<span class="muted">not seen</span>'
        run = f' · {d["runs"]} run{"s" if d["runs"] != 1 else ""}' + (f' ({esc(d["last_run"])})' if d["last_run"] else "") if d["runs"] else ""
        return f'{badge(d["outcome"])}{run}<br><span class="muted">{esc(views.ago(d["decided_at"]))}</span>'

    def buttons(i) -> str:
        status, acts = actions_for(cfg, i, decisions.get((repo, i["number"])), (repo, i["number"]) in approved)
        note = f'<span class="muted">{esc(status)}</span> ' if status else ""
        return note + action_forms(repo, i["number"], acts, csrf, back) + f'<a href="/labels/issue?repo={esc(repo)}&amp;n={int(i["number"])}">Labels</a>'

    def steps(i) -> str:
        names = set(_names(i))
        out = []
        for r in cfg.roles:
            cls = "done" if r.done_label in names else "now" if f"factory:working-{r.name}" in names else ""
            out.append(f'<span class="st {cls}">{esc(VERBS.get(r.name, r.name.capitalize()))}</span>')
        build = "done" if "factory:pr-open" in names else "now" if "factory:working" in names else ""
        out.append(f'<span class="st {build}">Build</span>')
        return f'<div class="steps">{"".join(out)}</div>'

    rows = "".join(
        f'<tr><td data-l="Issue">{views.ticket_link(repo, i["number"])}<br><span class="muted">{esc(i.get("title"))}</span></td><td data-l="State">{badge(i.get("state"), "")}</td>'
        f'<td data-l="Progress">{steps(i)}</td>'
        f'<td data-l="Labels">{" ".join(chip(n, cfg) for n in _names(i)) or "<span class=muted>none</span>"}</td><td data-l="Factory">{factory_cell(i["number"])}</td>'
        f'<td class="actions" data-l="Start">{buttons(i)}</td></tr>'
        + (f'<tr class="questions"><td colspan="6">{question_cards(repo, i["number"], asked[i["number"]], csrf, back)}</td></tr>' if i["number"] in asked else "")
        for i in issues)
    return ('<div class="scroll"><table class="tickets"><thead><tr><th>Issue</th><th>State</th><th>Progress</th><th>Labels</th><th>Factory</th><th>Start</th></tr></thead>'
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
        issue, names = _par(lambda: gh.get_issue(repo, n), lambda: label_names(gh, repo))
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
    """One-click start for a named action: Auto and Review apply their trigger label; Build and the stages queue a person's
    approval (the same one the Telegram buttons write), because a label would send the ticket back through the classifier. The ticket is re-read first, so a stale page cannot
    start something that is already running or queued, and the action must be one actions_for() offers right now."""
    cfg = h.app.cfg()
    action = form.get("action", "")
    try:
        repo, n = _repo(cfg, form.get("repo", "")), _number(form.get("n", ""))
    except Refused as e:
        return _page(h, 400, "Tickets", "", csrf, str(e), "bad")
    gh = _gh(h)
    if gh is None:
        return _done(h, form, csrf, NO_TOKEN, "bad")
    try:
        issue = gh.get_issue(repo, n)
        if "pull_request" in issue:
            return h._send(404, "no such issue", "text/plain")
        status, acts = actions_for(cfg, issue, _decisions(h, repo).get((repo, n)), (repo, n) in _approved(h))
        found = next(((a, lab) for a, _, lab in acts if a == action), None)
        if found is None:
            raise Refused(f"That cannot be started now ({status or 'not available'}).")
        chosen, decision = found[1], approval_for(cfg, action)
        if decision:           # a person's explicit decision: queue it for the factory, which runs it without asking the classifier again
            h.app.add_approval(repo, n, decision)
        else:
            if chosen:
                _existing(gh, repo, chosen)
                gh.add_labels(repo, n, [chosen])             # add first: if it fails nothing has changed
            if status == NEEDS_PERSON:                       # skipping covers the whole ticket: clear every trigger label
                held = set(_names(issue))
                for lab in (cfg.trigger_label, cfg.auto_label, *(r.label for r in cfg.roles), *([cfg.review.label] if cfg.review.enabled else []),
                            *([cfg.conflicts.label] if cfg.conflicts.enabled else [])):
                    if lab in held and lab != chosen:
                        gh.remove_label(repo, n, lab)
        with _needs_lock:
            _needs_cache["rows"] = None                      # the Floor's tray must not show it again
    except Refused as e:
        return _done(h, form, csrf, str(e), "bad")
    except (urllib.error.URLError, OSError) as e:
        log.warning("tickets: start %s on %s#%d failed", action, repo, n)
        return _done(h, form, csrf, _github_error(e), "bad")
    log.info("tickets: %s on %s#%d from the UI", "approved " + action if approval_for(cfg, action) else "started " + action, repo, n)
    _done(h, form, csrf, FLASH["skipped" if action == "skip" else "started"])


def answer(h, form, csrf: str) -> None:
    """A person's answer to an open question, or Accept all recommendations. Posted as the factory's account in the marked
    answers comment, which the factory then reads as a person's input (never as an instruction). The question and option ids
    are validated against the questions the factory posted on the ticket; when nothing needing a person is left, the auto
    label starts the next stage through the factory's normal gates."""
    cfg = h.app.cfg()
    try:
        repo, n = _repo(cfg, form.get("repo", "")), _number(form.get("n", ""))
    except Refused as e:
        return _page(h, 400, "Tickets", "", csrf, str(e), "bad")
    stage = form.get("stage", "")
    accept_all = form.get("accept") == "1"
    other = (form.get("other") or "").strip()
    picks = None if accept_all else {form.get("q", ""): ("other", other) if other else ("option", form.get("o", ""))}
    gh = _gh(h)
    if gh is None:
        return _done(h, form, csrf, NO_TOKEN, "bad")
    try:
        if stage or not accept_all:      # accepting every recommendation needs no stage: the latest stage's questions are used
            if not re.fullmatch(r"[a-z]{1,20}", stage):
                raise Refused("That answer does not match the ticket's questions. Reload the page.")
        issue = gh.get_issue(repo, n)
        if "pull_request" in issue:
            return h._send(404, "no such issue", "text/plain")
        status, _ = actions_for(cfg, issue, _decisions(h, repo).get((repo, n)))
        if status in ("closed", "running"):
            raise Refused(f"Answers cannot be recorded now ({status}).")
        if status != "queued":
            _existing(gh, repo, cfg.auto_label)               # checked before anything is posted
        _, done = Q.record(gh, repo, n, stage or None, picks, "factory UI", accept_all)
        if done and status != "queued":
            gh.add_labels(repo, n, [cfg.auto_label])
    except (Q.Refused, Refused) as e:
        return _done(h, form, csrf, str(e), "bad")
    except (urllib.error.URLError, OSError) as e:
        log.warning("tickets: answer on %s#%d failed", repo, n)
        return _done(h, form, csrf, _github_error(e), "bad")
    log.info("tickets: answers recorded on %s#%d from the UI", repo, n)
    _done(h, form, csrf, FLASH["continued" if done else "answered"])
