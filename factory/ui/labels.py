"""The Labels page: add, remove and swap labels on issues, as the factory's GitHub account (read on the server, never sent to the browser).
Only labels that already exist in the repository are accepted: GitHub's add-labels call would otherwise create them."""
import logging
import re
import threading
import time
import urllib.error
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor

from .. import db as dbm
from .. import questions as Q
from .. import tracker
from .. import why as W
from ..github import GitHub
from ..roles import STAGE_TO_ROLE
from . import integrations as I, views
from .views import badge, csrf_field, esc

log = logging.getLogger("factory.ui")
NUM = re.compile(r"^\d{1,9}$")
STATES = ("open", "closed", "all")
STAGE_FILTERS = ("progress", "needs", "prs", "done")
FLASH = {"skipped": "Skipped. The factory will leave this ticket alone until it is labelled again.", "started": "Started. The factory picks it up at its next poll (usually within a minute).", "added": "Label added.", "removed": "Label removed.", "replaced": "Label replaced.",
         "answered": "Answer recorded on the ticket. Other questions still need an answer.",
         "continued": "Answers recorded on the ticket. The factory starts the next stage at its next poll."}
MAX_TITLE, MAX_BODY = 200, 5000
DUP_SECONDS = 10
CLOSE_COMMENT = "Closed from the factory admin UI. It can be reopened if this was a mistake."
CLOSE_BUSY_COMMENT = "Closed from the factory admin UI. The factory stops its work on this ticket. It can be reopened if this was a mistake."
VERBS = {"analyst": "Analyze", "designer": "Design", "architect": "Architect"}
NEEDS_PERSON = "needs a person"
BUSY = ("running", "queued", "starting", NEEDS_PERSON)     # statuses in which closing a ticket also stops the factory's work on it
NO_TOKEN = "Save a GitHub token on the Credentials page first."


# After an action the browser goes back to the page the button was on, and the result is shown there once. The message is kept on
# the server per session (never taken from the URL), so a link cannot make the page say something.
_flash: dict = {}
_flash_lock = threading.Lock()
BACK = re.compile(r"^/(?:\?(?:station|need|view)=[a-z]{1,12}(?:&(?:station|need|view)=[a-z]{1,12}){0,2}|needs(?:\?need=[a-z]{1,12})?|tickets(?:\?[\w=&%.:/#+-]{0,300})?|ticket\?repo=[\w.%-]{1,150}&n=\d{1,9})?$")


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
    cfg = h.app.cfg()
    gh = tracker.Hub(token, cfg.db_path, actor=tracker.UI_ACTOR) if cfg.local_enabled else GitHub(token)
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
    stage = q.get("stage") if q.get("stage") in STAGE_FILTERS else ""
    if stage == "prs":                      # the pull requests and checks the factory is watching, across every repository (this was the PRs & CI page)
        db = h.app.ro_db()
        try:
            prs = [p for p in dbm.watched_prs(db, 300) if p.get("status") != "closed"] if db is not None else []
        finally:
            if db is not None:
                db.close()
        return _page(h, 200, "Tickets", filters(cfg, repo, state, label, text, [], csrf, stage) + views.prs_summary_line(prs)
                     + views.prs_cards(prs, cfg.ci.fix_rounds), csrf)
    gh = _gh(h)
    if gh is None:
        return _page(h, 200, "Tickets", views.tickets_page(_decisions(h, None)), csrf, NO_TOKEN + " Showing the factory's decisions only.", "bad")
    # The chips filter on GitHub (a search over every page), not on the page that happens to be loaded. The label lists are built here from config.
    any_of = tuple(sorted({f"factory:working-{r.name}" for r in cfg.roles} | {"factory:working"} if stage == "progress"
                          else {cfg.trigger_label, cfg.auto_label, *(r.label for r in cfg.roles)} if stage == "needs" else set()))
    want_state = "closed" if stage == "done" else state
    try:
        def fetch():
            if text.lstrip("#").isdigit() and len(text.lstrip("#")) <= 9:
                issue = gh.get_issue(repo, int(text.lstrip("#")))
                one = [] if "pull_request" in issue else [issue]
                return one, False, len(one)
            if text or any_of or stage == "done":
                return gh.search_page(repo, text, want_state, page_no, labels=any_of, label=label)
            issues, more = gh.issues(repo, want_state, label or None, page_no)
            return issues, more, gh.count_issues(repo, want_state, label)

        names, (issues, more, total) = _par(lambda: label_names(gh, repo), fetch)
    except (urllib.error.URLError, OSError, ValueError) as e:
        return _page(h, 502, "Tickets", filters(cfg, repo, state, label, text, [], "", stage), csrf, _github_error(e), "bad")
    if label and text.lstrip("#").isdigit():
        issues = [i for i in issues if label in _names(i)]
    if cfg.local_enabled:                   # moved issues live in the local tracker now
        issues = [i for i in issues if tracker.MOVED_LABEL not in _names(i)]
    decisions = _decisions(h, repo)
    asked = _asked(h, gh, cfg, repo, issues, decisions)
    approved = _approved(h)
    if stage == "needs":                 # a trigger label alone is not a question for a person: keep what the factory actually asked about
        issues = [i for i in issues if i["number"] in asked or actions_for(cfg, i, decisions.get((repo, i["number"])), (repo, i["number"]) in approved)[0] == NEEDS_PERSON]
        total = len(issues) if not more else total
    from urllib.parse import urlencode
    back = "/tickets?" + urlencode({"repo": repo, "state": state, "label": label, "q": text, "page": page_no, **({"stage": stage} if stage else {})})
    if not BACK.fullmatch(back):
        back = "/tickets"
    shown = flash_pop(csrf) or (FLASH.get(q.get("ok", "")), "ok")
    _page(h, 200, "Tickets", filters(cfg, repo, state, label, text, names, csrf, stage)
          + table(cfg, repo, issues, decisions, csrf, asked, approved, back)
          + (footer(len(issues), total) if issues else "") + pager(repo, state, label, text, page_no, more, stage), csrf, shown[0], shown[1])


