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


MAX_PM_MILESTONES, MIN_WORDS, MAX_WORDS, MAX_CATEGORIES = 6, 3, 25, 8
# A milestone is one plain line saying what the product's users can now do ("Parents can pay all their invoices in one checkout"),
# never code, ticket numbers, internal terms or a plan's numbering.
BAD_NAME = re.compile(r"#\d|`|[/\\<>]|\bL-\d|\b(?:phase|sprint|milestone|stage|step|iteration|release|version|mvp|v)\s*[-#:]?\s*\d"
                      r"|\bv?\d+\.\d+|\b\w+\.(?:py|js|ts|tsx|go|rs|rb|java|kt|swift|css|html|md|json|toml|ya?ml|sql)\b"
                      r"|\b(?:api|rls|schema|endpoint|refactor|backend|frontend|migration)s?\b", re.I)


@dataclass(frozen=True)
class Assessment:
    issue: int
    priority: str
    blocked_by: tuple
    reason: str
    repo: str = ""                  # the ticket's repository when the backlog spans several; "" for a one-repository backlog


def _num(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def _key(d: dict, backlog: set) -> tuple[str, int]:
    """(repo, number) of an entry. backlog: ticket numbers (one repository: repo is "") or (repo, number) pairs, where an entry
    names its repo unless the backlog holds a single repository."""
    n = d.get("issue")
    if not _num(n):
        raise ValueError("not a ticket of the backlog")
    if all(isinstance(k, int) for k in backlog):
        if n not in backlog:
            raise ValueError("not a ticket of the backlog")
        return "", n
    repos = {r for r, _ in backlog}
    r = d.get("repo", next(iter(repos)) if len(repos) == 1 else None)
    if not isinstance(r, str) or (r, n) not in backlog:
        raise ValueError("not a ticket of the backlog")
    return r, n


def _entry(d, backlog: set) -> Assessment:
    if not isinstance(d, dict):
        raise ValueError("not an object")
    r, n = _key(d, backlog)
    if d.get("priority") not in LABEL_FOR:
        raise ValueError("bad priority")
    blocked = d.get("blocked_by", [])
    if not isinstance(blocked, list) or len(blocked) > MAX_BLOCKERS or not all(_num(b) for b in blocked):
        raise ValueError("bad blocked_by")
    reason = d.get("reason", "")
    reason = " ".join(reason.split())[:MAX_REASON] if isinstance(reason, str) else ""
    return Assessment(n, d["priority"], tuple(sorted({b for b in blocked if b != n})), reason, r)


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
        if (a.repo, a.issue) not in done:
            done.add((a.repo, a.issue))
            out.append(a)
    return out


def milestone_name(name) -> str:
    """The line cleaned (one line of at most plan.MAX_NAME characters), or "" when a business user would not recognise it: numbers or ticket
    refs, a path or code, a version, internal terms, a plan's numbering ("Phase 2", "Sprint 3"), or too short to say who can
    do what ("Billing")."""
    name = " ".join(name.split()) if isinstance(name, str) else ""          # one line; one too long is dropped, never cut mid-sentence
    if (not name or len(name) > plan.MAX_NAME or BAD_NAME.search(name) or not re.search(r"[^\W\d_]{2}", name) or not MIN_WORDS <= len(name.split()) <= MAX_WORDS
            or name.lower() == NO_MILESTONE.lower()):
        return ""
    return name


NO_MILESTONE = "No milestone"


def _plain(text, limit: int, words: int) -> str:
    """One line of plain words of at most limit characters and words, or "" (too long is dropped, never cut)."""
    text = " ".join(text.split()) if isinstance(text, str) else ""
    if not text or len(text) > limit or len(text.split()) > words or BAD_NAME.search(text) or not re.search(r"[^\W\d_]{2}", text):
        return ""
    return text


def short_name(text) -> str:
    return _plain(text, plan.MAX_SHORT, 8)


def category_name(text) -> str:
    return _plain(text, plan.MAX_CATEGORY, 4)


def parse_features(text: str, backlog: set) -> dict:
    """{(repo, number): {"short": ..., "category": ...}} from the last `factory-priorities` block, each value validated (a bad
    one is left out). At most MAX_CATEGORIES distinct categories: later new ones are dropped. {} when there is no valid block."""
    blocks = BLOCK.findall(text or "")
    try:
        data = json.loads(blocks[-1]) if blocks else None
    except ValueError:
        return {}
    tickets = data.get("tickets") if isinstance(data, dict) else None
    out, cats = {}, []
    for d in tickets[:500] if isinstance(tickets, list) else []:
        if not isinstance(d, dict):
            continue
        try:
            key = _key(d, backlog)
        except ValueError:
            continue
        got = {}
        if (s := short_name(d.get("short"))):
            got["short"] = s
        if (c := category_name(d.get("category"))):
            c = next((x for x in cats if x.lower() == c.lower()), c)
            if c in cats or len(cats) < MAX_CATEGORIES:
                cats += [] if c in cats else [c]
                got["category"] = c
        if got and key not in out:
            out[key] = got
    return out


def apply_features(conn, found: dict) -> list[str]:
    """Write the PM's short names and categories where it may. A person's value is never touched. To keep the roadmap still, a
    short name is only written where there is none, and a category of the PM's own only changes when the sweep no longer uses it."""
    if not found:
        return []
    have = plan.features(conn, {r for r, _ in found})
    used = {v["category"] for v in found.values() if v.get("category")}
    named, sorted_n = 0, 0
    for k, v in found.items():
        cur = have.get(k, {})
        if v.get("short") and not cur.get("short") and cur.get("short_by") != "person":
            plan.set_feature(conn, k[0], k[1], "short", v["short"], "pm")
            named += 1
        if (v.get("category") and cur.get("category_by") != "person" and cur.get("category") != v["category"]
                and (not cur.get("category") or cur["category"] not in used)):
            plan.set_feature(conn, k[0], k[1], "category", v["category"], "pm")
            sorted_n += 1
    return ([f"named {named} feature(s)"] if named else []) + ([f"put {sorted_n} feature(s) in a category"] if sorted_n else [])


def parse_milestones(text: str, backlog: set) -> tuple[list, dict] | None:
    """The milestones the last `factory-priorities` block proposes, in order ([name], at most MAX_PM_MILESTONES), and
    {(repo, number): name} for the tickets it places ("" takes a ticket out). An invalid name is dropped (and so are the tickets
    put in it). None: no block, malformed JSON, or the block proposes no milestones."""
    blocks = BLOCK.findall(text or "")
    try:
        data = json.loads(blocks[-1]) if blocks else None
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("milestones"), list):
        return None
    names = []
    for raw in data["milestones"][:50]:
        name = milestone_name(raw)
        if not name:
            log.info("project manager: milestone dropped (%r)", str(raw)[:130])
            continue
        if name not in names and len(names) < MAX_PM_MILESTONES:
            names.append(name)
    assign = {}
    for d in data.get("tickets", [])[:500] if isinstance(data.get("tickets"), list) else []:
        if not isinstance(d, dict) or "milestone" not in d:
            continue
        try:
            key = _key(d, backlog)
        except ValueError:
            continue
        raw = d["milestone"]
        name = "" if raw in ("", None) else milestone_name(raw)
        if (name in names or raw in ("", None)) and key not in assign:
            assign[key] = name
    return names, assign


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
def backlog_issues(cfg, gh, repo: str, labels: list, skip=None) -> list[dict]:
    """The open tickets that carry a factory trigger or stage label, oldest first, at most pm.max_tickets. skip(issue) leaves
    one out before the cap (finished work)."""
    found: dict[int, dict] = {}
    for label in labels:
        for i in gh.labeled_issues(repo, label):
            found.setdefault(i["number"], i)
    return [found[n] for n in sorted(found) if not (skip and skip(found[n]))][:cfg.pm.max_tickets]


