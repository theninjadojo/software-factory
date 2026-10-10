"""The factory's two main screens, built to the "Ticket list and detail" design: the Factory dashboard and Tickets (a filtered
list beside the selected ticket, which a phone shows as two screens).

Both use one picture of the factory: ten stations a ticket passes on a belt (poll, classify, route, the three read-only stages,
build, review, CI and the pull request). A ticket's journey (db.journey) and its pull requests decide each station's state:
done (green), running (amber, the belt moves and crates ride it), waiting for a person (blue, crates back up at the door),
failed (red) or not reached (dim). The Floor shows where every open ticket is; a ticket shows its own route.

Every text is escaped; colours, placement and motion are CSS classes (the page policy forbids inline styles)."""
import re
import time
from urllib.parse import urlencode

from .. import db as dbm
from .. import plan
from .. import questions as Q
from .. import why as W
from ..tracker import display, is_local
from . import floorplan, localtickets as LT, outcome as OC, plant, views, yard
from .views import ago, esc, tok

STATIONS = (("poll", "Poll"), ("classify", "Classify"), ("route", "Route"), ("analyst", "Analyst"), ("designer", "Designer"),
            ("architect", "Architect"), ("build", "Build"), ("review", "Review"), ("ci", "CI"), ("pr", "Pull request"))
LABEL = dict(STATIONS)
SHORT = {**LABEL, "pr": "PR"}
ICON = {
    "poll": "M4 12a8 8 0 0 1 14-5.3M20 12a8 8 0 0 1-14 5.3M18 3v4h-4M6 21v-4h4",
    "classify": "M3 5h18l-7 8v6l-4-2v-4z",
    "route": "M12 21v-9M12 12L6 5M12 12l6-7M6 5H3M6 5v3M18 5h3M18 5v3",
    "analyst": "M10 17a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM15 15l6 6",
    "designer": "M4 20l4-1 11-11-3-3L5 16zM14 6l3 3",
    "architect": "M4 20h16M6 20V9l6-5 6 5v11M10 20v-6h4v6",
    "build": "M14 6a4 4 0 0 0-5 5L3 17l4 4 6-6a4 4 0 0 0 5-5l-3 3-3-1-1-3z",
    "review": "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
    "ci": "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM8 12l3 3 5-6",
    "pr": "M3 8l9-5 9 5v8l-9 5-9-5zM3 8l9 5 9-5M12 13v8",
}
STATE_WORD = {"done": "Done", "run": "Running", "wait": "Needs you", "fail": "Failed", "none": "Not reached"}
# The ticket filters, in the order of the chips. The colour dot is the state a ticket in it is in.
FILTERS = (("needs", "Needs you", "wait"), ("working", "Working", "run"), ("prs", "PRs & CI", "run"), ("failed", "Failed", "fail"),
           ("new", "Not started", "none"), ("done", "Done", "done"), ("all", "All", ""))
TICKET_WORD = {"needs": "Needs you", "working": "Working", "prs": "PR and CI", "failed": "Failed", "new": "Not started", "done": "Done"}
TICKET_TONE = {"needs": "wait", "working": "run", "prs": "run", "failed": "fail", "new": "none", "done": "done"}
ORDER = {"needs": 0, "working": 1, "failed": 2, "prs": 3, "new": 4, "done": 5}
_RUN_AT = {"analyst": "analyst", "designer": "designer", "architect": "architect", "build": "build", "review": "review",
           "ci": "ci", "conflicts": "pr"}
_STEP = {"done": "done", "running": "run", "queued": "none", "failed": "fail", "waiting": "wait"}   # queued: interrupted or requeued, not running


def secs(s) -> str:
    if s is None:
        return "—"
    s = int(s)
    return "—" if s < 1 else f"{s}s" if s < 60 else f"{s // 60}m {s % 60:02d}s" if s < 3600 else f"{s // 3600}h {(s % 3600) // 60:02d}m"


def tokens(t: dict) -> str:
    return "—" if t["in"] is None and t["out"] is None else f'{tok(t["in"])} / {tok(t["out"])}'


# ---------------------------------------------------------------- the station model
def stations(j: dict, prs: list[dict] = ()) -> dict:
    """{station id: state} for one ticket, from its journey and its pull requests."""
    st = {sid: "none" for sid, _ in STATIONS}
    steps = (j or {}).get("steps") or []
    if not steps and not prs:
        return st
    st["poll"] = st["classify"] = "done"
    # Once the work is merged, a failure before the merge (a red CI run, a fix that changed nothing) no longer holds the ticket back.
    merged = max((p.get("updated") or 0 for p in prs if p.get("summary") == "merged"), default=0) \
        if prs and all(p.get("status") == "closed" for p in prs) else 0
    for s in steps:
        if s["kind"] == "person" and s["state"] == "waiting":
            at = s.get("stage") if s.get("stage") in LABEL else "route"
            st[at] = "wait"
            continue
        if s["kind"] == "person":
            st["route"] = "done"
            continue
        if s["kind"] == "decision":
            continue
        at = _RUN_AT.get(s["station"] or "")
        if at is None:
            continue
        st["route"] = "done" if st["route"] == "none" else st["route"]
        st[at] = "done" if s["state"] == "failed" and (s.get("started") or 0) < merged else _STEP.get(s["state"], "done")
    if any(s["kind"] in ("run", "ci") for s in steps) and st["route"] == "none":
        st["route"] = "done"
    for p in prs:
        status = p.get("status") or ""
        st["pr"] = "done"                                   # opened; whether it is merged yet shows on the ticket, not as work in progress
        if st["ci"] in ("none", "done", "run"):
            st["ci"] = CI_STATE.get(status, "done")
    # A station can only be finished if the ticket got past it: fill the gaps behind the furthest station reached.
    ids = [sid for sid, _ in STATIONS]
    last = max((i for i, sid in enumerate(ids) if st[sid] != "none"), default=-1)
    for sid in ids[:last]:
        if st[sid] == "none" and sid in ("poll", "classify", "route"):
            st[sid] = "done"
    return st


CI_STATE = {"watching": "run", "failed": "fail", "timed-out": "fail"}      # passed, no-ci and closed: nothing left to run
CI_WORD = {"watching": "checks running", "passed": "checks passed, waiting to merge", "no-ci": "no checks, waiting to merge",
           "failed": "checks failing", "timed-out": "checks timed out", "closed": "closed"}


def current(st: dict) -> str | None:
    """Where the ticket is now: the station waiting for a person, else the one working, else the one that failed, else the last reached."""
    for want in ("wait", "run", "fail"):
        hit = [sid for sid, _ in STATIONS if st[sid] == want]
        if hit:
            return hit[-1] if want != "run" else hit[0]
    reached = [sid for sid, _ in STATIONS if st[sid] != "none"]
    return reached[-1] if reached else None


def ticket_state(st: dict, needs: bool, prs: list[dict]) -> str:
    if needs or "wait" in st.values():
        return "needs"
    if any(st[s] == "run" for s in ("analyst", "designer", "architect", "build", "review")):
        return "working"
    if "fail" in st.values():
        return "failed"
    if any((p.get("status") or "") != "closed" for p in prs):
        return "prs"
    return "done"


def _svg(sid: str, cls: str = "sd-ico") -> str:
    return f'<svg class="{cls}" viewBox="0 0 24 24" aria-hidden="true"><path d="{ICON[sid]}"/></svg>'


def _crates(kind: str) -> str:
    return '<i class="sd-crate"></i><i class="sd-crate two"></i>' if kind in ("run", "wait") else ""


def _belt(into: str, extra: str = "") -> str:
    kind = {"done": "ok", "none": "dim"}.get(into, into)
    return f'<span class="sd-belt {kind}{extra}" aria-hidden="true">{_crates(kind)}</span>'


SKIPPABLE = ("analyst", "designer", "architect", "review")


def shown(st: dict) -> dict:
    """The stations as a ticket's page shows them: a stage the ticket went past without running it is skipped, not "not yet"."""
    ids = [sid for sid, _ in STATIONS]
    last = max((i for i, sid in enumerate(ids) if st[sid] != "none"), default=-1)
    return {sid: "skip" if v == "none" and sid in SKIPPABLE and ids.index(sid) < last else v for sid, v in st.items()}


def strip(st: dict, verify: dict | None = None, now: float | None = None) -> str:
    """One ticket's route as a row of small machines joined by belts (the Journey card), with the railway to the worker that checks
    its build when there is one."""
    st = shown(st)
    rows = [(sid, SHORT[sid], ICON[sid], st[sid]) for sid, _ in STATIONS]
    return f'<div class="sd-scroll"><div class="sd-strip fm-jwrap">{yard.journey(rows, verify, time.time() if now is None else now)}</div></div>'


def ticket_verify(db, repo: str, issue: int) -> dict | None:
    """The ticket's latest verification job (status and worker), or None (no job, or no worker tables yet)."""
    try:
        row = db.execute("SELECT status, worker FROM verify_jobs WHERE repo=? AND issue=? ORDER BY id DESC LIMIT 1", (repo, issue)).fetchone()
    except Exception:
        return None
    return {"status": row[0], "worker": row[1] or ""} if row else None


def progress(st: dict) -> str:
    return '<span class="sd-segs" aria-hidden="true">' + "".join(f'<i class="{st[sid]}"></i>' for sid, _ in STATIONS) + "</span>"


# ---------------------------------------------------------------- tickets: rows from the database (and GitHub's titles)
def ticket_rows(db, needs_rows=None, titles: dict | None = None, now: float | None = None, github: bool = True) -> list[dict]:
    """Every ticket the factory has looked at, newest activity first within each state, with its stations and a one-line why.
    github=False (Work from GitHub issues is off): local tickets only, plus GitHub ones whose job is still running (it finishes)."""
    now = time.time() if now is None else now
    titles, need = titles or {}, {(r["repo"], r["issue"]): r for r in needs_rows or []}
    try:
        prs_all = dbm.watched_prs(db, 500)
        base = dbm.tickets(db, 300)
    except Exception:
        return []
    by_ticket: dict = {}
    for p in prs_all:
        by_ticket.setdefault((p["issue_repo"], p["issue_num"]), []).append(p)
    try:                                       # tickets the factory worked on without a recorded decision (a label applied by hand)
        worked = [{"repo": r, "issue": n, "title": t, "detail": "", "decided_at": 0}
                  for r, n, t in db.execute("SELECT repo, issue, MAX(title) FROM runs WHERE issue > 0 GROUP BY repo, issue ORDER BY MAX(id) DESC LIMIT 300")]
    except Exception:
        worked = []
    worked += [{"repo": r, "issue": n, "title": None, "detail": "", "decided_at": 0} for r, n in by_ticket]
    worked += LT.rows(db)                       # every local ticket, started or not: the factory's database is its only home
    seen, out = set(), []
    for t in base + worked + [{"repo": r, "issue": n, "title": nr.get("title"), "detail": "", "decided_at": nr.get("at") or 0} for (r, n), nr in need.items()]:
        key = (t["repo"], t["issue"])
        if key in seen:
            continue
        seen.add(key)
        out.append(_one(db, t, by_ticket.get(key, []), need.get(key), titles.get(key), now,
                       needs_rows is not None or (not github and not is_local(int(t["issue"])))))
    if not github:
        out = [r for r in out if is_local(r["issue"]) or r["state"] == "working"]
    heads = dbm.doc_heads(db, [(r["repo"], r["issue"]) for r in out if r["state"] == "done"])
    for r in out:
        if r["state"] == "done":
            r["outcome"] = outcome_line(r, heads.get((r["repo"], r["issue"])))
    prios = plan.who_set_all(db)
    for r in out:
        r["priority"], r["prio_src"] = prios.get((r["repo"], r["issue"]), ("normal", ""))
    out.sort(key=lambda r: (ORDER[r["state"]], -r["when"]))
    return out