def filters(cfg, repo, state, label, text, names, csrf: str = "", stage: str = "") -> str:
    from urllib.parse import urlencode
    def opts(values, cur, blank=None):
        return "".join(f'<option value="{esc(v)}"{" selected" if v == cur else ""}>{esc(v or blank)}</option>' for v in values)

    chip = lambda name, key: (f'<a href="/tickets?{esc(urlencode({"repo": repo, "state": state, "label": label, "q": text, **({"stage": key} if key else {})}))}"'
                              f'{" aria-current=page" if key == stage else ""}>{name}</a>')
    chips = "".join(chip(n, k) for n, k in (("All", ""), ("Needs you", "needs"), ("In progress", "progress"), ("PRs &amp; CI", "prs"), ("Done", "done")))
    return (new_ticket_form(cfg, repo, csrf) + import_form(cfg, repo, csrf) + '<p class="muted">Changes are made as the factory\'s GitHub account. Sorted: waiting for you first, then failed, ready, in progress and open PRs; newest first within each.</p>'
            f'<form method="get" class="filters"><select name="repo" aria-label="Repository">{opts(cfg.repos, repo)}</select>'
            f'<select name="state" aria-label="State">{opts(STATES, state)}</select>'
            f'<select name="label" aria-label="Label">{opts(["", *names], label, "any label")}</select>'
            f'<input type="search" name="q" aria-label="Search title or #number" placeholder="search title or #number" value="{esc(text)}">'
            f'{f"<input type=hidden name=stage value={esc(stage)}>" if stage else ""}<button>Filter</button></form>'
            f'<nav class="nd-seg tk-seg" aria-label="Stage filter">{chips}</nav>')


def start_options(cfg) -> list[tuple[str, str, str | None]]:
    """What a new ticket may start with: (key, text, label). The first is the default and carries no label. Only Auto and the
    read-only stages are offered (never Build). This list is also the allow-list `create` checks the browser's choice against."""
    return ([("", "Just open the ticket", None), ("auto", "Auto", cfg.auto_label)]
            + [(r.name, VERBS.get(r.name, r.name.capitalize()), r.label) for r in cfg.roles])


def start_field(roles: list[str]) -> str:
    """The Start select and its help text, shared by the Tickets form and the floor's Add a ticket (given the role names, in the
    order start_options lists them; `create` still checks the choice against the configuration)."""
    keys = [("", "Just open the ticket"), ("auto", "Auto")] + [(r, VERBS.get(r, r.capitalize())) for r in roles]
    opts = "".join(f'<option value="{esc(k)}">{esc(t)}</option>' for k, t in keys)
    return (f'<label>Start<select name="start" aria-describedby="start-help">{opts}</select></label>'
            '<p class="muted" id="start-help">Choose a Start action to begin work right away. Leave it on "Just open the ticket" '
            'to start it later from its row.</p>')


def import_form(cfg, repo: str, csrf: str) -> str:
    """Move GitHub issues into the local tracker: one issue by number, or every open issue with a label (at most MAX_BULK)."""
    if not csrf or not cfg.repos or not cfg.local_enabled:
        return ""
    opts = "".join(f'<option value="{esc(r)}"{" selected" if r == repo else ""}>{esc(r)}</option>' for r in cfg.repos)
    return ('<details class="disclose"><summary class="btn">Import</summary>'
            f'<form method="post" action="/tickets/import" class="field">{csrf_field(csrf)}'
            f'<label>Repository<select name="repo">{opts}</select></label>'
            '<label>Issue number<input name="n" inputmode="numeric" maxlength="9"></label>'
            f'<label>Or every open issue with the label<input name="label" maxlength="50"></label>'
            '<label><input type="checkbox" name="all" value="1"> Or every open issue (not pull requests)</label>'
            '<label><input type="checkbox" name="close" value="1"> Also close the issue on GitHub</label>'
            '<label><input type="checkbox" name="confirm" value="1"> Confirm closing every issue imported with &ldquo;every open issue&rdquo;</label>'
            f'<p class="muted">Copies the issue and its comments to a local ticket, then removes its trigger labels on GitHub, labels it <code>{tracker.MOVED_LABEL}</code> and links to the local ticket. At most {tracker.MAX_BULK} issues at a time; submit again for the rest.</p>'
            '<button>Import</button></form></details>')


def ticket_dialog(dom_id: str, label: str, sub: str, form: str) -> str:
    """A trigger button and the form in a popup (as Answer… does); without JavaScript the form is shown in the page."""
    return (f'<button type="button" class="btn" data-dialog="{dom_id}">{esc(label)}</button>'
            f'<noscript><details class="disclose" open><summary>{esc(label)}</summary>{form}</details></noscript>'
            f'<dialog id="{dom_id}" class="nd-dialog" data-backdrop aria-label="{esc(label)}"><div class="nd-dhead"><div><strong>{esc(label)}</strong>'
            f'<p class="muted">{esc(sub)}</p></div><button type="button" class="secondary" data-close aria-label="Close">Close</button></div>'
            f'<div class="nd-form">{form}</div></dialog>')


