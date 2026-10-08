"""The project manager (PM): priorities and blockers for a repository's factory tickets.

On a periodic sweep a read-only agent reads the repository's open factory tickets and ends its document with a
`factory-priorities` block. That block is untrusted agent output (derived from ticket text anyone may write), so it is only
data: each entry is validated against fixed sets (a ticket of the backlog, a priority of high / normal / low, blockers that are
open issues of the same repository) and anything else is dropped. The PM can do exactly three things with it:

* put `priority: high` / `priority: low` on a ticket, or take off the one it put there itself. A priority a person pinned (on
  the Tickets page or with a priority label in the label editor, plan.py) or a priority label a person set, changed or removed
  always wins: from then on the PM leaves that ticket's priority alone. The poller already orders eligible
  tickets by these labels (main.priority_rank); a priority never makes a ticket eligible.
* record blockers. While the PM is enabled, a build ticket (implement or auto) whose blockers are still open waits, its trigger
  label untouched, and starts once they are closed. A blocker comes from the PM, or from a `Blocked by #N` line in the ticket's
  body (which only its author or someone with write access can edit). A person with write access applies `pm.unblock_label` to
  make the factory ignore the PM's blockers on a ticket. A run that already started is never stopped.
* comment on a ticket when it changed its priority or its blockers, from validated values and a sanitized one-line reason.
"""
import hashlib
import json
import logging
import re
from dataclasses import dataclass

from . import db as dbm
from . import plan
from . import tracker
from .sanitize import sanitize_markdown

log = logging.getLogger("factory.pm")
BLOCK = re.compile(r"^```factory-priorities[ \t]*\n(.*?)^```[ \t]*$", re.M | re.S)
LABEL_FOR = {"high": "priority: high", "normal": "", "low": "priority: low"}
MARKER = "<!-- factory:pm -->"
EXPLICIT = re.compile(r"^[ \t]*blocked[ \t]+by:?[ \t]*((?:(?:#\d{1,9}|L-\d{1,8})[ \t,]*)+)", re.I | re.M)
REF = re.compile(r"#(\d+)|L-(\d+)", re.I)
BUILD_KINDS = ("implement", "auto")                 # the kinds of work a blocker holds back (read-only stages still run)
MAX_BLOCKERS, MAX_EXPLICIT, MAX_REASON = 5, 10, 200
STATUS_KEY = "pm:{repo}"


@dataclass(frozen=True)
class Assessment:
    issue: int
    priority: str
    blocked_by: tuple
    reason: str


def _num(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def _entry(d, backlog: set) -> Assessment:
    if not isinstance(d, dict):
        raise ValueError("not an object")
    n = d.get("issue")
    if not _num(n) or n not in backlog:
        raise ValueError("not a ticket of the backlog")
    if d.get("priority") not in LABEL_FOR:
        raise ValueError("bad priority")
    blocked = d.get("blocked_by", [])
    if not isinstance(blocked, list) or len(blocked) > MAX_BLOCKERS or not all(_num(b) for b in blocked):
        raise ValueError("bad blocked_by")
    reason = d.get("reason", "")
    reason = " ".join(reason.split())[:MAX_REASON] if isinstance(reason, str) else ""
    return Assessment(n, d["priority"], tuple(sorted({b for b in blocked if b != n})), reason)


def parse(text: str, backlog: set) -> list | None:
    """The validated entries of the last `factory-priorities` block, one per ticket of the backlog (the first wins). An invalid
    entry is dropped, so its ticket keeps what it had. None when there is no block or its JSON is malformed: nothing changes."""
    blocks = BLOCK.findall(text or "")
    if not blocks:
        return None
    try:
        data = json.loads(blocks[-1])
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("tickets"), list):
        return None
    out, done = [], set()
    for d in data["tickets"][:500]:
        try:
            a = _entry(d, backlog)
        except ValueError as e:
            log.info("project manager: entry dropped (%s)", e)
            continue
        if a.issue not in done:
            done.add(a.issue)
            out.append(a)
    return out


def explicit_blockers(body: str) -> list[int]:
    """`Blocked by #9, #12` lines in a ticket's body; a local ticket is named as it is shown (`Blocked by L-3`)."""
    nums = [int(gh) if gh else tracker.LOCAL_BASE + int(loc) for m in EXPLICIT.finditer(body or "") for gh, loc in REF.findall(m.group(1))]
    return sorted(set(nums))[:MAX_EXPLICIT]


def is_open(gh, repo: str, num: int, cache: dict) -> bool:
    """An open issue (not a pull request) of this repository. Anything unreadable does not block: a blocker only holds work back."""
    k = (repo, int(num))
    if k not in cache:
        try:
            i = gh.get_issue(repo, int(num))
            cache[k] = i.get("state") == "open" and "pull_request" not in i
        except Exception:
            log.warning("could not read %s#%s to check a blocker", repo, num)
            cache[k] = False
    return cache[k]


def blockers(cfg, gh, conn, repo: str, issue: dict, cache: dict, trusted) -> list[int]:
    """The open tickets holding this one back. Only while the PM is enabled. trusted: main.trusted (who applied a label)."""
    if not cfg.pm.enabled:
        return []
    num = issue["number"]
    nums = set(explicit_blockers(issue.get("body") or ""))
    inferred = dbm.pm_blocked_by(conn, repo, num)
    if inferred:
        names = {lb.get("name", "") for lb in issue.get("labels", [])}
        if not (cfg.pm.unblock_label in names and trusted(cfg, gh, repo, num, cfg.pm.unblock_label)[0]):
            nums |= set(inferred)
    return [n for n in sorted(nums) if n != num and is_open(gh, repo, n, cache)]