def _one(db, t: dict, prs: list, need, title, now: float, known: bool = False) -> dict:
    """known: GitHub has been read, so a ticket is waiting for a person only if it says so (the database's record of questions
    asked or a person called can be out of date: answered on GitHub, or the issue closed)."""
    j = dbm.journey(db, t["repo"], int(t["issue"]), now)
    st = stations(j, prs)
    loc = LT.info(db, t["repo"], int(t["issue"])) if is_local(int(t["issue"])) else None
    if need is None:
        closed = known or (loc and loc["state"] == "closed")     # a closed local ticket waits for no one
        ids = [sid for sid, _ in STATIONS]
        past = max((i for i, sid in enumerate(ids) if st[sid] in ("done", "run", "fail")), default=-1)
        # a person's ask the ticket has moved past (a later stage ran, or a PR opened) is answered, whatever the database still holds
        st = {k: ("done" if v == "wait" and (closed or ids.index(k) < past) else v) for k, v in st.items()}
    state = ticket_state(st, need is not None, prs)
    if loc and state == "done" and loc["state"] == "open" and not j["steps"] and not prs:
        state = "new"                           # an open local ticket nothing has run on yet
    idle = bool(loc and state == "done" and loc["state"] == "open" and not prs
                and any(s["kind"] == "run" and s.get("run_kind") != "chat" for s in j["steps"]))
    if idle:                                    # open, nothing running, nothing asked, nothing shipped: the factory stopped and a
        state = "needs"                         # person picks the next step (it is not done until it is closed or merged)
    at = current(st)
    last = max([s["finished"] or s["started"] for s in j["steps"]] + [p.get("updated") or 0 for p in prs] + [t.get("decided_at") or 0])
    return {"repo": t["repo"], "issue": int(t["issue"]), "title": (loc or {}).get("title") or title or (need or {}).get("title") or t.get("title")
            or f"Ticket {display(int(t['issue']))}",
            "state": state, "closed": bool(loc and loc["state"] == "closed"), "stations": st, "at": at, "when": last,
            "why": _idle_why(at) if idle else _why(state, j, prs, need, t, at, now), "journey": j, "prs": prs, "need": need,
            "decision": t.get("outcome") or "", "detail": t.get("detail") or ""}


def outcome_of(r: dict, head) -> dict:
    """The Outcome of a Done row; head is its latest stage document as (stage, text), or None."""
    stage, text = head or ("", "")
    ignored = _clip(plain(r.get("detail") or ""), 200) if r.get("decision") == "ignored" else ""
    return OC.derive(r, doc_gist(text) if text else "", stage, ignored)


def outcome_line(r: dict, head) -> str:
    """The Tickets list's one line: the document's sentence, else the reason; empty when nothing was recorded."""
    o = outcome_of(r, head)
    return "" if o["tone"] == "warn" else OC.preview(o["sentence"] or o["reason"])


def outcome_card(o: dict, repo: str, issue: int, at: float = 0) -> str:
    """The Outcome card at the top of a Done ticket: headline, the document's sentence, why it is Done, and links."""
    links = "".join(f'<a href="https://github.com/{esc(repo)}/pull/{int(n)}" rel="noopener noreferrer" target="_blank" '
                    f'aria-label="Pull request {int(n)}, opens GitHub">PR #{int(n)} ↗</a>' for n in o["prs"]) if views.REPO.match(repo) else ""
    if o["stage"] in views.DOC_NOUN:
        links += f'<a href="{views.doc_url(repo, issue, o["stage"])}">Read the full report</a>'
    when = f'<span>{esc(time.strftime("%-d %b %Y, %H:%M", time.localtime(at)))}</span>' if at else ""
    sentence = f'<p class="sd-outsent">{esc(o["sentence"])}</p>' if o["sentence"] else ""
    return (f'<section class="sd-card sd-outcome {esc(o["tone"])}" aria-labelledby="out-h"><h3 id="out-h" class="lab">Outcome</h3>'
            f'<b class="sd-outhead">{esc(o["headline"])}</b>{sentence}<p class="sd-why muted">{esc(o["reason"])}</p>'
            f'<div class="sd-outlinks muted sd-fine">{when}{links}</div></section>')


def _idle_why(at) -> str:
    return f'{LABEL.get(at or "", "The last step")} finished; choose the next step'


def row_for(db, repo: str, issue: int, needs_rows=None, titles: dict | None = None, now: float | None = None) -> dict:
    """One ticket's row, for a ticket the list does not hold (an old one, or one filtered out)."""
    now = time.time() if now is None else now
    need = next((r for r in needs_rows or [] if r["repo"] == repo and r["issue"] == issue), None)
    try:
        prs = [p for p in dbm.watched_prs(db, 500) if p["issue_repo"] == repo and p["issue_num"] == issue]
        dec = next((x for x in dbm.tickets(db, 500) if x["repo"] == repo and x["issue"] == issue), {})
    except Exception:
        prs, dec = [], {}
    return _one(db, {"repo": repo, "issue": issue, "title": dec.get("title"), "detail": dec.get("detail") or "", "outcome": dec.get("outcome") or "",
                     "decided_at": 0}, prs, need, (titles or {}).get((repo, issue)), now,
                needs_rows is not None)


def ticket_extras(db, repo: str, issue: int, j: dict) -> tuple[list, list, list, bool]:
    """(design preview files, the stages with a stored document, the ticket's recent events, whether it has images) for its detail."""
    design_runs = [s["run_id"] for s in j["steps"] if s["kind"] == "run" and s.get("stage") == "designer"]
    files = [f for fs in dbm.design_files_for_runs(db, design_runs).values() for f in fs] if design_runs else []
    widths = dbm.mockup_widths(db, files)
    files = [{**f, "width": widths.get((f["repo"], f["path"]), 0)} for f in files]
    try:
        events = [dict(zip(("ts", "kind", "message"), x)) for x in
                  db.execute("SELECT ts, kind, message FROM events WHERE repo=? AND issue=? ORDER BY id DESC LIMIT 60", (repo, issue))]
    except Exception:
        events = []
    runs = [s["run_id"] for s in j["steps"] if s["kind"] == "run"]
    images = any(dbm.run_images(db, i) for i in runs) or any(f["path"].endswith(".png") for f in files)
    return files, dbm.doc_stages(db, repo, issue), events, images


def _why(state: str, j: dict, prs: list, need, t: dict, at, now: float) -> str:
    runs = [s for s in j["steps"] if s["kind"] == "run"]
    if state == "new":
        return "Nothing has run yet"
    if state == "needs":
        w = j.get("waiting") or {}
        if w.get("pending"):
            return f'{LABEL.get(w.get("stage"), "A stage")} asked {int(w["pending"])} question{"s" if int(w["pending"]) != 1 else ""}'
        return plain((need or {}).get("reason") or w.get("message") or "") or "Waiting for a person"
    if state == "working" and runs:
        r = next((s for s in runs if s["state"] in ("running", "queued")), runs[-1])
        return f'{LABEL.get(at or "", "A stage")} running, {secs(r["seconds"])} in' if r["seconds"] else f'{LABEL.get(at or "", "A stage")} queued'
    if state == "failed":
        f = [s for s in j["steps"] if s["state"] == "failed"]
        bad = [p for p in prs if p.get("status") == "failed"]
        if bad:
            return f'PR #{int(bad[0]["number"])}: CI failing, {views.pr_round_line(bad[0], 2).lower()}'
        fails = [c for c in ci_results(j).values() if c["failing"]]
        last = max(fails, key=lambda c: c["ts"]) if fails else None
        if last and at == "ci":                 # its pull requests are closed: the last checks that failed say why
            return f'CI failing on #{last["number"]}: ' + _clip(", ".join(_short_check(c) for c in last["checks"]), 110)
        why = (f[-1].get("message") or "") if f else ""
        return f'{LABEL.get(at or "", "A step")} failed' + (f": {_clip(plain(why), 90)}" if why else "")
    if state == "prs" and prs:
        p = next(p for p in prs if (p.get("status") or "") != "closed")
        return f'PR #{int(p["number"])} · {CI_WORD.get(p.get("status") or "", "checks pending")}'
    detail = plain((t.get("detail") or "").rsplit(";", 1)[-1])
    return detail[:90] or ("Merged" if prs else "Finished")


CI_MSG = re.compile(r"^([\w.-]+/[\w.-]+)#(\d+): (failing|passed)(?:: (.*))?$", re.S)


def ci_results(j: dict) -> dict:
    """(repo, number) -> {repo, number, failing, checks, ts}: each pull request's latest CI result on the journey."""
    out: dict = {}
    for s in j.get("steps") or []:
        m = CI_MSG.match(s.get("message") or "") if s["kind"] == "ci" else None
        if m:
            out[(m.group(1), int(m.group(2)))] = {"repo": m.group(1), "number": int(m.group(2)), "failing": m.group(3) == "failing",
                                                  "checks": [c.strip() for c in (m.group(4) or "").split(", ") if c.strip()] if m.group(3) == "failing" else [],
                                                  "ts": s["started"]}
    return out


def _short_check(name: str) -> str:
    """A check without its workflow name ("CI / lint" -> "lint"), for one-line summaries."""
    return name.split(" / ", 1)[-1]


def _clip(text: str, n: int) -> str:
    """At most n characters, cut at a word with an ellipsis."""
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0].rstrip(" ,·:") + "…"


def _open_pr(r: dict) -> bool:
    return any((p.get("status") or "") != "closed" for p in r["prs"])


_TAG = re.compile(r"\s*\(?cls=[\w/.=,:-]*\)?|\s*\[dry-run\]")
_SAYS = (("human: classifier flagged needs_human", "The classifier asked for a person"), ("needs a person: ", ""), ("human: ", ""))


REPO_START = re.compile(r"^[\w.-]+/[\w.-]+#\d")


def plain(msg: str) -> str:
    """A decision or event message without the classifier's internal tag (cls=kind/complexity/human=.../conf=...), in words."""
    out = re.sub(r"([:;])\s*[:;]", r"\1", _TAG.sub("", msg or "")).strip(" ;:")
    for raw, said in _SAYS:
        if out.startswith(raw):
            out = said + out[len(raw):]
    out = out.strip(" ;:")
    if REPO_START.match(out):                   # a repository name keeps its case
        return out
    return out[:1].upper() + out[1:] if out else ""


def counts(rows: list[dict]) -> dict:
    """Each ticket counts once, in its state; PRs & CI counts every ticket with an open pull request, whatever else it waits for."""
    c = {k: 0 for k, _, _ in FILTERS}
    for r in rows:
        c[r["state"]] += 1
    c["prs"] = sum(_open_pr(r) for r in rows)
    c["all"] = len(rows)
    return c


def pick(rows: list[dict], flt: str, at: str = "", q: str = "", order: str = "latest") -> list[dict]:
    out = [r for r in rows if flt in ("", "all") or r["state"] == flt or (flt == "prs" and _open_pr(r))]
    if at in LABEL:
        out = [r for r in out if r["at"] == at]
    q = q.strip().lstrip("#").lower()
    if q.isdigit():                                              # a number is that ticket, never every ticket whose number contains it
        out = [r for r in out if str(r["issue"]) == q]
    elif q:
        out = [r for r in out if q in r["title"].lower() or q in f'{r["repo"]}#{r["issue"]}'.lower()]
    if order == "oldest":
        out.sort(key=lambda r: (ORDER[r["state"]], r["when"]))
    elif order == "priority":
        out.sort(key=lambda r: (plan.RANK.get(r.get("priority"), 1), ORDER[r["state"]], -r["when"]))
    return out


PIN_ICON = '<svg class="pr-pin" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 17v5M9 10.8V4h6v6.8l3 3.2H6z"/></svg>'
PRIO_WORD = {"high": "High", "normal": "Normal", "low": "Low"}