def new_ticket_form(cfg, repo: str, csrf: str) -> str:
    from . import localtickets as LT
    if not csrf or not cfg.repos:
        return ""
    opts = "".join(f'<option value="{esc(r)}"{" selected" if r == repo else ""}>{esc(r)}</option>' for r in cfg.repos)
    form = (f'<form method="post" action="/tickets/create" enctype="multipart/form-data" class="field">{csrf_field(csrf)}'
            f'<label>Repository<select name="repo">{opts}</select></label>'
            f'<label>Title<input name="title" required maxlength="{MAX_TITLE}"></label>'
            f'<label>Description (optional)<textarea name="body" rows="5" maxlength="{MAX_BODY}"></textarea></label>'
            + ('<label>Keep it in<select name="where"><option value="local">The factory (local ticket)</option>'
               '<option value="github">GitHub issues</option></select></label>' if cfg.local_enabled else
               '<p class="muted ft-note">It becomes a GitHub issue. Local tickets, kept in the factory, are off. '
               '<a href="/tickets?ask=local#tk-settings">Turn them on</a></p>')
            + (f'<label>Attachments (optional, local tickets only)<input type="file" name="file" multiple '
               f'accept=".png,.jpg,.jpeg,.gif,.pdf,.txt,.md,.log,.json,.csv"></label><p class="muted">{esc(LT.attach_help(cfg))}</p>'
               if cfg.local_enabled else '')
            + f'{start_field([r.name for r in cfg.roles])}'
            '<button>Create ticket</button></form>')
    return ticket_dialog("nt-d", "New ticket", "Kept in the factory or filed on GitHub.", form)


def trigger_labels(cfg) -> tuple:
    """Every label that makes the factory start work on a ticket."""
    return (cfg.trigger_label, cfg.auto_label, *(r.label for r in cfg.roles), *([cfg.review.label] if cfg.review.enabled else []),
            *([cfg.conflicts.label] if cfg.conflicts.enabled else []))


def close_form(repo: str, i: dict, csrf: str, back: str, busy: bool = False) -> str:
    n = int(i["number"])
    stops = " The factory stops the work it is doing on it." if busy else ""
    title = (i.get("title") or "")[:60]
    return (f'<details class="disclose"><summary aria-label="Close ticket {esc(tracker.display(n))}">Close</summary>'
            f'<form method="post" action="/tickets/close" class="inline">{csrf_field(csrf)}<input type="hidden" name="repo" value="{esc(repo)}">'
            f'<input type="hidden" name="n" value="{n}"><input type="hidden" name="back" value="{esc(back)}">'
            f'<p>Close {esc(tracker.display(n))} “{esc(title)}”? This closes the issue on GitHub and adds a comment. You can reopen it there.{esc(stops)}</p>'
            '<button class="secondary">Close ticket</button></form></details>')


def action_forms(repo: str, n: int, acts, csrf: str, back: str = "/tickets") -> str:
    """One POST form per available action; the first is the primary button. The browser only names an action."""
    hidden = (f'{csrf_field(csrf)}<input type="hidden" name="repo" value="{esc(repo)}"><input type="hidden" name="n" value="{int(n)}">'
              f'<input type="hidden" name="back" value="{esc(back)}">')
    return "".join(
        f'<form method="post" action="/tickets/start" class="inline">{hidden}'
        f'<button name="action" value="{esc(a)}" class="{"" if k == 0 else "secondary"}" title="{esc("Removes the trigger labels" if lab is None else "Applies " + lab)}" '
        f'aria-label="{esc(text)} {esc(tracker.display(n))}">{esc(text)}</button></form> ' for k, (a, text, lab) in enumerate(acts))


def action_forms_for(need: dict, csrf: str, back: str = "/") -> str:
    """Everything needed to finish a "Needs you" row on the Floor: the buttons for a ticket the factory asked about, or the question
    cards (an option per question, a free-text answer, and Accept recommendations) for one with questions."""
    if need.get("questions"):
        return question_form(need["repo"], need["issue"], need["st"], csrf, back)
    return action_forms(need["repo"], need["issue"], need["acts"], csrf, back)


_needs_cache: dict = {"at": 0.0, "rows": None, "titles": {}, "dirty": False}
_needs_lock = threading.Lock()
NEEDS_TTL = 20      # seconds: the Floor refreshes every 5s, GitHub is asked at most this often
NEEDS_EXTRA = 40    # waiting tickets older than the first page of open issues, fetched one by one


def needs_forget(repo: str, n: int) -> None:
    """A ticket was acted on from the UI: drop it from the cached rows (the page that follows still has a GitHub-checked list)
    and make the next needs_you read GitHub again."""
    with _needs_lock:
        if _needs_cache["rows"] is not None:
            _needs_cache["rows"] = [r for r in _needs_cache["rows"] if (r["repo"], r["issue"]) != (repo, n)]
        _needs_cache["dirty"] = True


def needs_cached():
    """The tray's rows if they were read recently, else None. Never calls GitHub: the nav badge uses it on every page."""
    with _needs_lock:
        return _needs_cache["rows"] if _needs_cache["rows"] is not None and time.time() - _needs_cache["at"] < NEEDS_TTL * 6 else None