def backlog_items(issues: list, body_chars: int, pinned: dict | None = None, placed: dict | None = None,
                  named: dict | None = None) -> list[dict]:
    """What the agent sees of each ticket. Of the labels only priority, factory and stage ones: they say where it stands.
    pinned: issue -> the priority a person pinned (plan.pins), which the agent ranks the rest around. An issue carrying "repo"
    (a backlog over several repositories) is keyed (repo, number) in pinned and placed, and its item names the repository.
    placed: key -> (milestone, "person" | "pm"): the milestone the ticket is in now and who put it there.
    named: key -> plan.features entry: the short name and category it has now."""
    keep = lambda n: n.strip().lower().startswith(("priority:", "factory:", "stage:"))
    out = []
    for i in issues:
        key = (i["repo"], i["number"]) if i.get("repo") else i["number"]
        item = {"number": i["number"], "title": i.get("title") or "", "body": (i.get("body") or "")[:body_chars],
                "labels": [n for lb in i.get("labels", []) if keep(n := lb.get("name", ""))]}
        if i.get("repo"):
            item["repo"] = i["repo"]
        if (pinned or {}).get(key):
            item["pinned_priority"] = pinned[key]
        if (placed or {}).get(key):
            item["milestone"], item["milestone_by"] = placed[key]
        for f in ("short", "category"):
            if ((named or {}).get(key) or {}).get(f):
                item[f], item[f + "_by"] = named[key][f], named[key][f + "_by"]
        out.append(item)
    return out