def prio_chip(priority: str, source: str) -> str:
    """A ticket's priority, when someone set one; a pin when a person did. Nothing for a normal priority nobody chose."""
    if source != "you" and priority == "normal":
        return ""
    who = {"you": "pinned by you", "pm": "set by the project manager", "label": "from a priority label"}.get(source, "")
    return (f'<span class="pr-chip {esc(priority)}{" mine" if source == "you" else ""}" title="{esc(PRIO_WORD[priority] + " priority, " + who)}">'
            f'{PIN_ICON if source == "you" else ""}{esc(PRIO_WORD[priority])}<span class="sr"> priority, {esc(who)}</span></span>')


# ---------------------------------------------------------------- the Tickets screen
def _qs(**kw) -> str:
    return urlencode({k: v for k, v in kw.items() if v})


def ticket_url(r: dict, flt: str = "", q: str = "", at: str = "", project: str = "") -> str:
    return "/ticket?" + _qs(repo=r["repo"], n=r["issue"], stage=flt, q=q, at=at, project=project)


STANDALONE = "_standalone"          # not a valid project name (names are letters, digits, spaces, dot, dash), so it never collides


def project_options(cfg) -> list[tuple[str, str]]:
    """The switcher's choices after "All projects": each configured project, then Standalone when a repo is in none."""
    if not cfg.projects:
        return []
    grouped = {r.repo for p in cfg.projects for r in p.repos}
    opts = [(p.name, p.name) for p in cfg.projects]
    return opts + ([(STANDALONE, "Standalone")] if any(r not in grouped for r in cfg.repos) else [])


def in_project(rows: list[dict], cfg, key: str) -> list[dict]:
    """The rows of one project (or of no project at all); an empty key keeps every row."""
    if not key:
        return rows
    grouped = {r.repo for p in cfg.projects for r in p.repos}
    if key == STANDALONE:
        return [r for r in rows if r["repo"] not in grouped]
    mine = {r.repo for p in cfg.projects if p.name == key for r in p.repos}
    return [r for r in rows if r["repo"] in mine]


def list_html(rows: list[dict], sel, flt: str, q: str, at: str, now: float, csrf: str = "", project: str = "") -> str:
    if not rows and not q.strip() and not at:
        head, sub = ("Nothing needs you right now.", "Every local ticket is either working or done.") if flt == "needs" else ("No tickets here.", "")
        sub = f'<p class="muted">{sub}</p>' if sub else ""
        more = "" if flt in ("", "all") else f'<a class="btn secondary" href="/tickets?{esc(_qs(stage="all", project=project))}">Show all tickets</a>'
        return f'<div class="sd-empty sd-emptybox"><p><strong>{head}</strong></p>{sub}{more}</div>'
    if not rows:
        return '<p class="muted sd-empty">No tickets match. Pick another filter or clear the search.</p>'
    items = ""
    for r in rows[:60]:
        on = sel is not None and (r["repo"], r["issue"]) == sel
        out_line = f'<span class="sd-why muted">Outcome: {esc(r["outcome"])}</span>' if r.get("outcome") else ""
        items += (f'<a class="sd-pick{" on" if on else ""}" href="{esc(ticket_url(r, flt, q, at, project))}"{" aria-current=page" if on else ""}>'
                  f'<span class="sd-row"><span class="mono muted">{esc(views.ref(r["repo"], r["issue"], short=True))}</span>'
                  f'<span class="sd-word {TICKET_TONE[r["state"]]}">{esc(TICKET_WORD[r["state"]])}</span>{prio_chip(r.get("priority", "normal"), r.get("prio_src", ""))}'
                  f'<span class="mono muted sd-at" title="{esc("At " + LABEL[r["at"]] if r["at"] else "Not started")}">{esc(ago(r["when"], now) if r["when"] else "")}</span></span>'
                  f'<span class="sd-title">{esc(r["title"])}</span><span class="sd-why muted">{esc(r["why"])}</span>'
                  f"{out_line}{progress(r['stations'])}</a>")
        need = r.get("need") or {}
        if need.get("acts") and not need.get("st") and csrf:
            from . import labels as L
            items += f'<div class="sd-pickacts">{L.action_forms(r["repo"], r["issue"], need["acts"], csrf, "/tickets")}</div>'
    more = f'<p class="muted sd-more">Showing 60 of {len(rows)}. Narrow the list with a filter or the search.</p>' if len(rows) > 60 else ""
    return items + more


def filters_html(c: dict, flt: str, q: str, at: str, order: str, repos=(), project: str = "", projects=()) -> str:
    chips = ""
    for key, word, tone in FILTERS:
        on = (flt or "all") == key
        dot = f'<span class="sd-dot {tone}" aria-hidden="true"></span>' if tone else ""
        chips += (f'<a class="sd-chip{" on" if on else ""}" href="/tickets?{esc(_qs(stage=key, q=q, at=at, sort=order if order != "latest" else "", project=project))}"'
                  f'{" aria-current=page" if on else ""}>{dot}{esc(word)} <b class="mono">{int(c.get(key, 0))}</b></a>')
    opt = lambda v, w, cur: f'<option value="{esc(v)}"{" selected" if v == cur else ""}>{esc(w)}</option>'
    station_opts = opt("", "Any", at) + "".join(opt(sid, w, at) for sid, w in STATIONS)
    sort_opts = opt("latest", "Latest activity", order) + opt("oldest", "Oldest waiting", order) + opt("priority", "Priority", order)
    pick_project = (f'<label class="sd-lab sd-project">Project <select name="project" aria-label="Filter tickets by project">'
                    f'{opt("", "All projects", project)}{"".join(opt(v, w, project) for v, w in projects)}</select></label>') if projects else ""
    used = sum(1 for x in (project, at, order != "latest") if x)
    count = f' <b class="mono sd-fcount">{used}</b>' if used else ""
    return (f'<div class="sd-filters" role="search"><nav class="sd-chips" aria-label="Filter by state">{chips}</nav><span class="sd-grow"></span>'
            f'<form method="get" action="/tickets" class="sd-form" data-autosubmit>'
            f'<input type="hidden" name="stage" value="{esc(flt)}">'
            f'<label class="sd-search"><span class="sr">Search tickets</span><input type="search" name="q" value="{esc(q)}" placeholder="Search" title="Search by number or title"></label>'
            f'<details class="sd-menu sd-fmenu"><summary>Filters{count}{MORE_ICON}</summary><div class="sd-menul" role="group" aria-label="Filters">{pick_project}'
            f'<label class="sd-lab">Station <select name="at">{station_opts}</select></label>'
            f'<label class="sd-lab">Sort <select name="sort">{sort_opts}</select></label></div></details>'
            f'<button class="secondary sd-apply">Apply</button></form></div>')




def _bar(s: dict, t0: float, span: float) -> str:
    if s["seconds"] is None or span <= 0:
        return '<span class="muted">—</span>'
    x = max(0.0, min(99.0, (s["started"] - t0) / span * 100))
    w = max(1.0, min(100 - x, s["seconds"] / span * 100))
    return (f'<svg class="sd-bar" viewBox="0 0 100 10" preserveAspectRatio="none" aria-hidden="true"><rect class="sd-track" width="100" height="10"/>'
            f'<rect class="sd-fill {_STEP.get(s["state"], "done")}" x="{x:.2f}" width="{w:.2f}" height="10"/></svg>')


RUN_WORD = {"stage": "document posted", "pr": "pull request opened", "passed": "passed", "failed": "failed", "running": "running now",
            "no-change": "nothing to change", "rejected": "change rejected", "rate-limited": "waiting for the rate limit", "interrupted": "interrupted", "cancelled": "cancelled (ticket closed)"}


def _step_label(s: dict) -> tuple[str, str]:
    if s["kind"] == "run":
        at = _RUN_AT.get(s["station"] or "", "build")
        base = LABEL.get(at, "Run")
        if s.get("run_kind") == "fix":
            return "Build", "CI fix round"
        return base, RUN_WORD.get(str(s.get("status") or ""), str(s.get("status") or ""))
    if s["kind"] == "person":
        return ("Needs you", plain(s.get("message"))[:100]) if s["state"] == "waiting" else ("Asked a person", plain(s.get("message"))[:100])
    if s["kind"] == "ci":
        return "CI", plain(s.get("message"))[:100]
    return "Classify", plain(s.get("message"))[:100]


def steps_html(j: dict, repo: str, issue: int, docs=()) -> str:
    steps = j["steps"]
    if not steps:
        return ""
    t0 = steps[0]["started"]
    end = max([s["finished"] for s in steps if s["finished"] is not None] + [s["started"] + (s["seconds"] or 0) for s in steps])
    span = max(1.0, end - t0)
    rows = ""
    for s in steps:
        label, note = _step_label(s)
        if s.get("repeat", 1) > 1:
            note += f' · {int(s["repeat"])} times over {secs(s["finished"] - s["started"])}'
        tone = _STEP.get(s["state"], "done")
        agent = f'{s["model"]} · {s["effort"]}' if s["kind"] == "run" and s.get("model") else "—"
        run = f'<a href="/runs/{int(s["run_id"])}">#{int(s["run_id"])}</a>' if s["kind"] == "run" else "—"
        stage = s.get("stage") if s["kind"] == "run" and s.get("stage") in docs else None
        doc = f' · <a href="{views.doc_url(repo, issue, stage)}">Read {esc(views.DOC_NOUN[stage])}</a>' if stage else ""
        so_far = " so far" if s["state"] == "running" else ""
        cls = {"running": "live", "waiting": "waitrow", "queued": "dim"}.get(s["state"], "")
        rows += (f'<tr class="{cls}"><td data-l="#"><span class="sd-n {tone}">{int(s["n"])}</span></td>'
                 f'<td data-l="Step"><strong>{esc(label)}</strong><br><span class="muted sd-note">{esc(note)}</span>{doc}</td>'
                 f'<td data-l="Agent" class="mono muted">{esc(agent)}</td><td data-l="When and how long" class="sd-when">{_bar(s, t0, span)}</td>'
                 f'<td data-l="Time" class="mono num">{esc(secs(s["seconds"]))}{so_far}</td>'
                 f'<td data-l="Tokens in / out" class="mono num">{esc(tokens(s["tokens"]) if s["kind"] == "run" else "—")}</td>'
                 f'<td data-l="Run" class="mono num">{run}</td></tr>')
    heads = "".join(f"<th{' class=num' if h in ('Time', 'Tokens in / out', 'Run') else ''}>{esc(h)}</th>"
                    for h in ("#", "Step", "Agent", "When and how long", "Time", "Tokens in / out", "Run"))
    return (f'<section class="sd-card sd-steps" aria-label="Steps in order"><div class="scroll"><table class="stack sd-table"><thead><tr>{heads}</tr></thead>'
            f'<tbody>{rows}</tbody></table></div></section>')


PR_WORD = {"passed": "Checks passed", "failed": "Checks failing", "closed": "Closed"}


def _pr_name(p: dict, repo: str) -> str:
    return ("" if p["repo"] == repo else p["repo"].split("/")[-1] + " ") + f'#{int(p["number"])}'


def _pr_tone(status: str) -> str:
    return "done" if status in ("passed", "closed") else "fail" if status == "failed" else "run"


def prs_fold(prs: list[dict], fix_rounds: int, csrf: str = "", repo: str = "") -> str:
    """Pull requests folded to one row: each one's checks in a few words, and how many passed, fail or still run."""
    if not prs:
        return views.fold("prs", "Pull requests", "No pull request yet", prs_list(prs, fix_rounds, csrf, repo), head_id="pr-h")
    words = [f'{_pr_name(p, repo)} {("merged" if p.get("summary") == "merged" else PR_WORD.get(p.get("status") or "", "Checks running").lower())}' for p in prs]
    tally: dict = {}
    for p in prs:
        tally[_pr_tone(p.get("status") or "watching")] = tally.get(_pr_tone(p.get("status") or "watching"), 0) + 1
    meta = "".join(f'<span class="sd-word {t}">{n} {w}</span>' for t, w in (("done", "done"), ("run", "running"), ("fail", "failing")) if (n := tally.get(t)))
    return views.fold("prs", "Pull requests", esc(" · ".join(words)), prs_list(prs, fix_rounds, csrf, repo), meta, head_id="pr-h")