def needs_you(h) -> list | None:
    """The tickets waiting for a person, for the Floor: either the factory asked (the Telegram prompt), or a stage left questions.
    Read from GitHub (labels decide, as on the Tickets page) and cached briefly. None when there is no token or GitHub fails."""
    cfg = h.app.cfg()
    gh = _gh(h)
    if not (cfg.github_issues_enabled or cfg.local_enabled) or gh is None or not cfg.repos:
        return None
    gh_on = cfg.github_issues_enabled           # off: only local tickets are read (their questions and calls for a person are in the local store)
    with _needs_lock:
        if _needs_cache["rows"] is not None and not _needs_cache["dirty"] and time.time() - _needs_cache["at"] < NEEDS_TTL:
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
    rows, titles = [], {}
    try:
        pages = (dict(zip(cfg.repos, (issues for issues, _ in _par(*[(lambda r=r: gh.issues(r, "open", None, 1)) for r in cfg.repos]))))
                 if gh_on else {repo: [] for repo in cfg.repos})
        # The first page holds the newest 50 open items, pull requests included. A ticket the factory is waiting on can be older:
        # fetch those by number (the questions it left, or its call for a person), so the tray agrees with the rest of the page.
        for repo in cfg.repos:
            seen = {i["number"] for i in pages[repo]}
            asked = sorted((d.get("decided_at") or 0, n) for (r, n), d in decisions.items() if r == repo and d.get("outcome") == "human")
            want = [n for n in sorted(waiting.get(repo, ()), reverse=True) if n not in seen]
            want += [n for _, n in reversed(asked) if n not in seen and n not in want]
            if not gh_on:
                want = [n for n in want if tracker.is_local(n)]
            more = _par(*[(lambda n=n, r=repo: gh.get_issue(r, n)) for n in want[:NEEDS_EXTRA]]) if want else []
            pages[repo] = pages[repo] + [i for i in more if "pull_request" not in i and i.get("state") == "open"]
        for repo, issues in pages.items():
            for i in issues:
                titles[(repo, i["number"])] = (i.get("title") or "")[:160]
                d = decisions.get((repo, i["number"]))
                status, acts = actions_for(cfg, i, d, (repo, i["number"]) in queued)
                if status == NEEDS_PERSON:
                    detail = (d or {}).get("detail", "")
                    m = re.search(r"cls=(\w+)/(\w+)/human=\w+/conf=([0-9.]+)", detail)
                    rows.append({"repo": repo, "issue": i["number"], "title": i.get("title", "")[:120], "acts": acts, "at": (d or {}).get("decided_at", 0),
                                 "scores": W.parse((d or {}).get("scores")),
                                 "reason": (detail.rsplit(";", 1)[-1].strip() or "needs a person")[:80],
                                 **({"kind": m.group(1), "complexity": m.group(2), "conf": min(1.0, float(m.group(3)))} if m else {})})
                elif i["number"] in waiting.get(repo, ()) and status not in ("closed", "running", "queued"):
                    rows.append({"repo": repo, "issue": i["number"], "title": i.get("title", "")[:120], "acts": [], "questions": True,
                                 "at": (decisions.get((repo, i["number"])) or {}).get("decided_at", 0), "reason": "questions waiting for you"})
        asking = [r for r in rows if r.get("questions")]
        if asking:                       # the questions themselves, so they can be answered on the Floor (read from the factory's own comments)
            me = _login(gh)
            found = _par(*[(lambda r=r: Q.latest(Q.from_comments(gh.issue_comments(r["repo"], r["issue"]), me))) for r in asking])
            for r, cur in zip(asking, found):
                r["st"] = cur
            rows = [r for r in rows if not r.get("questions") or (r.get("st") and r["st"].pending())]
    except (urllib.error.URLError, OSError, ValueError, KeyError, TypeError):
        log.warning("floor: could not read the tickets that need you")
        return None
    rows.sort(key=lambda r: r["at"], reverse=True)
    with _needs_lock:
        _needs_cache.update(at=time.time(), rows=rows, titles=titles, dirty=False)
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


def question_form(repo: str, n: int, st, csrf: str, back: str = "/tickets") -> str:
    """One form for all of a stage's open questions: an option per question (radio), a free-text answer, Send answers for what was
    chosen, and Accept all recommendations for the rest. The browser only names question and option ids; both are checked against
    the questions the factory posted on the ticket when the answer arrives."""
    hidden = (f'{csrf_field(csrf)}<input type="hidden" name="repo" value="{esc(repo)}"><input type="hidden" name="n" value="{int(n)}">'
              f'<input type="hidden" name="stage" value="{esc(st.stage)}"><input type="hidden" name="back" value="{esc(back)}">')
    blocks = []
    for k, q in enumerate(st.questions, 1):
        cls = badge("safe default", "good") if q.safe else badge("needs a person", "bad") + (f' <span class="muted">{esc(q.why)}</span>' if q.why else "")
        head = f'<div class="nd-qhead"><span class="muted fl-mono">{k} of {len(st.questions)}</span> {cls}</div><h3 class="nd-qt">{esc(q.text)}</h3>'
        if q.id in st.answers:
            blocks.append(f'<div class="nd-q" data-q="done">{head}<p class="muted">{esc(Q.describe(q, st.answers[q.id]))}</p></div>')
            continue
        opts = "".join(
            f'<label class="nd-opt{" rec" if o == q.recommended else ""}"><input type="radio" name="a_{esc(q.id)}" value="{esc(o)}">'
            f'<span class="nd-r"></span><span class="fl-grow">{esc(lab)}</span>'
            + ('<span class="badge warn">Recommended</span>' if o == q.recommended else "") + "</label>" for o, lab in q.options)
        blocks.append(
            f'<div class="nd-q" data-q="open">{head}<div class="nd-opts" role="radiogroup" aria-label="{esc(q.text)}">{opts}</div>'
            f'<label class="muted nd-own">Or write your own<input name="x_{esc(q.id)}" maxlength="{Q.MAX_OTHER}" placeholder="Your own answer"></label>'
            f'<p class="muted nd-why">Recommended: {esc(q.label(q.recommended))}. {esc(q.reason)}</p></div>')
    read = views.doc_link(repo, n, st.stage, "Read the " + views.DOC_NOUN.get(st.stage, "document"), True)
    read = f'<p class="nd-doc">{read}</p>' if read else ""
    return (f'<form method="post" action="/tickets/answer" class="nd-form">{hidden}{read}<div class="nd-qs">{"".join(blocks)}</div>'
            '<div class="nd-foot"><button name="send" value="1" class="nd-send">Send answers</button> '
            '<button name="accept" value="1" class="secondary" formnovalidate '
            'title="Records the recommended option for every question not answered yet, then starts the next stage">Accept all recommendations</button>'
            '<span class="muted">Answers are posted on the ticket as the factory\'s account. The next stage starts at the next poll.</span></div></form>')