def cycles(graph: dict) -> list[tuple]:
    """Tickets that wait for each other (each a sorted tuple), among the tickets in graph (ticket -> its blockers)."""
    found = set()
    for start in graph:
        stack, seen = [(start, (start,))], {start}
        while stack:
            node, path = stack.pop()
            for nxt in graph.get(node, ()):
                if nxt == start:
                    found.add(tuple(sorted(path)))
                elif nxt in graph and nxt not in seen:
                    seen.add(nxt)
                    stack.append((nxt, path + (nxt,)))
    return sorted(found)


# ---------------------------------------------------------------- the sweep
def backlog_issues(cfg, gh, repo: str, labels: list) -> list[dict]:
    """The open tickets that carry a factory trigger or stage label, oldest first, at most pm.max_tickets."""
    found: dict[int, dict] = {}
    for label in labels:
        for i in gh.labeled_issues(repo, label):
            found.setdefault(i["number"], i)
    return [found[n] for n in sorted(found)][:cfg.pm.max_tickets]


def backlog_items(issues: list, body_chars: int, pinned: dict | None = None) -> list[dict]:
    """What the agent sees of each ticket. Of the labels only priority, factory and stage ones: they say where it stands.
    pinned: issue -> the priority a person pinned (plan.pins), which the agent ranks the rest around."""
    keep = lambda n: n.strip().lower().startswith(("priority:", "factory:", "stage:"))
    out = []
    for i in issues:
        item = {"number": i["number"], "title": i.get("title") or "", "body": (i.get("body") or "")[:body_chars],
                "labels": [n for lb in i.get("labels", []) if keep(n := lb.get("name", ""))]}
        if (pinned or {}).get(i["number"]):
            item["pinned_priority"] = pinned[i["number"]]
        out.append(item)
    return out


def digest(issues: list) -> str:
    """Changes when a ticket is added, removed or edited: no sweep when nothing changed."""
    return hashlib.sha256(json.dumps(sorted((i["number"], i.get("updated_at", "")) for i in issues)).encode()).hexdigest()


def sweep_state(conn, repo: str) -> dict:
    row = conn.execute("SELECT value FROM status WHERE key=?", (STATUS_KEY.format(repo=repo),)).fetchone()
    try:
        v = json.loads(row[0]) if row else {}
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def set_sweep_state(conn, repo: str, ts: float, dig: str) -> None:
    dbm.set_status(conn, STATUS_KEY.format(repo=repo), json.dumps({"ts": ts, "digest": dig}))


def comment_body(cfg, a: Assessment, priority_set: bool, blocked: list, was_blocked: list) -> str:
    """Built from validated values; the agent's only text is the one-line reason, sanitized."""
    lines = [MARKER]
    if priority_set:
        lines.append(f"**Priority:** {a.priority}" + ("" if LABEL_FOR[a.priority] else " (the priority label was removed)")
                     + ", set by the project manager.")
    if blocked:
        lines.append("**Blocked by:** " + ", ".join(f"#{n}" for n in blocked) + ". The factory will not start building this ticket "
                     f"until they are closed. A person with write access can apply `{cfg.pm.unblock_label}` to ignore this.")
    elif was_blocked:
        lines.append("**No longer blocked** in the project manager's assessment.")
    if a.reason:
        lines.append(f"**Why:** {a.reason}")
    lines.append("\n_An assessment by the project manager agent. A person's priority label always wins over it._")
    return sanitize_markdown("\n".join(lines), 4000)


def apply(cfg, gh, conn, repo: str, backlog: dict, found: list, run_id) -> list[str]:
    """Record each assessment, change only the priority labels the PM owns, and comment on what changed. Returns a line per change."""
    changes, cache = [], {}
    for a in found:
        try:
            fresh = gh.get_issue(repo, a.issue)                      # what a person may have done since the backlog was read
        except Exception:
            log.warning("project manager: could not read %s#%s", repo, a.issue)
            continue
        if fresh.get("state", "open") != "open" or "pull_request" in fresh:
            continue
        prev = dbm.pm_assessment(conn, repo, a.issue) or {}
        mine, was_blocked = prev.get("applied_label", ""), dbm.pm_blocked_by(conn, repo, a.issue)
        current = {n.strip().lower(): n for lb in fresh.get("labels", []) if (n := lb.get("name", "")).strip().lower().startswith("priority:")}
        # The PM owns the priority only while nobody pinned it and the labels are exactly what it left there. A person who adds,
        # changes or removes a priority label takes it over for good (labels added from the UI use the factory's account, so the
        # actor cannot tell: the UI records a pin instead, which also covers a person choosing the label the PM had set).
        owned = (plan.pin(conn, repo, a.issue) is None and not prev.get("overridden")
                 and set(current) == ({mine} if mine else set()))
        target, priority_set = LABEL_FOR[a.priority], False
        if owned and target != mine:
            if mine:
                gh.remove_label(repo, a.issue, current[mine])
            if target:
                gh.add_labels(repo, a.issue, [target])
            priority_set = True
        blocked = [n for n in a.blocked_by if n in backlog or is_open(gh, repo, n, cache)]
        dbm.set_pm_assessment(conn, repo, a.issue, a.priority, target if owned else "", not owned, blocked, a.reason,
                              fresh.get("updated_at", ""), run_id)
        if priority_set or blocked != was_blocked:
            gh.comment(repo, a.issue, comment_body(cfg, a, priority_set, blocked, was_blocked))
            changes.append(f"#{a.issue}: " + ", ".join(
                ([f"priority {a.priority}"] if priority_set else []) + ([f"blocked by {', '.join(f'#{n}' for n in blocked)}"] if blocked
                                                                          else ["unblocked"] if blocked != was_blocked else [])))
    return changes