def prs_list(prs: list[dict], fix_rounds: int, csrf: str = "", repo: str = "") -> str:
    rows = ""
    for p in prs:
        status = p.get("status") or "watching"
        tone = _pr_tone(status)
        url = f'https://github.com/{p["repo"]}/pull/{int(p["number"])}'
        name = _pr_name(p, repo)
        link = f'<a class="mono" href="{esc(url)}" rel="noopener noreferrer" target="_blank">{esc(name)} ↗</a>' if views.REPO.match(p["repo"]) else esc(name)
        merge = (f'<form method="post" action="/prs/merge" class="inline">{views.csrf_field(csrf)}<input type="hidden" name="repo" value="{esc(p["repo"])}">'
                 f'<input type="hidden" name="n" value="{int(p["number"])}"><button aria-label="Merge pull request #{int(p["number"])}" '
                 'title="Merges this pull request on GitHub with a merge commit">Merge</button></form>'
                 if csrf and status == "passed" and views.REPO.match(p["repo"]) else "")
        rows += (f'<li class="sd-prrow">{link}<span class="sd-prt">{esc(p.get("title") or p["repo"])}</span>'
                 f'<span class="sd-word {tone}">{esc(PR_WORD.get(status, "Checks running"))}</span>'
                 f'<span class="muted">{esc(views.pr_round_line(p, fix_rounds))}</span>{merge}</li>')
    return (f'<ul class="sd-prs">{rows}</ul>' if rows else '<p class="muted sd-fine">No pull request yet. When a build opens one, it appears '
            'here with its checks, the fix rounds and the merge state.</p>')


def design_url(repo: str, issue: int) -> str:
    return "/ticket/design?" + _qs(repo=repo, n=issue)


def design_html(files: list[dict], repo: str, issue: int, docs, prs_link: str, imports: list[dict] | None = None,
                notes: list[dict] | None = None, csrf: str = "") -> str:
    """The designer's screens on the canvas the full design page and the Screens board share: pan, zoom and the notes as pins. A screen
    opens large in Review the screens; the Note tool drops a note on the canvas and comes back to the ticket."""
    from . import canvas as CV, review as RV
    shots = [f for f in files if views.mockup_ok(f)]
    linked = views.import_links(imports)
    if not shots and not linked:
        return ""
    read = f'<a href="{views.doc_url(repo, issue, "designer")}">Read design</a>' if "designer" in docs else ""
    body = ""
    if shots:
        imgs = [{"key": f"mockup:{f['repo']}:{f['path']}", "src": views.mockup_src(f), "label": f["path"]} for f in shots]
        widths = {f"mockup:{f['repo']}:{f['path']}": f.get("width", 0) for f in shots}
        keys = set(widths)
        opened = [(k, x) for k, x in CV.numbered(notes or []) if x["image"] in keys]
        n = len(shots)
        more = f'<a class="pz-open" href="{esc(design_url(repo, issue))}">{CV.OPEN_ICON}Open full canvas</a>'
        body = (CV.canvas("dz", "Design screens", RV.design_rows(imgs, widths, lambda k: RV.url(repo, issue, k)), opened,
                          CV.scope_hidden(repo, issue, csrf), "ticket", extra=more)
                + f'<p class="muted sd-fine pz-hint">{n} screen{"s" if n != 1 else ""}'
                + (f' · {len(opened)} open note{"" if len(opened) == 1 else "s"}' if opened else "")
                + '. Drag to move around, Ctrl and scroll or pinch to zoom. Click a screen to open it large and mark what should change.</p>')
    return (f'<section class="sd-card sd-design" aria-labelledby="d-h"><div class="sd-cardhead"><h3 id="d-h">Design output</h3>'
            f'<span class="sd-links">{read}{prs_link}</span></div>{body}{linked}</section>')


def design_fold(files: list[dict], repo: str, issue: int, docs, prs_link: str, imports: list[dict] | None = None,
                notes: list[dict] | None = None, csrf: str = "") -> str:
    """The design output folded to one row: how many screens, open notes and linked designs."""
    card = design_html(files, repo, issue, docs, prs_link, imports, notes, csrf)
    shots = sum(1 for f in files if views.mockup_ok(f))
    keys = {f"mockup:{f['repo']}:{f['path']}" for f in files if views.mockup_ok(f)}
    opened = sum(1 for x in notes or [] if x.get("image") in keys)
    linked = len(imports or [])
    parts = ([f'{shots} screen{"s" if shots != 1 else ""}'] if shots else []) + ([f'{opened} open note{"s" if opened != 1 else ""}'] if opened else []) \
        + ([f'{linked} linked design{"s" if linked != 1 else ""}'] if linked else []) + [f'Draft PR #{u.rsplit("/", 1)[-1]}' for u in sorted({f.get("pr") for f in files if f.get("pr")}) if views.GH_URL.match(u)]
    return views.fold("design", "Design output", esc(" · ".join(parts)), card, tone="fd-design")


def activity_html(events: list[dict], now: float) -> str:
    if not events:
        return ""
    groups: list[list[dict]] = []
    for e in events:                                   # newest first; the same message again and again is one line
        if groups and plain(groups[-1][0].get("message")) == plain(e.get("message")):
            groups[-1].append(e)
        else:
            groups.append([e])
    items = "".join(f'<li><span class="mono">{esc(ago(g[0]["ts"], now))}</span> · {esc(plain(g[0].get("message"))[:160])}'
                    + (f' <span class="muted">· {len(g)} times since {esc(ago(g[-1]["ts"], now))}</span>' if len(g) > 1 else "") + "</li>"
                    for g in groups[:8])
    return f'<section class="sd-activity" aria-labelledby="ev-h"><h3 id="ev-h">Activity</h3><ul>{items}</ul></section>'


def activity_fold(events: list[dict], now: float) -> str:
    """The activity folded to one row: the newest event and how many there are."""
    if not events:
        return ""
    newest = events[0]
    summary = f'{esc(ago(newest["ts"], now))} · {esc(views.clip(plain(newest.get("message")), 140))}'
    return views.fold("activity", "Activity", summary, activity_html(events, now), f'<span class="mono muted">{len(events)} event{"s" if len(events) != 1 else ""}</span>')


def steps_fold(j: dict, repo: str, issue: int, docs=()) -> str:
    """The step table folded to one row: the step that runs (or waits) now, else the last one, and the totals."""
    steps = j["steps"]
    if not steps:
        return ""
    now_s = next((s for s in reversed(steps) if s["state"] in ("running", "waiting")), None)
    s = now_s or steps[-1]
    label, note = _step_label(s)
    head = "Now" if now_s else "Last"
    summary = esc(f'{head}: {label}' + (f', {note}' if note else "") + (f' · {secs(s["seconds"])}' if secs(s["seconds"]) != "—" else ""))
    runs = sum(1 for x in steps if x["kind"] == "run")
    meta = f'<span class="mono muted">{len(steps)} step{"s" if len(steps) != 1 else ""} · {runs} run{"s" if runs != 1 else ""}</span>'
    return views.fold("steps", "Steps", summary, steps_html(j, repo, issue, docs), meta)


def journey_line(r: dict) -> str:
    """Where the ticket is and its numbers, in one line: the station, time, tokens, runs and models."""
    j, at = r["journey"], r.get("at")
    tin, tout = j["tokens"]["in"], j["tokens"]["out"]
    runs = sum(1 for s in j["steps"] if s["kind"] == "run")
    parts = [f'At {LABEL[at]}' if at else "Not started"]
    parts += [secs(j["seconds"]) + (" so far" if j["status"] == "running" else "")] if secs(j["seconds"]) != "—" else []
    parts += [f'{tok((tin or 0) + (tout or 0))} tokens'] if tin is not None or tout is not None else []
    parts += [f'{runs} run{"" if runs == 1 else "s"}' + (f' on {", ".join(sorted(j["models"]))}' if j["models"] else "")] if runs else []
    return " · ".join(parts)


def journey_fold(r: dict, files: list, docs, now: float) -> str:
    """The journey folded to one row: the ten stations as a bar and the numbers; open it for the floor's strip (a phone: the list)."""
    picker = r.get("picker") or ""
    body = (picker + f'<p class="muted sd-fine fd-note">The same stations as the Factory floor</p>{strip(r["stations"], r.get("verify"), now)}'
            + phone_journey(r, files, docs))
    summary = f'{progress(r["stations"])}<span class="mono fd-line">{esc(journey_line(r))}</span>'
    meta = '<span class="fd-hint">Send it to a stage</span>' if "<details" in picker else ""
    return views.fold("journey", "Journey", summary, body, meta, head_id="j-h", tone="fd-journey")


def phone_journey(r: dict, files: list, docs) -> str:
    """Phones: the ten stations in order, each with its time, agent, tokens and links; the mockups sit in the Designer step."""
    st, steps, repo, n = shown(r["stations"]), r["journey"]["steps"], r["repo"], r["issue"]
    by: dict = {}
    for s in steps:
        if s["kind"] == "run":
            by.setdefault(_RUN_AT.get(s["station"] or "", "build"), []).append(s)
    items = ""
    for i, (sid, label) in enumerate(STATIONS, 1):
        state, runs = st[sid], by.get(sid, [])
        took = sum(s["seconds"] or 0 for s in runs)
        time_s = "Not started" if state == "none" else "Skipped" if state == "skip" else secs(took) if took else ""
        note = {"wait": "needs you", "fail": "failed", "run": "running now"}.get(state, "")
        if runs:
            last = runs[-1]
            agent = f'{last["model"]} · {last["effort"]}' if last.get("model") else ""
            tk = tokens(last["tokens"]) if last["tokens"]["in"] is not None else ""
            stage = last.get("stage") if last.get("stage") in docs else None
            links = " · ".join([f'<a href="/runs/{int(x["run_id"])}">run #{int(x["run_id"])}</a>' for x in runs[-2:]]
                               + ([f'<a href="{views.doc_url(repo, n, stage)}">Read {esc(views.DOC_NOUN[stage])}</a>'] if stage else []))
            extra = (f'<div class="mono muted sd-fine">{esc(" · ".join(x for x in (agent, tk) if x))}</div>' if agent or tk else "") + (f'<div class="sd-fine">{links}</div>' if links else "")
        else:
            extra = ""
        if sid == "designer" and files:
            extra += (f'<div class="mockups">{"".join(views.mockup_img(f) for f in files)}</div>'
                      + (f'<div class="sd-fine"><a href="{esc(design_url(repo, n))}">Open full canvas →</a></div>' if any(views.mockup_ok(f) for f in files) else ""))
        items += (f'<li class="sd-pj {state}"><span class="sd-n {state}">{i}</span><div><div class="sd-row"><b class="sd-pjl">{esc(label)}</b>'
                  f'<span class="mono sd-fine">{esc(time_s)}</span></div>' + (f'<div class="sd-word {state}">{esc(note)}</div>' if note else "") + f'{extra}</div></li>')
    return f'<section class="sd-phj" aria-labelledby="pj-h"><h3 id="pj-h" class="lab">Journey</h3><ol>{items}</ol></section>'