def question_popup(repo: str, i: dict, st, csrf: str, back: str, alone: bool = False) -> str:
    """The Answer… button and the dialog holding the question form, as on the Floor. Without scripts the button cannot open the
    dialog, so the link narrows the list to this ticket, where (alone) the form is shown in the page."""
    n = int(i["number"])
    ref = esc(views.ref(repo, n, short=True))
    total = len(st.questions)
    form = question_form(repo, n, st, csrf, back)
    meta = f'The {esc(st.stage)} asked {total} question{"s" if total != 1 else ""}'
    dialog = (f'<dialog id="nd-t{n}" class="nd-dialog" aria-label="Questions for {ref}"><div class="nd-dhead"><div><span class="badge warn">{ref}</span> '
              f'<span class="nd-t">{esc(i.get("title"))}</span><p class="muted">{meta}</p></div>'
              f'<button type="button" class="secondary" data-close aria-label="Close">Close</button></div>{form}</dialog>')
    opener = f'<button type="button" class="secondary" data-dialog="nd-t{n}" aria-label="Answer questions for {ref}">Answer…</button>'
    if alone:
        fallback = f'<noscript><details open><summary>Answer questions</summary>{form}</details></noscript>'
    else:
        fallback = f'<noscript><a class="btn secondary" href="/tickets?repo={esc(repo)}&amp;q=%23{n}">Answer</a></noscript>'
    return opener + fallback + dialog


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
        out = note + action_forms(repo, i["number"], acts, csrf, back) + f'<a href="/labels/issue?repo={esc(repo)}&amp;n={int(i["number"])}">Labels</a>'
        if i["number"] in asked:
            out += " " + question_popup(repo, i, asked[i["number"]], csrf, back, alone=len(issues) == 1)
        if status == "closed":
            return out
        return out + " " + close_form(repo, i, csrf, back, status in BUSY or i["number"] in asked)

    def stage_of(i) -> str:
        """The one stage a ticket is in. Failed counts as Needs you (a person must act); queued and ready tickets show the stage they are about to run."""
        n = i["number"]
        d = decisions.get((repo, n))
        status, _ = actions_for(cfg, i, d, (repo, n) in approved)
        names = set(_names(i))
        if status == "closed":
            return "Done"
        if d and d.get("outcome") in ("ignored", "skip"):
            return "Ignored"
        if status in (NEEDS_PERSON, "failed") or n in asked:
            return "Needs you"
        if status == "pr open":
            return "PR open"
        if status in ("running", "queued", "starting"):
            role = next((r.name for r in cfg.roles if f"factory:working-{r.name}" in names or r.label in names), "")
            return f"{VERBS.get(role, role.capitalize())} working" if role else "Build working"
        return "Not started"

    def progress(i) -> str:
        names = set(_names(i))
        marks = ["on" if r.done_label in names else "" for r in cfg.roles] + ["on" if "factory:pr-open" in names else ""]
        return f'<span class="pg" role="img" aria-label="{marks.count("on")} of {len(marks)} steps done">{"".join('<i class="on"></i>' if m else "<i></i>" for m in marks)}</span> <span class="muted fl-mono">{marks.count("on")}/{len(marks)}</span>'

    def updated(i) -> str:
        d = decisions.get((repo, i["number"]))
        when = _epoch(i.get("updated_at"))
        return (f'{esc(views.ago(when))}' if when else '<span class="muted">—</span>') + (f'<br>{factory_cell(i["number"])}' if d else "")

    rows = "".join(
        f'<tr data-row="{esc(repo)}#{int(i["number"])}"><td data-l="Ticket">{views.ticket_link(repo, i["number"])}<br><span>{esc(i.get("title"))}</span><br>'
        f'{" ".join(chip(n, cfg) for n in _names(i))}</td><td data-l="Stage">{esc(stage_of(i))}</td>'
        f'<td data-l="Progress">{progress(i)}</td><td data-l="Updated">{updated(i)}</td>'
        f'<td class="actions" data-l="Actions">{buttons(i)}</td></tr>'
        for i in issues)
    return ('<div class="scroll"><table class="tickets stack"><thead><tr><th>Ticket</th><th>Stage</th><th>Progress</th><th>Updated</th><th>Actions</th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>')


def footer(shown: int, total) -> str:
    return f'<p class="tk-foot">Showing {int(shown)}' + (f' of {int(total)}' if isinstance(total, int) and total >= shown else "") + "</p>"


def pager(repo, state, label, text, page_no, more, stage: str = "") -> str:
    from urllib.parse import urlencode

    def link(text_, p):
        return f'<a href="/tickets?{esc(urlencode({"repo": repo, "state": state, "label": label, "q": text, "page": p, **({"stage": stage} if stage else {})}))}">{text_}</a>'

    return '<p class="pager">' + (link("← newer", page_no - 1) if page_no > 1 else "") + " " + (link("older →", page_no + 1) if more else "") + "</p>"