def finished(conn, repo: str, issue: dict, done_label: str) -> bool:
    """Work that is done, which the project manager leaves out: the build is finished and its pull request is open (done_label),
    or every pull request the factory opened for it was merged."""
    if done_label in {lb.get("name", "") for lb in issue.get("labels", [])}:
        return True
    try:
        rows = conn.execute("SELECT status, summary FROM prs WHERE issue_repo=? AND issue_num=? AND status != 'superseded'",
                            (repo, issue["number"])).fetchall()
    except Exception:
        return False
    return bool(rows) and all(st == "closed" and sm == "merged" for st, sm in rows)


def digest(issues: list) -> str:
    """Changes when a ticket is added, removed or edited: no sweep when nothing changed."""
    return hashlib.sha256(json.dumps(sorted((i.get("repo", ""), i["number"], i.get("updated_at", "")) for i in issues)).encode()).hexdigest()


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


# ---------------------------------------------------------------- milestones
def apply_milestones(conn, scope: str, backlog: set, proposed: list, assign: dict) -> list[str]:
    """Make the milestones the project manager proposed and place its tickets, within what it owns. backlog: (repo, number) of
    the open, unfinished tickets it looked at. Returns a line per change.

    A person's milestone (made, renamed, described, moved or deleted by a person, or older than the project manager's) and a
    ticket a person placed are never touched. To keep the roadmap from churning, a milestone of the PM's own is settled while
    most of the tickets it was given are still open in it: it keeps its place and its tickets, and new tickets may join it. Only
    a milestone whose tickets clearly changed (most closed or moved) is reordered, emptied or removed. A new milestone needs two
    tickets, unless it is the first one proposed (the single most important item)."""
    plan.ensure_tables(conn)
    have, owner = plan.milestones(conn, scope), plan.milestone_owners(conn, scope)
    current, last = plan.ticket_milestones(conn, scope), plan.pm_tickets(conn, scope)
    towner = plan.ticket_owners(conn, {r for r, _ in backlog})
    mine = [n for n in have if owner.get(n) == "pm"]
    settled = {n for n in mine if (given := last.get(n)) and 2 * sum(k in backlog and current.get(k) == n for k in given) > len(given)}

    def movable(k) -> bool:
        cur = current.get(k)
        if towner.get(k) == "person" or (cur is not None and towner.get(k) != "pm"):
            return False
        return cur is None or (cur in mine and cur not in settled)

    changes, made = [], []
    wanted = {}
    for k, name in assign.items():
        if k in backlog and movable(k) and name and current.get(k) != name:
            wanted.setdefault(name, []).append(k)
    for i, name in enumerate(proposed):
        if name in have or owner.get(name) == "person" or len(mine) + len(made) >= MAX_PM_MILESTONES:
            continue                        # exists already, or a person deleted that name: theirs
        if len(wanted.get(name, [])) >= 2 or (i == 0 and wanted.get(name)):
            if plan.add_milestone(conn, scope, name):
                plan.mark_milestone(conn, scope, name, "pm")
                made.append(name)
                changes.append(f"new milestone {name}")
    names = plan.milestones(conn, scope)
    touched = set(made)
    for k, name in assign.items():
        if k not in backlog or not movable(k) or current.get(k) == name or (name and name not in names):
            continue
        plan.set_ticket_milestone(conn, scope, k[0], k[1], name)
        plan.mark_ticket(conn, k[0], k[1], "pm")
        touched |= {name, current.get(k)} - {None, ""}
        changes.append(f"{tracker.display(k[1])} " + (f"to {name}" if name else "out of its milestone"))
    current = plan.ticket_milestones(conn, scope)
    free = [n for n in mine if n not in settled]
    for n in free:                          # an unsettled milestone of the PM's that it no longer proposes and that holds nothing
        if n not in proposed and not any(v == n for v in current.values()):
            plan.delete_milestone(conn, scope, n)
            changes.append(f"removed empty milestone {n}")
    names = plan.milestones(conn, scope)
    moving = [n for n in names if n in made or n in free]
    order = [n for n in proposed if n in moving] + [n for n in moving if n not in proposed]
    if moving and order != moving:          # the moving milestones take the places they hold now, in the proposed order
        slots, it = [i for i, n in enumerate(names) if n in moving], iter(order)
        new = list(names)
        for i in slots:
            new[i] = next(it)
        plan.set_order(conn, scope, new)
        changes.append("milestones reordered")
    for n in plan.milestones(conn, scope):
        if owner.get(n, "pm" if n in made else None) != "pm" or n not in touched | set(made):
            continue
        plan.set_pm_tickets(conn, scope, n, {k for k, v in current.items() if v == n and k in backlog} | (last.get(n, set()) if n in settled else set()))
    return changes