def live_part(r: dict, files: list, docs, events, fix_rounds: int, now: float, csrf: str = "", needs_html: str = "") -> str:
    """Everything under the ticket's header, in the order a person needs it: Journey, what needs them, status, pull requests, then
    the detail. It changes while the ticket runs (refreshed in place on the ticket's own page); needs_html rides along."""
    repo, n, j = r["repo"], r["issue"], r["journey"]
    design_prs = sorted({f.get("pr") for f in files if f.get("pr")})
    design_link = "".join(f'<a href="{esc(u)}" rel="noopener noreferrer" target="_blank">Draft PR #{esc(u.rsplit("/", 1)[-1])} ↗</a>' for u in design_prs if views.GH_URL.match(u))
    card = outcome_card(r["outcome_full"], repo, n, r.get("when") or 0) if r["state"] == "done" and r.get("outcome_full") else ""
    return (card + needs_html + journey_fold(r, files, docs, now) + prs_fold(r["prs"], fix_rounds, csrf, repo)
            + design_fold(files, repo, n, docs, design_link, r.get("design_imports"), r.get("review_notes"), csrf) + steps_fold(j, repo, n, docs)
            + activity_fold(events, now))


X_ICON = '<svg class="sd-x" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18"/></svg>'
OK_ICON = '<svg class="sd-x" viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12l5 5L20 7"/></svg>'


def failed_card(r: dict, fix_rounds: int, now: float, actions: str = "", latest: dict | None = None) -> str:
    """A failed ticket's first card: what failed (each pull request's failing checks, in full), how the fix rounds ended, the
    factory's latest word on it and what a person can do next. latest: {author, body, at} of the newest factory comment."""
    j, prs, repo = r["journey"], r["prs"], r["repo"]
    results = ci_results(j)
    failed = [s for s in j["steps"] if s["state"] == "failed"]
    at = r.get("at")
    if at == "ci" or any(p.get("status") == "failed" for p in prs):
        used = max([int(p.get("rounds") or 0) for p in prs], default=0)
        head = (f'CI kept failing after {used} of {fix_rounds} fix round{"" if fix_rounds == 1 else "s"}.' if used
                else "CI failed and no fix round ran.")
    else:
        head = f'{LABEL.get(at or "", "A step")} failed.'
    when = max([s["started"] for s in failed] or [0])
    head += f" The factory stopped {ago(when, now)}." if when else ""
    rows = ""
    known = {(p["repo"], int(p["number"])) for p in prs}
    seen = prs + [{"repo": k[0], "number": k[1], "status": "failed" if c["failing"] else "passed", "rounds": 0, "summary": ""}
                  for k, c in results.items() if k not in known]           # a CI result for a pull request the factory no longer tracks
    seen.sort(key=lambda p: not (results.get((p["repo"], int(p["number"]))) or {}).get("failing"))    # what still fails first
    for p in seen:
        res = results.get((p["repo"], int(p["number"])))
        status = p.get("status") or ""
        word = "Merged" if p.get("summary") == "merged" else {"failed": "Checks failing", "closed": "Closed", "passed": "Checks passed"}.get(status, "Checks running")
        name = ("" if p["repo"] == repo else p["repo"].split("/")[-1] + " ") + f'#{int(p["number"])}'
        url = f'https://github.com/{p["repo"]}/pull/{int(p["number"])}'
        link = (f'<a class="mono" href="{esc(url)}" rel="noopener noreferrer" target="_blank">{esc(name)} ↗</a>' if views.REPO.match(p["repo"])
                else f'<span class="mono">{esc(name)}</span>')
        checks = res["checks"] if res and res["failing"] else ([c.strip() for c in p["summary"][len("failing: "):].split(", ")]
                                                                if (p.get("summary") or "").startswith("failing: ") else [])
        if checks:
            chips = "".join(f'<span class="sd-check bad">{X_ICON}{esc(c)}</span>' for c in checks)
        elif res and not res["failing"]:
            chips = f'<span class="sd-check good">{OK_ICON}Checks passed{" after the fix" if int(p.get("rounds") or 0) else ""}</span>'
        else:
            chips = ""
        rows += (f'<li class="sd-wwpr"><div class="sd-wwhead">{link}<span class="sd-word {"fail" if status == "failed" else "none"}">{esc(word)}</span>'
                 f'<span class="muted">{esc(rounds_used(p, fix_rounds))}</span></div>' + (f'<div class="sd-checks">{chips}</div>' if chips else "") + "</li>")
    if not rows and failed:
        s = failed[-1]
        label, _ = _step_label(s)
        run = f' <a href="/runs/{int(s["run_id"])}">Open run #{int(s["run_id"])}</a>' if s["kind"] == "run" else ""
        rows = f'<li class="sd-wwpr"><div class="sd-wwhead"><strong>{esc(label)}</strong>{run}</div><p class="sd-wwmsg">{esc(plain(s.get("message")) or "No message was recorded.")}</p></li>'
    note = ""
    if prs and all((p.get("status") or "") == "closed" for p in prs) and not any(p.get("summary") == "merged" for p in prs):
        note = (f'<p class="sd-wwnote">{"Both pull requests are" if len(prs) == 2 else "The pull request is" if len(prs) == 1 else "The pull requests are"} '
                'closed without being merged, so the factory no longer watches their checks.</p>')
    said = (f'<div class="sd-wwsaid"><span class="lab">Latest from the factory · {esc(ago(latest["at"], now))}</span>'
            f'<p>{esc(_clip(" ".join(latest["body"].split()), 400))}</p></div>') if latest and latest.get("body") else ""
    return (f'<section class="sd-card sd-ww" aria-labelledby="ww-h"><div class="sd-wwtop"><h3 id="ww-h">What went wrong</h3>'
            f'<span>{esc(head)}</span></div><div class="sd-wwbody">' + (f'<ul class="sd-wwprs">{rows}</ul>' if rows else "")
            + note + said + (f'<div class="sd-acts sd-wwacts">{actions}</div>' if actions else "") + "</div></section>")


def rounds_used(p: dict, limit: int) -> str:
    n = int(p.get("rounds") or 0)
    return f'{n} of {limit} fix round{"" if limit == 1 else "s"} used' if n else "no fix round used"






MORE_ICON = '<svg class="sd-mi" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 9l6 6 6-6"/></svg>'


def ticket_links(r: dict, files: list[dict], docs, images: bool) -> tuple[str, str]:
    """(the links shown in the ticket's header, the rest for its More menu): GitHub and the first pull request stay in sight."""
    repo, n = r["repo"], r["issue"]
    ext = ' rel="noopener noreferrer" target="_blank"'
    prs = [f'<a href="https://github.com/{esc(p["repo"])}/pull/{int(p["number"])}"{ext}>PR #{int(p["number"])} ↗</a>'
           for p in r["prs"] if views.REPO.match(p["repo"])]
    prs += [f'<a href="{esc(u)}"{ext}>Draft PR #{esc(u.rsplit("/", 1)[-1])} ↗</a>'
            for u in sorted({f.get("pr") for f in files if f.get("pr")}) if views.GH_URL.match(u)]
    shown = ([] if is_local(int(n)) else [f'<a href="{esc(f"https://github.com/{repo}/issues/{n}")}"{ext}>Open on GitHub ↗</a>']) + prs[:1]
    more = (prs[1:] + [f'<a href="/labels/issue?{esc(_qs(repo=repo, n=n))}">Edit labels</a>']
            + [f'<a href="{views.doc_url(repo, n, s)}">Read {esc(views.DOC_NOUN[s])}</a>' for s in docs]
            + ([f'<a href="/ticket/images?{esc(_qs(repo=repo, n=n))}">View images</a>',
                f'<a href="/ticket/review?{esc(_qs(repo=repo, n=n))}">Review the screens</a>'] if images else []))
    return "".join(shown), "".join(more)


def more_menu(links: str, label: str = "More") -> str:
    """A small menu of links under a button, for what a person needs now and then."""
    if not links:
        return ""
    return (f'<details class="sd-menu"><summary>{esc(label)}{MORE_ICON}</summary>'
            f'<div class="sd-menul" role="group" aria-label="{esc(label)}">{links}</div></details>')


FOLD_ALL = ('<div class="sd-foldall"><button type="button" class="link" data-fold-all="1">Expand all</button>'
            '<button type="button" class="link" data-fold-all="0">Collapse all</button></div>')


def detail_html(r: dict, needs_html: str, files: list[dict], docs, events, fix_rounds: int, now: float, live: bool, images: bool = False,
                local_html: str = "", handling: str = "", close_html: str = "", csrf: str = "", failed_html: str = "", move: str = "") -> str:
    """local_html: a local ticket's own parts (description, comments, chat, priority…), folded; it has no GitHub page to link to.
    close_html: Close for a GitHub ticket (a local ticket's card has its own). handling: how the settings apply to this ticket
    (features.ticket_handling). move: the Start or move menu (kanban.menu), always at hand in the header.
    Everything but what needs a person and what went wrong is folded to one row that says what is inside."""
    repo, n, j = r["repo"], r["issue"], r["journey"]
    shown, more = ticket_links(r, files, docs, images)
    if r["state"] == "failed":
        # what went wrong first; then the journey with its numbers in one line; the rest folded
        design_link = "".join(f'<a href="{esc(u)}" rel="noopener noreferrer" target="_blank">Draft PR #{esc(u.rsplit("/", 1)[-1])} ↗</a>'
                              for u in sorted({f.get("pr") for f in files if f.get("pr")}) if views.GH_URL.match(u))
        body = ((failed_html or failed_card(r, fix_rounds, now)) + needs_html + journey_fold(r, files, docs, now)
                + design_fold(files, repo, n, docs, design_link, r.get("design_imports"), r.get("review_notes"), csrf)
                + steps_fold(j, repo, n, docs) + activity_fold(events, now))
    else:
        body = live_part(r, files, docs, events, fix_rounds, now, csrf, needs_html)
    if live and j["status"] in ("running", "waiting", "queued"):
        body = f'<div id="live" data-src="/fragment/ticket">{body}</div>'
    return (f'<article class="sd-detail" aria-label="Ticket {esc(display(int(n)))}">'
            f'<div class="sd-dhead"><div class="sd-dtitle"><div class="sd-row"><span class="mono muted">{esc(views.ref(repo, n))}</span>'
            f'<span class="sd-word {TICKET_TONE[r["state"]]}">{esc(TICKET_WORD[r["state"]])}</span>{prio_chip(r.get("priority", "normal"), r.get("prio_src", ""))}</div>'
            f'<h2>{esc(r["title"])}</h2></div><div class="sd-links">{move}{shown}{more_menu(more)}</div></div>'
            + FOLD_ALL + '<div class="sd-folds">' + body + local_html + handling + "</div>"
            + (f'<div class="sd-acts">{close_html}</div>' if close_html else "") + "</article>")


SUMMARY_LINE = re.compile(r"^\s*(?:\*\*)?Summary:?(?:\*\*)?:?\s*(.+)$", re.I | re.M)


def doc_gist(text: str) -> str:
    """One or two plain sentences from a stage document: its own `Summary:` line, or (older documents) the first prose line."""
    head = "\n".join(text.splitlines()[:12])
    m = SUMMARY_LINE.search(head)
    if m:
        return plain(m.group(1))[:400]
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith(("#", "```", "-", "|", ">", "*")):
            return plain(line)[:300]
    return ""