def _edit_body(cfg, repo: str, n: int, issue: dict, all_labels: list[str], csrf: str) -> str:
    have = _names(issue)
    hidden = f'{csrf_field(csrf)}<input type="hidden" name="repo" value="{esc(repo)}"><input type="hidden" name="n" value="{n}">'
    ref = views.ref(repo, n)
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
    _page(h, status, f"Edit labels · {views.ref(repo, n)}", _edit_body(cfg, repo, n, issue, names, csrf), csrf, flash, kind)


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
                for lab in trigger_labels(cfg):
                    if lab in held and lab != chosen:
                        gh.remove_label(repo, n, lab)
        needs_forget(repo, n)                                # the Floor's tray must not show it again
    except Refused as e:
        return _done(h, form, csrf, str(e), "bad")
    except (urllib.error.URLError, OSError) as e:
        log.warning("tickets: start %s on %s#%d failed", action, repo, n)
        return _done(h, form, csrf, _github_error(e), "bad")
    log.info("tickets: %s on %s#%d from the UI", "approved " + action if approval_for(cfg, action) else "started " + action, repo, n)
    _done(h, form, csrf, FLASH["skipped" if action == "skip" else "started"])


_recent: dict = {}          # (repo, title) -> time of the last create, so a double click makes one issue
_recent_lock = threading.Lock()


_flights: list = []                       # (when, "#n") for tickets created here: the floor flies them in
_flights_lock = threading.Lock()


def flights(now: float, last: float = 20.0) -> list[dict]:
    """Tickets created in the last few seconds, for the floor's 747 (each starts at its age, so a refresh does not restart it)."""
    with _flights_lock:
        _flights[:] = [f for f in _flights if now - f[0] < last]
        return [{"label": lab, "age": now - t} for t, lab in _flights]


def create(h, form, csrf: str, files=()) -> None:
    """A person's new ticket: title, body and at most one start action, looked up in start_options (never a label from the
    browser). No start action means no labels, so it does not start work. Validated before GitHub is called."""
    cfg = h.app.cfg()
    title, body = " ".join((form.get("title") or "").split()), (form.get("body") or "").strip()
    try:
        repo = _repo(cfg, form.get("repo", ""))
        if not title:
            raise Refused("Enter a title.")
        if len(title) > MAX_TITLE:
            raise Refused(f"The title is too long ({MAX_TITLE} characters at most).")
        if len(body) > MAX_BODY:
            raise Refused(f"The description is too long ({MAX_BODY:,} characters at most).")
        chosen = next((o for o in start_options(cfg) if o[0] == (form.get("start") or "").strip()), None)
        if chosen is None:
            raise Refused("That start action is not available.")
        labels = [chosen[2]] if chosen[2] else []
        where = form.get("where") or ("local" if cfg.local_enabled else "github")
        if where not in ("local", "github") or (where == "local" and not cfg.local_enabled):
            raise Refused("Local tickets are turned off. Turn them on under Settings → General.")
        if files and where != "local":
            raise Refused("Attachments can only be kept on local tickets. Choose “The factory”, or remove the files.")
        if files:
            from . import localtickets as LT
            files = LT.vet(cfg, files)
    except Refused as e:
        return _done(h, form, csrf, str(e), "bad")
    if where == "local":
        return _create_local(h, form, csrf, cfg, repo, title, body, labels, chosen[1], files)
    gh = _gh(h)
    if gh is None:
        return _done(h, form, csrf, NO_TOKEN, "bad")
    now = time.time()
    with _recent_lock:
        if now - _recent.get((repo, title), 0) < DUP_SECONDS:
            return _done(h, form, csrf, "That ticket was just created.", "bad")
        if len(_recent) > 100:
            _recent.clear()
        _recent[(repo, title)] = now
    try:
        n = gh.create_ticket(repo, title, body, labels)["number"]
    except (urllib.error.URLError, OSError, KeyError, TypeError) as e:
        with _recent_lock:
            _recent.pop((repo, title), None)
        log.warning("tickets: create on %s failed", repo)
        return _done(h, form, csrf, _github_error(e) if isinstance(e, urllib.error.HTTPError)
                     else "GitHub did not accept the ticket. Nothing was created. Try again, or check the token under Credentials.", "bad")
    log.info("tickets: created %s#%s from the UI%s", repo, n, f" with {labels[0]}" if labels else "")
    with _flights_lock:
        _flights.append((time.time(), f"#{int(n)}"))
        del _flights[:-5]
    _done(h, form, csrf, f"Created {repo}#{int(n)} and started {chosen[1]}. The factory picks it up on its next poll." if labels
          else f"Created {repo}#{int(n)}. Nothing started.")


def _create_local(h, form, csrf: str, cfg, repo: str, title: str, body: str, labels: list[str], verb: str, files=()) -> None:
    from . import localtickets as LT
    now = time.time()
    with _recent_lock:
        if now - _recent.get((repo, title), 0) < DUP_SECONDS:
            return _done(h, form, csrf, "That ticket was just created.", "bad")
        _recent[(repo, title)] = now
    try:
        n = LT.create(cfg, repo, title, body, labels, files)
    except ValueError as e:
        with _recent_lock:
            _recent.pop((repo, title), None)
        return _done(h, form, csrf, str(e), "bad")
    log.info("tickets: created local %s %s from the UI%s", repo, tracker.display(n), f" with {labels[0]}" if labels else "")
    with _flights_lock:
        _flights.append((time.time(), tracker.display(n)))
        del _flights[:-5]
    ref = views.ref(repo, n)
    _done(h, form, csrf, f"Created {ref} and started {verb}. The factory picks it up on its next poll." if labels
          else f"Created {ref}. Nothing started.")