def summary_card(db, repo: str, issue: int, docs, files: list[dict]) -> str:
    """What the stages that already ran concluded, one line each, so a person can decide Build or Skip without reading the documents."""
    rows, first = "", ""
    for stage in docs:
        d = dbm.stage_doc(db, repo, issue, stage)
        gist = doc_gist(d["output"]) if d else ""
        if not gist:
            continue
        first = first or f'{LABEL.get(stage, stage.title())}: {gist}'
        extra = ""
        if stage == "designer":
            n = sum(1 for f in files if f.get("preview"))
            extra = (f' <span class="muted">{n} mockup image{"s" if n != 1 else ""} below.</span>' if n else
                     ' <span class="muted">No mockup image was made.</span>')
        rows += (f'<li><b>{esc(LABEL.get(stage, stage.title()))}</b> {esc(gist)}{extra} '
                 f'<a href="{views.doc_url(repo, issue, stage)}">Read {esc(views.DOC_NOUN[stage])}</a></li>')
    if not rows:
        return ""
    n = rows.count("<li>")
    return views.fold("found", "What the stages found", esc(views.clip(first, 160)), f'<ul class="sd-sumlist">{rows}</ul>',
                      f'<span class="mono muted">{n} stage{"s" if n != 1 else ""}</span>', head_id="sum-h")


def why_part(row: dict) -> tuple[str, str]:
    """(heading, body html) for a decision a person must make: the classifier's scores when they were recorded, else the old short reason."""
    d = row.get("scores")
    if not d:
        return row.get("reason") or "A decision needs a person", '<p class="muted">Decided before scores were recorded. The scores for this decision are not available.</p>'
    if d["reason"] != "low":
        return "Needs a person", f'<p class="muted">{esc(W.SENTENCES[d["reason"]])}</p>'
    th, head = d["threshold"], "Needs a person: low confidence"
    if d["missing"]:
        which = " and ".join(d["missing"])
        return head, (f'<p class="muted">The labels-only classifier is in use and the {esc(which)} label is missing (fixed confidence {d["confidence"]:.2f}, '
                      f'threshold {th:.2f}). Add a {esc(which)} label to this ticket, or choose below.</p>')
    weak, rows = W.weakest(d), ""
    for key, name in W.ROWS:
        if key not in d["answers"]:
            continue
        v = d["answers"][key]
        if v is None:
            rows += f'<tr><td data-label="Answer">{name}</td><td data-label="Choice">Set by label</td><td data-label="Score" class="mono">-</td><td data-label="Against {th:.2f}" class="muted">Not scored</td></tr>'
            continue
        below = v[1] < th
        verdict = f'<span style="color:var(--warn)">Below by {th - v[1]:.2f}</span>' if below else '<span style="color:var(--good)">Above</span>'
        label = f"<b>{name} (weakest)</b>" if key == weak else name
        rows += (f'<tr class="{"sd-weak" if key == weak else ""}"><td data-label="Answer">{label}</td>'
                 f'<td data-label="Choice">{esc(v[0])}</td><td data-label="Score" class="mono">{v[1]:.2f}</td>'
                 f'<td data-label="Against {th:.2f}">{verdict}</td></tr>')
    src = "labels-only" if d["source"] == "labels" else "Jev"
    return head, (f'<p class="muted">Confidence {d["confidence"]:.2f}, below the threshold of {th:.2f}.</p><div class="scroll"><table class="stack sd-table">'
                  f'<caption class="sr">Classifier answers and scores</caption><thead><tr><th scope="col">Answer</th><th scope="col">Choice</th>'
                  f'<th scope="col">Score</th><th scope="col">Against {th:.2f}</th></tr></thead><tbody>{rows}</tbody></table></div>'
                  f'<p class="muted sd-fine">Scored by the {src} classifier. The overall score is the lowest of these.</p>')


def needs_card(row: dict | None, csrf: str, back: str) -> str:
    """What the ticket needs from a person. Questions: each one a person must answer, its options with the recommendation marked
    (and chosen to start with), Accept recommendations, and the ones answered with safe defaults folded away. It posts the same
    fields as labels.question_form, so the server checks them the same way. A decision: the same buttons as everywhere else."""
    if not row:
        return ""
    from . import labels as L
    repo, n, st = row["repo"], int(row["issue"]), row.get("st")
    if not st:
        acts = L.action_forms(repo, n, row.get("acts") or [], csrf, back)
        head, why = why_part(row)
        return (f'<section class="sd-card sd-needs" aria-labelledby="nq-h"><div class="sd-cardhead"><h3 id="nq-h">{esc(head)}</h3></div>'
                f'{why}<div class="sd-acts">{acts}</div></section>')
    pend = st.pending()
    pend_ids = {q.id for q in pend}
    hidden = (f'{views.csrf_field(csrf)}<input type="hidden" name="repo" value="{esc(repo)}"><input type="hidden" name="n" value="{n}">'
              f'<input type="hidden" name="stage" value="{esc(st.stage)}"><input type="hidden" name="back" value="{esc(back)}">')
    blocks = ""
    for q in pend:
        opts = "".join(f'<label class="sd-opt{" rec" if o == q.recommended else ""}"><input type="radio" name="a_{esc(q.id)}" value="{esc(o)}"'
                       f'{" checked" if o == q.recommended else ""}><span>{esc(lab)}</span>'
                       + ('<span class="mono sd-rec">recommended</span>' if o == q.recommended else "") + "</label>" for o, lab in q.options)
        blocks += (f'<fieldset class="sd-q" data-q="open"><legend>{esc(q.text)}</legend>{opts}'
                   f'<span class="muted sd-fine">{esc(q.reason)}</span>'
                   f'<details class="sd-own"><summary>Write your own answer</summary><input name="x_{esc(q.id)}" maxlength="500" aria-label="Your own answer"></details></fieldset>')
    safe = [q for q in st.questions if q.id not in pend_ids]
    folded = ""
    if safe:
        said = " ".join(f'{esc(q.text.rstrip("?"))}: <b class="sd-said">{esc(Q.describe(q, st.answers[q.id], st.auto.get(q.id, "")) if q.id in st.answers else q.label(q.recommended))}</b>.' for q in safe)
        folded = (f'<details class="sd-safe"><summary>{len(safe)} answered with safe defaults</summary><p class="muted">{said} '
                  f'<a href="{views.doc_url(repo, n, st.stage)}">Change an assumption</a></p></details>')
    total = len(st.questions)
    head = f"{len(pend)} of {total} {esc(views.DOC_NOUN.get(st.stage, st.stage))} question{'s' if total != 1 else ''} need a person"
    return (f'<section class="sd-card sd-needs" aria-labelledby="nq-h"><form method="post" action="/tickets/answer" class="nd-form sd-qform">{hidden}'
            f'<div class="sd-cardhead"><h3 id="nq-h">{head}</h3><button name="accept" value="1" formnovalidate>Accept recommendations</button></div>'
            f'{blocks}{folded}<div class="sd-acts"><button name="send" value="1" class="secondary">Send these answers</button>'
            '<span class="muted sd-fine">Answers are posted on the ticket as the factory\'s account. The next stage starts at the next poll.</span></div></form></section>')


def phone_needs(rows: list[dict], csrf: str) -> str:
    """The phone list starts with what needs you (the desktop has the chip and the open ticket for that)."""
    need = [r for r in rows if r["state"] == "needs"]
    if not need:
        return ""
    asking = [r for r in need if (r.get("need") or {}).get("st")]
    bulk = ""
    if asking and csrf:
        refs = ",".join(f'{r["repo"]}#{int(r["issue"])}' for r in asking[:20])
        bulk = (f'<form method="post" action="/tickets/answer-all">{views.csrf_field(csrf)}<input type="hidden" name="tickets" value="{esc(refs)}">'
                '<input type="hidden" name="back" value="/tickets"><button class="wide">Accept all recommendations</button></form>')
    what = " · ".join(f'{display(r["issue"])} {r["why"][:40]}' for r in need[:3])
    return (f'<section class="sd-card sd-needs sd-phneeds" aria-labelledby="pn-h"><h2 id="pn-h">{len(need)} ticket{"s" if len(need) != 1 else ""} need you</h2>'
            f'<p class="muted sd-fine">{esc(what)}</p>{bulk}</section>')


def _switch(project: str) -> str:
    from . import kanban                      # kanban builds on this module
    return kanban.switch("list", project)


DIALOG = re.compile(r"<noscript>.*?</noscript>|<dialog\b.*?</dialog>", re.S)


def tickets_page(rows: list[dict], sel_row: dict | None, explicit: bool, flt: str, q: str, at: str, order: str, detail: str, new_ticket: str, now: float,
                 csrf: str = "", settings: str = "", project: str = "", projects=(), unknown: bool = False, github: bool = True, more_new: str = "") -> str:
    """settings: the strip of settings that shape this page (features.tickets_strip). rows are already narrowed to project
    (a configured name or STANDALONE; "" is all); projects are the switcher's choices; unknown: the asked-for project is gone.
    more_new: the header's other ways in (Import, Review tickets, New project), behind a More button; their popups stay outside
    the menu, so closing the menu does not hide an open one."""
    popups = "".join(DIALOG.findall(more_new))
    more = more_menu(DIALOG.sub("", more_new)) + popups if more_new else ""
    shown = pick(rows, flt, at, q, order)
    sel = (sel_row["repo"], sel_row["issue"]) if sel_row else None
    back = f'<p class="sd-back"><a href="/tickets?{esc(_qs(stage=flt, q=q, at=at, project=project))}">← All tickets</a></p>'     # shown on a phone while a ticket is open
    note = ""
    if project in dict(projects):
        n = len(rows)
        what = "standalone repositories" if project == STANDALONE else project
        note = (f'<p class="muted sd-scope" role="status">Showing {esc(what)} · {n} ticket{"" if n == 1 else "s"} '
                f'<a href="/tickets?{esc(_qs(stage=flt, q=q, at=at))}">Show all projects</a></p>')
    elif unknown:
        note = '<p class="muted sd-scope" role="status">That project no longer exists. Showing all projects.</p>'
    if not github:
        note = ('<p class="muted sd-scope" role="status">GitHub issues are off. Showing local tickets and GitHub work that is still running. '
                '<a href="/settings">Turn on Work from GitHub issues</a></p>') + note
    # One row: the title, Board | List, the state chips, search and Filters, the page's settings and the New buttons.
    return (f'<div class="sd-page{" has-sel" if explicit else ""}">{back}<div class="sd-pagehead sd-topbar"><h1>Tickets</h1>{_switch(project)}'
            + filters_html(counts(rows), flt, q, at, order, project=project, projects=projects)
            + f'<div class="sd-barend">{settings}{new_ticket}{more}</div></div>' + phone_needs(rows, csrf) + note
            + f'<div class="sd-split"><section class="sd-list" aria-label="Ticket list">{list_html(shown, sel, flt, q, at, now, csrf, project)}</section>'
            + (detail or '<div class="sd-detail sd-none"><p class="muted">Pick a ticket to see its journey.</p></div>') + "</div>"
            + f'<template id="ld-detail"><div class="sd-detail sd-loading">{loader("Loading the ticket from GitHub")}</div></template></div>')


# ---------------------------------------------------------------- the Factory dashboard
SNAKE = ("poll", "classify", "route", "analyst", "designer", "architect", "build", "review", "ci", "pr")


def floor_stations(rows: list[dict]) -> dict:
    """{station: {"state", "refs", "count"}}: the open tickets at each station now."""
    out = {sid: {"state": "none", "refs": [], "count": 0} for sid, _ in STATIONS}
    rank = {"wait": 4, "run": 3, "fail": 2, "done": 1, "none": 0}
    for r in rows:
        if r["state"] == "done" or not r["at"]:
            continue
        o = out[r["at"]]
        o["count"] += 1
        o["refs"].append(f'#{r["issue"]}')
        here = r["stations"][r["at"]]
        if rank.get(here, 0) > rank[o["state"]]:
            o["state"] = here
    return out


def floor_order(cfg: dict) -> list[tuple[str, str, str]]:
    """The stations on the floor, in route order, from what the factory has: every stage role in the config, and review and CI only
    when they are on. (station id, label, icon path)."""
    roles = cfg.get("roles") or ["analyst", "designer", "architect"]
    ids = ["poll", "classify", "route", *roles, "build"] + (["review"] if cfg.get("review", True) else []) + (["ci"] if cfg.get("ci", "on") != "off" else []) + ["pr"]
    return [(sid, LABEL.get(sid, sid.replace("_", " ").title()), ICON.get(sid, yard.GENERIC_ICON)) for sid in ids]


def _floor_word(sid: str, o: dict) -> str:
    return "To merge" if sid == "pr" and o["state"] == "done" else {"none": "Idle"}.get(o["state"], STATE_WORD[o["state"]])


def floor_card(d: dict, rows: list[dict], now: float, csrf: str = "") -> str:
    """The floor: the plant (plant.py) and, for phones, the stations as a list."""
    from . import labels as L
    order = floor_order(d["cfg"])
    fl = floor_stations(rows)
    for sid, _, _ in order:
        fl.setdefault(sid, {"state": "none", "refs": [], "count": 0})
    roles = d["cfg"].get("roles") or ["analyst", "designer", "architect"]
    working = [r for r in rows if r["at"] == "build" and r["stations"].get("build") == "run"]
    running = {x.get("harness") for x in d.get("running") or []}
    last = d.get("status", {}).get("last_poll_ok", {}).get("value")
    extras = {"bypass": [f'#{r["issue"]}' for r in working if all(r["stations"].get(x, "none") == "none" for x in roles)],
              "fix": [f'#{r["issue"]}' for r in working if r["stations"].get("ci") == "fail"],
              "queued": [f'#{int(q["issue"])}' for q in d.get("queued") or []],
              "power": [{**h, "on": h["name"] in running} for h in d["cfg"].get("power") or []],
              "schedules": d.get("schedules") or [],
              "poll": {"every": d["cfg"].get("poll_seconds"), "last": float(last) if last else None},
              "flights": L.flights(now),
              "notify": [{"name": n, "on": bool((d["cfg"].get("notify") or {}).get(n))} for n in floorplan.NOTIFIERS],
              "server": d.get("server")}
    workers = d.get("workers") or []
    ctx = floor_ctx(d["cfg"], workers)
    plan, _ = floorplan.usable(*d["floor_layout"], ctx) if d.get("floor_layout") else (None, "")
    plan = plan or floorplan.default_plan(ctx)                       # no layout saved (or none usable): the default one, as the editor shows it
    svg = plant.floor_map(order, fl, workers, bool(d["cfg"].get("workers")), now, _floor_word,
                          lambda sid: f"/tickets?{_qs(stage='all', at=sid)}", extras, floorplan.compile_plan(plan, ctx) if plan else None)
    edit = '<a class="btn secondary fm-edit" href="/floor/edit">Edit layout</a>' if csrf else ""
    return snake(fl, [sid for sid, _, _ in order], svg, add_ticket(d["cfg"].get("repos") or [], csrf, roles, d["cfg"].get("attach_help") or "",
                                  tuple(d["cfg"].get("attach_limits") or (0, 0))) + edit)


def floor_ctx(cfg: dict, workers: list) -> "floorplan.Ctx":
    """What a floor layout has to hold for this config (see floorplan.py)."""
    shown = workers[:yard.MAX_WORKERS]
    return floorplan.Ctx([sid for sid, _, _ in floor_order(cfg)], [h["name"] for h in cfg.get("power") or []],
                         bool(cfg.get("workers")) or bool(workers), len(shown), workers=[w["name"] for w in shown])


def add_ticket(repos: list[str], csrf: str, roles: list[str], attach_help: str = "", limits: tuple = (0, 0)) -> str:
    """The floor's Add a ticket: the same form as Tickets' New ticket, back to the floor, where the ticket arrives by air."""
    from . import labels as L
    if not csrf or not repos:
        return ""
    opts = "".join(f'<option value="{esc(r)}">{esc(r)}</option>' for r in repos)
    multipart = ' enctype="multipart/form-data"' if attach_help else ""      # given only when local tickets are on; the floor form has no "where", so it files locally
    files = L.attach_field("Attachments (optional)", attach_help, *limits) if attach_help else ""
    form = (f'<form method="post" action="/tickets/create"{multipart} class="field">{views.csrf_field(csrf)}<input type="hidden" name="back" value="/">'
            f'<label>Repository<select name="repo">{opts}</select></label>'
            f'<label>Title<input name="title" required maxlength="{L.MAX_TITLE}"></label>'
            f'<label>Description (optional)<textarea name="body" rows="4" maxlength="{L.MAX_BODY}"></textarea></label>'
            f'{files}{L.start_field(roles)}'
            '<p class="muted">It is flown in from the mainland and lands at Receiving. It starts work only if you choose a Start action.</p>'
            '<button>Send by air</button></form>')
    return '<span class="fn-addt">' + L.ticket_dialog("fn-add-d", "Add a ticket", "Flown in from the mainland to Receiving.", form) + '</span>'


def snake(fl: dict, order=SNAKE, floor_map: str = "", add: str = "") -> str:
    """The floor: the stations joined by belts with crates. Given the map, the list is what phones show (one column, belts running
    down); without it the list is laid out in two rows (left to right, down, right to left)."""
    out = ""
    for i, sid in enumerate(order):
        o = fl[sid]
        word = _floor_word(sid, o)
        label = LABEL.get(sid, sid.replace("_", " ").title())
        out += (f'<a class="sd-mach p{i} {o["state"]}" href="/tickets?{esc(_qs(stage="all", at=sid))}" aria-label="{esc(label)}: {int(o["count"])} ticket(s), {esc(word)}">'
                f'<span class="sd-mtop">{_svg(sid) if sid in ICON else ""}<b class="mono">{int(o["count"])}</b></span><strong>{esc(label)}</strong>'
                f'<span class="sd-mstate">{esc(word)}</span><span class="mono muted sd-refs">{esc(" · ".join(o["refs"][:3]))}</span></a>')
        if i < len(order) - 1:
            nxt = fl[order[i + 1]]["state"]
            into = nxt if nxt != "none" else ("done" if o["state"] != "none" else "none")
            way = " down" if i == 4 else " left" if i > 4 else ""
            out += _belt(into, f" q{i}{way}")
    legend = "".join(f'<li><span class="sd-dot {k}" aria-hidden="true"></span>{esc(w)}</li>'
                     for k, w in (("done", "Finished"), ("run", "Running now"), ("wait", "Waiting for a person"), ("fail", "Failed"), ("none", "Nothing here")))
    if floor_map:
        if "fm-train" in floor_map:
            legend += '<li class="fm-key"><span class="sd-dot fm-train-key" aria-hidden="true"></span>A train to a worker and back; signals let one onto the shared track at a time</li>'
        legend += ('<li class="fm-key"><span class="sd-dot fm-drone-key" aria-hidden="true"></span>Drones: issues in from GitHub, pull requests out</li>')
    lst = f'<div class="sd-scroll{" fm-phone" if floor_map else ""}"><div class="sd-snake">{out}</div></div>'
    return (f'<section class="sd-card sd-floor" aria-labelledby="floor-h"><div class="sd-cardhead"><h2 id="floor-h">The floor</h2>'
            f'<span class="muted sd-fine">Pick a station to see the tickets at it, in Tickets.</span>{_time_buttons() if floor_map else ""}{add}</div>'
            + (f'<div class="sd-scroll fm-wrap">{floor_map}</div>' if floor_map else "") + f'{lst}<ul class="sd-legend">{legend}</ul></section>')


def _time_buttons() -> str:
    """The floor's time of day and weather (static/app.js): Auto cycles day, dusk and night, the others hold one; the weather is clear
    or rain, snow or fog over the floor. Shown only with the script."""
    group = lambda label, attr, first, opts: (
        f'<span class="fm-time" role="group" aria-label="{label}"><span class="lab">{label.split()[0]}</span>'
        + "".join(f'<button type="button" class="secondary" data-{attr}="{k}" aria-pressed="{"true" if k == first else "false"}">{n}</button>'
                  for k, n in opts) + '</span>')
    return (group("Time of day", "tod", "auto", (("auto", "Auto"), ("day", "Day"), ("dusk", "Dusk"), ("night", "Night")))
            + group("Weather", "wx", "clear", (("clear", "Clear"), ("rain", "Rain"), ("snow", "Snow"), ("fog", "Fog"))))


def _tile(href: str, lab: str, num, tone: str, sub: str, hot: bool = False) -> str:
    return (f'<a class="sd-tile link{" hot" if hot else ""}" href="{esc(href)}"><span class="lab">{esc(lab)}</span>'
            f'<b class="mono sd-num {tone}">{num}</b><span class="muted">{esc(sub)}</span></a>')


def usage_card(d: dict, now: float) -> str:
    import json
    from .. import usage
    try:
        u = json.loads(d["status"]["claude_usage"]["value"])
    except (KeyError, ValueError, TypeError):
        return ""
    bars = ""
    for _, label in usage.WINDOWS:
        w = u.get(label)
        if not w:
            continue
        pct = max(0, min(100, int(round(w["used"]))))
        bars += (f'<div class="sd-use"><span class="muted">This {esc(label)}</span><span class="mono">{pct}%</span></div>'
                 f'<meter class="sd-meter{" bad" if pct >= usage.LOW_PERCENT else ""}" min="0" max="100" value="{pct}" aria-label="Claude plan used this {esc(label)}">{pct}%</meter>')
    if not bars:
        return ""
    stale = '<p class="muted sd-fine">Could not refresh; these are the last known numbers.</p>' if u.get("error") else ""
    return f'<section class="sd-card" aria-labelledby="use-h"><h2 id="use-h">Claude plan usage</h2>{bars}{stale}</section>'


def running_card(d: dict, rows: list[dict], now: float) -> str:
    items = ""
    for run in d["running"][:4]:
        title = run.get("title") or f'#{run["issue"]}'
        at = LABEL.get(_RUN_AT.get(run.get("stage") or "", "") or ("build" if run["kind"] in ("build", "fix") else ""), "Run")
        items += (f'<div class="sd-runrow"><div class="sd-row"><a href="/ticket?{esc(_qs(repo=run["repo"], n=run["issue"]))}">{esc(title)}</a>'
                  f'<span class="mono muted">#{int(run["issue"])}</span></div><span class="muted sd-fine">{esc(at)} · {esc(run.get("model") or "")} · {esc(secs(now - run["started"]))} in</span>'
                  '<span class="sd-stripe" aria-hidden="true"></span></div>')
    for p in [p for p in d["prs"] if p["status"] == "watching"][:3]:
        items += (f'<div class="sd-runrow"><div class="sd-row"><a href="/ticket?{esc(_qs(repo=p["issue_repo"], n=p["issue_num"]))}">{esc(p.get("title") or p["repo"])}</a>'
                  f'<span class="mono muted">#{int(p["issue_num"])}</span></div><span class="muted sd-fine">CI · PR #{int(p["number"])} · checks running</span>'
                  '<span class="sd-stripe" aria-hidden="true"></span></div>')
    if not items:
        items = '<p class="muted sd-pad">Nothing is running. The factory picks up labelled tickets at its next poll.</p>'
    return f'<section class="sd-card sd-flush" aria-labelledby="run-h"><h2 id="run-h" class="sd-bar-h">Running now</h2>{items}</section>'