def merge_pr(h, form, csrf: str) -> None:
    """Merge a pull request the factory is watching, from the Tickets > PRs view. Only a tracked PR whose checks passed is merged; it is
    re-read first (open, not a draft, head unchanged) and GitHub still applies the repository's own rules (reviews, required checks)."""
    repo, n = form.get("repo", ""), form.get("n", "")
    back = "/tickets"
    if not (views.REPO.fullmatch(repo) and NUM.match(n or "")):
        return _page(h, 400, "Tickets", "", csrf, "That is not a pull request.", "bad")
    db = h.app.ro_db()
    try:
        row = next((p for p in dbm.watched_prs(db, 300) if p["repo"] == repo and p["number"] == int(n)), None) if db is not None else None
    finally:
        if db is not None:
            db.close()
    if row is not None:                    # back to the ticket the pull request belongs to
        back = f"/ticket?repo={quote(row['issue_repo'], safe='')}&n={int(row['issue_num'])}"
    if row is None or row["status"] != "passed":
        return _done_to(h, csrf, back, "Only a pull request whose checks passed can be merged here.", "bad")
    gh = _gh(h)
    if gh is None:
        return _done_to(h, csrf, back, NO_TOKEN, "bad")
    try:
        pr = gh.get_pr(repo, int(n))
        if pr.get("merged") or pr.get("state") != "open":
            return _done_to(h, csrf, back, f"{repo}#{n} is already closed or merged.", "bad")
        if pr.get("draft"):
            return _done_to(h, csrf, back, f"{repo}#{n} is still a draft.", "bad")
        gh.merge_pr(repo, int(n), pr["head"]["sha"])
    except urllib.error.HTTPError as e:
        if e.code in (405, 409, 422):
            return _done_to(h, csrf, back, f"GitHub would not merge {repo}#{n} ({e.code}): it has conflicts, a missing review or required check, or changed just now.", "bad")
        return _done_to(h, csrf, back, _github_error(e), "bad")
    except (urllib.error.URLError, OSError, ValueError) as e:
        return _done_to(h, csrf, back, _github_error(e), "bad")
    _done_to(h, csrf, back, f"Merged {repo}#{n}.")


def _done_to(h, csrf: str, where: str, msg: str, kind: str = "ok") -> None:
    flash_set(csrf, msg, kind)
    h._redirect(where)


def close(h, form, csrf: str) -> None:
    """Close an open ticket, after a fixed comment. The ticket is re-read first. One that is running, queued or waiting for a person
    is closed too: its trigger labels come off, and the orchestrator stops the work once it sees the ticket is closed."""
    cfg = h.app.cfg()
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
        status, _ = actions_for(cfg, issue, _decisions(h, repo).get((repo, n)), (repo, n) in _approved(h))
        if status == "closed":
            raise Refused("This ticket is already closed.")
        busy = status in BUSY
        gh.comment(repo, n, CLOSE_BUSY_COMMENT if busy else CLOSE_COMMENT)
        held = set(_names(issue))                        # a reopen on GitHub must not silently restart the work
        for lab in (*trigger_labels(cfg), Q.NEEDS_ANSWERS):
            if lab in held:
                gh.remove_label(repo, n, lab)
        gh.update_issue(repo, n, state="closed")
    except Refused as e:
        return _done(h, form, csrf, str(e), "bad")
    except (urllib.error.URLError, OSError) as e:
        log.warning("tickets: close %s#%d failed", repo, n)
        return _done(h, form, csrf, _github_error(e), "bad")
    needs_forget(repo, n)
    log.info("tickets: closed %s %s from the UI", repo, tracker.display(n))
    _done(h, form, csrf, f"Closed {views.ref(repo, n)}.")


def _open_numbers(gh, cfg, repo: str, label: str, max_pages: int = 20) -> tuple[list[int], int]:
    """The next batch (at most MAX_BULK) of open issues to import, in GitHub's order, and how many more wait behind it. Issues
    already moved or queued are left out, so a second submit takes the next batch."""
    taken = tracker.taken(cfg.db_path, repo)
    found: list[int] = []
    for page in range(1, max_pages + 1):
        got, more = gh.issues(repo, "open", label or None, page)
        found += [int(i["number"]) for i in got if int(i["number"]) not in taken
                  and tracker.MOVED_LABEL not in {x.get("name") for x in i.get("labels", [])}]
        if not more:
            break
    return found[:tracker.MAX_BULK], max(0, len(found) - tracker.MAX_BULK)


def import_issues(h, form, csrf: str) -> None:
    """Queue GitHub issues to be moved into the local tracker: one by number, or the open issues with a label. The orchestrator
    re-reads and checks each one (a pull request, a moved or a busy issue is refused) before it changes anything."""
    cfg = h.app.cfg()
    try:
        if not cfg.local_enabled:
            raise Refused("The local tracker is turned off.")
        repo = _repo(cfg, form.get("repo", ""))
        label = (form.get("label") or "").strip()
        if len(label) > 50:
            raise Refused("That label is not valid.")
        every = form.get("all") == "1"
        if sum(map(bool, (label, (form.get("n") or "").strip(), every))) > 1:
            raise Refused("Give an issue number, a label, or every open issue, not a mix.")
        numbers = [] if label or every else [_number((form.get("n") or "").strip())]
        if any(tracker.is_local(n) for n in numbers):
            raise Refused("That is a local ticket, not a GitHub issue.")
    except Refused as e:
        return _done(h, form, csrf, str(e), "bad")
    left = 0
    if label or every:
        gh = _gh(h)
        if gh is None:
            return _done(h, form, csrf, NO_TOKEN, "bad")
        try:
            numbers, left = _open_numbers(gh, cfg, repo, label)
        except (urllib.error.URLError, OSError) as e:
            return _done(h, form, csrf, _github_error(e), "bad")
        if not numbers:
            return _done(h, form, csrf, "No open issue has that label." if label else "No open issue is left to import.", "bad")
        if every and form.get("close") == "1" and form.get("confirm") != "1":
            more = f" (and {left} more after that)" if left else ""
            return _done(h, form, csrf, f"This would import and close {len(numbers)} open issue(s) of {repo} on GitHub{more}. "
                         "Nothing was queued: tick the confirmation to go ahead.", "bad")
    tracker.request_import(cfg.db_path, repo, numbers, form.get("close") == "1")
    log.info("tickets: queued %d import(s) from %s", len(numbers), repo)
    rest = f" {left} more are left: import again for the next batch." if left else ""
    _done(h, form, csrf, f"Queued {len(numbers)} issue(s) to move. The factory moves them at its next poll.{rest}")


def _record(gh, cfg, h, repo: str, n: int, stage, picks, accept_all: bool) -> bool:
    """Record a person's answers on one ticket, after the same checks wherever they come from. True when nothing needing a person is
    left, in which case the auto label starts the next stage through the factory's normal gates."""
    issue = gh.get_issue(repo, n)
    if "pull_request" in issue:
        raise Refused("That is a pull request, not a ticket.")
    status, _ = actions_for(cfg, issue, _decisions(h, repo).get((repo, n)))
    if status in ("closed", "running"):
        raise Refused(f"Answers cannot be recorded now ({status}).")
    if status != "queued":
        _existing(gh, repo, cfg.auto_label)               # checked before anything is posted
    _, done = Q.record(gh, repo, n, stage, picks, "factory UI", accept_all)
    if done and status != "queued":
        gh.add_labels(repo, n, [cfg.auto_label])
    return done


def _picks(form) -> dict:
    """The answers in the form: a_<question> is an option id, x_<question> is the person's own words (which win)."""
    picks = {}
    for k, v in form.items():
        if k.startswith("a_") and v:
            picks[k[2:]] = ("option", v)
    for k, v in form.items():
        if k.startswith("x_") and v.strip():
            picks[k[2:]] = ("other", v.strip())
    return picks


def answer(h, form, csrf: str) -> None:
    """A person's answers to open questions, or Accept all recommendations. Posted as the factory's account in the marked answers
    comment, which the factory then reads as a person's input (never as an instruction). The question and option ids are validated
    against the questions the factory posted on the ticket."""
    cfg = h.app.cfg()
    try:
        repo, n = _repo(cfg, form.get("repo", "")), _number(form.get("n", ""))
    except Refused as e:
        return _page(h, 400, "Tickets", "", csrf, str(e), "bad")
    stage = form.get("stage", "")
    accept_all = form.get("accept") == "1"
    picks = None if accept_all else _picks(form)
    gh = _gh(h)
    if gh is None:
        return _done(h, form, csrf, NO_TOKEN, "bad")
    try:
        if stage or not accept_all:      # accepting every recommendation needs no stage: the latest stage's questions are used
            if not re.fullmatch(r"[a-z]{1,20}", stage):
                raise Refused("That answer does not match the ticket's questions. Reload the page.")
        if not accept_all and not picks:
            raise Refused("Choose an answer first, or accept the recommendations.")
        done = _record(gh, cfg, h, repo, n, stage or None, picks, accept_all)
    except (Q.Refused, Refused) as e:
        return _done(h, form, csrf, str(e), "bad")
    except (urllib.error.URLError, OSError) as e:
        log.warning("tickets: answer on %s#%d failed", repo, n)
        return _done(h, form, csrf, _github_error(e), "bad")
    needs_forget(repo, n)                                    # the tray must not show it again
    log.info("tickets: answers recorded on %s#%d from the UI", repo, n)
    _done(h, form, csrf, FLASH["continued" if done else "answered"])


TICKETS = re.compile(r"^[\w.-]+/[\w.-]+#\d{1,9}$")


def answer_all(h, form, csrf: str) -> None:
    """Accept the recommendations on several tickets at once. The person has just been shown what each recommendation is; the tickets
    named here are checked like any other (configured repository, valid number) and every ticket's questions are re-read from GitHub."""
    cfg = h.app.cfg()
    refs = [t for t in (form.get("tickets") or "").split(",") if t][:20]
    if not refs or any(not TICKETS.fullmatch(t) for t in refs):
        return _done(h, form, csrf, "That list of tickets is not valid. Reload the page.", "bad")
    gh = _gh(h)
    if gh is None:
        return _done(h, form, csrf, NO_TOKEN, "bad")
    ok, failed = 0, []
    for ref in refs:
        repo, _, num = ref.rpartition("#")
        try:
            _repo(cfg, repo), _number(num)
            _record(gh, cfg, h, repo, int(num), None, None, True)
            needs_forget(repo, int(num))
            ok += 1
        except (Q.Refused, Refused, urllib.error.URLError, OSError):
            failed.append(ref.split("/")[-1])
    log.info("tickets: recommendations accepted on %d ticket(s) from the UI, %d failed", ok, len(failed))
    msg = f"Recommendations accepted on {ok} ticket{'s' if ok != 1 else ''}." + (f" Could not do: {', '.join(failed)}." if failed else "")
    _done(h, form, csrf, msg, "bad" if failed and not ok else "ok")


def stage_comment(h, repo: str, issue: int, stage: str) -> str | None:
    """The newest stage document the factory's own account posted on the ticket, or None. Read from GitHub, never from the request."""
    gh = _gh(h)
    if gh is None or repo not in h.app.cfg().repos:
        return None
    try:
        me, found = _login(gh), None
        for c in gh.issue_comments(repo, issue):
            m = Q.STAGE_HEAD.match(c.get("body") or "")
            if m and m.group(1) == stage and (c.get("user") or {}).get("login") == me:
                found = c["body"]
        return found
    except Exception:
        return None


def titles_cached() -> dict:
    """{(repo, issue): title} of the open issues the last Needs-you read saw (no GitHub call)."""
    with _needs_lock:
        return dict(_needs_cache.get("titles") or {})


def has_token(h) -> bool:
    """Whether a GitHub token is saved (no GitHub call)."""
    return bool(I.read_secret(h.app.cfg(), "github"))