def needs_tray(needs, csrf: str) -> str:
    from . import labels as L
    titles = L.titles_cached()
    if needs is None:
        return ('<section class="sd-card sd-flush sd-needs" aria-labelledby="needs-h"><h2 id="needs-h" class="sd-bar-h">Needs you</h2>'
                '<p class="muted sd-pad">Save a GitHub token on the Credentials page to see the tickets waiting for you.</p></section>')
    asking = [r for r in needs if r.get("st")]
    bulk = ""
    if asking:
        refs = ",".join(f'{r["repo"]}#{int(r["issue"])}' for r in asking[:20])
        bulk = (f'<form method="post" action="/tickets/answer-all" class="inline">{views.csrf_field(csrf)}<input type="hidden" name="tickets" value="{esc(refs)}">'
                '<input type="hidden" name="back" value="/"><button class="blue">Accept all recommendations</button></form>')
    rows = ""
    for r in needs[:6]:
        ref = f'{r["repo"].split("/")[-1]}#{int(r["issue"])}'
        page = f'/ticket?{_qs(repo=r["repo"], n=r["issue"])}'
        st = r.get("st")
        if st:
            pend = len(st.pending())
            meta, why = f"{ref} · the {st.stage} asked {len(st.questions)} question{'s' if len(st.questions) != 1 else ''}", f"{pend} need a person"
            act = f'<a class="btn blue" href="{esc(page)}">Answer {pend} question{"s" if pend != 1 else ""}</a>'
        else:
            meta, why = ref + (f' · {r["kind"]}' if r.get("kind") else ""), r.get("reason") or "needs a person"
            act = L.action_forms(r["repo"], r["issue"], r.get("acts") or [], csrf, "/")
        rows += (f'<div class="sd-needrow"><div class="sd-grow"><span class="mono muted sd-fine">{esc(meta)}</span>'
                 f'<a class="sd-title" href="{esc(page)}">{esc(titles.get((r["repo"], r["issue"])) or r["title"])}</a><span class="muted sd-fine">{esc(why)}</span></div><div class="sd-acts">{act}</div></div>')
    if not rows:
        rows = '<p class="muted sd-pad">Nothing needs you. The factory will ask here, and on Telegram, when it needs a decision.</p>'
    return (f'<section class="sd-card sd-flush sd-needs" aria-labelledby="needs-h"><div class="sd-bar-h"><h2 id="needs-h">Needs you</h2>'
            f'<div class="sd-acts">{bulk}<a class="btn secondary" href="/tickets?stage=needs">Open in Tickets</a></div></div>{rows}</section>')


def dashboard(d: dict, rows: list[dict], needs, csrf: str, now: float, mode_bar: str = "") -> str:
    from . import floor
    cfg = d["cfg"]
    c = counts(rows)
    paused = d["paused"]
    state = f"Paused: {paused}" if paused else "running"
    line = f'{"Live" if cfg["live"] else "Dry run"} · {state} · polling every {int(cfg["poll_seconds"])} s'
    last = d["status"].get("last_poll_ok", {}).get("value")
    seen = f"last poll {ago(float(last), now)}" if last else "no poll yet"
    line += f" · Not reporting, {seen}" if floor._stale(d, now) else f" · Healthy, {seen}"
    open_prs = [p for p in d["prs"] if p["status"] != "closed"]
    checks = sum(p["status"] == "watching" for p in open_prs)
    failing = sum(p["status"] == "failed" for p in open_prs)
    today = [r for r in d["recent"] if (r.get("finished") or r["started"]) > now - 86400]
    passed = sum(r["status"] in ("passed", "pr", "stage") for r in today)
    work_at = sorted({LABEL.get(_RUN_AT.get(x.get("stage") or "", "") or ("build" if x["kind"] in ("build", "fix") else "review" if x["kind"] == "review" else ""), "a run").lower()
                      for x in d["running"]})
    fail_rows = [r for r in rows if r["state"] == "failed"]
    nneeds = len(needs) if needs is not None else c["needs"]
    asking = sum(1 for r in needs or [] if r.get("st"))
    needs_sub = ("asking GitHub…" if d.get("needs_loading") else f"{asking} with questions, {nneeds - asking} to decide" if nneeds else "nothing waiting")
    tiles = (_tile("/tickets?stage=needs", "Needs you", nneeds, "wait", needs_sub, hot=bool(nneeds))
             + _tile("/tickets?stage=working", "Working now", len(d["running"]), "run", " and ".join(work_at) if d["running"] else "nothing running")
             + _tile("/tickets?stage=prs", "PRs & CI", f'{len(open_prs)} <small>open</small>', "run", f"{checks} running checks, {failing} failing")
             + _tile("/runs?status=passed", "Done today", passed, "done", f"{len(today)} runs in the last 24 hours")
             + _tile("/tickets?stage=failed", "Failed", len(fail_rows), "fail", f'#{fail_rows[0]["issue"]} {fail_rows[0]["why"]}'[:48] if fail_rows else "none"))
    head = (f'<div class="sd-dash-head"><div><p class="muted sd-fine">{esc(line)}</p><h1>Factory</h1></div>'
            f'<div class="sd-acts"><a class="btn" href="/projects/new">New project</a>{floor.pause_form(paused, csrf)}<a class="btn secondary" href="/?mode=confirm">Mode: {"live" if cfg["live"] else "dry run"}</a></div></div>')
    return (f'<div class="sd-dash">{head}{mode_bar}{d.get("settings_strip", "")}<div class="sd-tiles five">{tiles}</div>{floor_card(d, rows, now, csrf)}'
            f'<div class="sd-cols">{slot("/fragment/needs-tray", "Asking GitHub what needs you", "sd-card sd-flush sd-needs", '<h2 class="sd-bar-h">Needs you</h2>') if d.get("needs_loading") else needs_tray(needs, csrf)}<div class="sd-side">{running_card(d, rows, now)}{usage_card(d, now)}</div></div></div>')


# ---------------------------------------------------------------- loading: a small factory line while GitHub answers
# A drill mines ore, a furnace smelts it into plates, a splitter sends them to two assemblers that make tickets, and the tickets merge
# into a chest. SVG motion, so the page policy allows it; app.js holds it still for people who ask for reduced motion. Machines are
# drawn after the items, so an item goes into a machine and comes out as the next thing.
_LD_BELTS = ("M64 130 H120", "M184 130 H236", "M236 130 V50", "M236 130 V210", "M236 50 H300", "M236 210 H300",
             "M364 50 H420", "M420 50 V130", "M364 210 H420", "M420 210 V130", "M420 130 H470")
_LD_ORE = "M32 130 H152"
_LD_PLATE = ("M152 130 H236 V50 H332", "M152 130 H236 V210 H332")
_LD_TICKET = ("M332 50 H420 V130 H510", "M332 210 H420 V130 H510")
_LD_GEAR = "M12 2.5V6M21.5 12H18M12 21.5V18M2.5 12H6M18.7 5.3L16.2 7.8M18.7 18.7L16.2 16.2M5.3 18.7L7.8 16.2M5.3 5.3L7.8 7.8"
LD_QUIPS = ("Mining issue ore", "Smelting markdown into plates", "Splitting the belt", "Assembling comments", "Inserting labels",
            "Balancing the bus", "The factory must grow")


def _ld_items(cls: str, paths, dur: float, count: int, size: tuple[int, int]) -> str:
    w, h = size
    return "".join(f'<rect class="{cls}" x="{-w / 2:g}" y="{-h / 2:g}" width="{w}" height="{h}" rx="2"><animateMotion path="{paths[i % len(paths)]}" '
                   f'dur="{dur:g}s" begin="{-i * dur / count:.3f}s" repeatCount="indefinite"/></rect>' for i in range(count))


def _ld_gear(cx: int, cy: int, dur: str) -> str:
    return (f'<g class="ld-gear"><g transform="translate({cx - 15} {cy - 15}) scale(1.25)"><circle cx="12" cy="12" r="5.5"/><circle cx="12" cy="12" r="1.8"/>'
            f'<path d="{_LD_GEAR}"/></g><animateTransform attributeName="transform" type="rotate" from="0 {cx} {cy}" to="{"-" if dur[0] == "-" else ""}360 {cx} {cy}" '
            f'dur="{dur[1:]}" repeatCount="indefinite"/></g>')


def loader(what: str = "Loading") -> str:
    belts = "".join(f'<path class="ld-belt" d="{d}"/><path class="ld-flow" d="{d}"/>' for d in _LD_BELTS)
    items = (_ld_items("ld-ore", (_LD_ORE,), 3, 4, (10, 10)) + _ld_items("ld-plate", _LD_PLATE, 6, 8, (11, 11))
             + "".join(f'<rect class="ld-tk" x="-6" y="-7.5" width="12" height="15" rx="2"><animateMotion path="{_LD_TICKET[i % 2]}" dur="6s" '
                       f'begin="{-(i * .75 + .375):.3f}s" repeatCount="indefinite"/></rect>' for i in range(8)))
    puffs = "".join(f'<circle class="ld-puff" cx="160" cy="92" r="5"><animate attributeName="cy" values="92;46" dur="2.4s" begin="{-k * .8:.1f}s" '
                    f'repeatCount="indefinite"/><animate attributeName="opacity" values="0;.55;0" dur="2.4s" begin="{-k * .8:.1f}s" repeatCount="indefinite"/>'
                    f'<animate attributeName="r" values="3;9" dur="2.4s" begin="{-k * .8:.1f}s" repeatCount="indefinite"/></circle>' for k in range(3))
    bar = lambda x, y, delay: (f'<rect class="ld-track" x="{x + 9}" y="{y + 50}" width="46" height="3"/><rect class="ld-bar" x="{x + 9}" y="{y + 50}" width="0" height="3">'
                               f'<animate attributeName="width" from="0" to="46" dur="1.5s" begin="{delay}" repeatCount="indefinite"/></rect>')
    machines = ('<rect class="ld-m" x="0" y="98" width="64" height="64" rx="4"/>'
                '<g class="ld-bit"><path d="M32 127V117l4 4M35 132l9 5-5 1M29 132l-9 5 2-6"/><circle cx="32" cy="130" r="3"/>'
                '<animateTransform attributeName="transform" type="rotate" from="0 32 130" to="360 32 130" dur=".75s" repeatCount="indefinite"/></g>'
                '<rect class="ld-m" x="120" y="98" width="64" height="64" rx="4"/><path class="ld-grate" d="M137 118H167M137 124H167"/>'
                '<rect class="ld-fire" x="138" y="134" width="28" height="12" rx="2"/>'
                '<rect class="ld-m" x="300" y="18" width="64" height="64" rx="4"/>' + _ld_gear(332, 46, "+1.5s") + bar(300, 18, "0s")
                + '<rect class="ld-m" x="300" y="178" width="64" height="64" rx="4"/>' + _ld_gear(332, 206, "-1.5s") + bar(300, 178, "-.75s")
                + '<rect class="ld-m ld-chest" x="470" y="90" width="80" height="80" rx="4"/><path class="ld-grate" d="M476 112H544"/>'
                '<text class="ld-chest-t" x="510" y="146">TICKETS</text>')
    n, step = len(LD_QUIPS), 2.25
    shown = lambda i: ("1;0", f"0;{1 / n:.4f}") if i == 0 else ("0;1;0", f"0;{i / n:.4f};{(i + 1) / n:.4f}")   # each joke in its turn
    quips = "".join(f'<text class="ld-quip" x="280" y="292" opacity="{1 if i == 0 else 0}">› {esc(q)}<animate attributeName="opacity" '
                    f'values="{shown(i)[0]}" keyTimes="{shown(i)[1]}" calcMode="discrete" dur="{n * step:g}s" repeatCount="indefinite"/></text>'
                    for i, q in enumerate(LD_QUIPS))
    return (f'<div class="ld" role="status" aria-live="polite"><svg class="ld-net" viewBox="0 0 560 300" aria-hidden="true">'
            f'{belts}{items}{puffs}{machines}{quips}</svg><p class="muted sd-fine">{esc(what)}…</p></div>')


def slot(src: str, what: str, cls: str = "", head: str = "") -> str:
    """A part of the page that loads by itself (app.js fetches src and puts the answer in its place); a link stands in without scripts.
    head: the part's own heading, kept while it loads so the page says what is coming."""
    return f'<div class="ld-slot {cls}" data-load="{esc(src)}">{head}{loader(what)}<noscript><a href="{esc(src)}">Show it</a></noscript></div>'
