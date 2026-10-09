"""A person's plan for a project's tickets: priorities they pinned, milestones, and the order of operations they lead to.

A pinned priority is a person's choice (high, normal or low) made on the Tickets page or with a priority label in the label
editor. The project manager never changes a pinned priority; "Let the project manager decide" takes the pin off. Normal is a pin
of its own, so a person can keep a ticket at normal although no label says so.

Milestones are named, ordered groups of tickets kept per scope in the factory's database (so local and GitHub tickets work alike,
and nothing is written to GitHub). A scope is a project (its name: a milestone may hold tickets of any of its repositories), or a
repository that belongs to no project (its owner/name; names of projects never hold a slash, so the two never collide). A
milestone is one line of plain English: the capability it gives the product's users ("Parents can pay all their invoices in one
checkout"). The project manager may make milestones and put tickets in them too;
milestone_owners and ticket_owners record who did what, and whatever a person made, reworded, moved, deleted or
assigned stays theirs: the project manager never changes it again (see pm.apply_milestones). The roadmap orders tickets into
steps: a ticket's step is one more than the latest step of the tickets it waits for; within a step, higher priority comes first.
There are no dates, only what comes before what.
"""
import json
import sqlite3
import time

PRIORITIES = ("high", "normal", "low")
LABEL_FOR = {"high": "priority: high", "normal": "", "low": "priority: low"}
FROM_LABEL = {"priority: high": "high", "priority: low": "low"}
RANK = {"high": 0, "normal": 1, "low": 2}
MAX_MILESTONES, MAX_NAME, MAX_SHORT, MAX_CATEGORY = 30, 120, 40, 24


def ensure_tables(db) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS priority_pins (
            repo TEXT NOT NULL, issue INTEGER NOT NULL, priority TEXT NOT NULL CHECK (priority IN ('high','normal','low')),
            at REAL NOT NULL, PRIMARY KEY (repo, issue));
        CREATE TABLE IF NOT EXISTS milestones (
            repo TEXT NOT NULL, name TEXT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY (repo, name));
        CREATE TABLE IF NOT EXISTS ticket_milestones (
            repo TEXT NOT NULL, issue INTEGER NOT NULL, milestone TEXT NOT NULL, PRIMARY KEY (repo, issue));
        CREATE TABLE IF NOT EXISTS milestone_owners (
            scope TEXT NOT NULL, name TEXT NOT NULL, owner TEXT NOT NULL CHECK (owner IN ('person','pm')), PRIMARY KEY (scope, name));
        CREATE TABLE IF NOT EXISTS ticket_owners (
            repo TEXT NOT NULL, issue INTEGER NOT NULL, owner TEXT NOT NULL CHECK (owner IN ('person','pm')), PRIMARY KEY (repo, issue));
        CREATE TABLE IF NOT EXISTS ticket_features (
            repo TEXT NOT NULL, issue INTEGER NOT NULL, short TEXT NOT NULL DEFAULT '', category TEXT NOT NULL DEFAULT '',
            short_by TEXT NOT NULL DEFAULT '', category_by TEXT NOT NULL DEFAULT '', PRIMARY KEY (repo, issue));
        CREATE TABLE IF NOT EXISTS pm_milestone_tickets (
            scope TEXT NOT NULL, name TEXT NOT NULL, tickets TEXT NOT NULL DEFAULT '[]', PRIMARY KEY (scope, name));
    """)
    # milestones.repo holds the scope (a project name or a repository); ticket_milestones.scope the scope of its milestone
    if "scope" not in {r[1] for r in db.execute("PRAGMA table_info(ticket_milestones)")}:
        db.execute("ALTER TABLE ticket_milestones ADD COLUMN scope TEXT NOT NULL DEFAULT ''")
        db.execute("UPDATE ticket_milestones SET scope=repo")
    db.commit()


def _read(db, sql: str, args=()) -> list:
    """Rows, or none when the orchestrator has not created the tables yet (the UI reads the database read-only)."""
    try:
        return db.execute(sql, args).fetchall()
    except sqlite3.OperationalError:
        return []


# ---------------------------------------------------------------- pinned priorities
def pin(db, repo: str, issue: int) -> dict | None:
    r = _read(db, "SELECT priority, at FROM priority_pins WHERE repo=? AND issue=?", (repo, int(issue)))
    return {"priority": r[0][0], "at": r[0][1]} if r else None


def pins(db, repo: str | None = None) -> dict:
    """(repo, issue) -> priority."""
    rows = _read(db, "SELECT repo, issue, priority FROM priority_pins" + (" WHERE repo=?" if repo else ""), (repo,) if repo else ())
    return {(r, n): p for r, n, p in rows}


def set_pin(db, repo: str, issue: int, priority: str) -> None:
    if priority not in PRIORITIES:
        raise ValueError("bad priority")
    ensure_tables(db)
    db.execute("INSERT OR REPLACE INTO priority_pins VALUES (?,?,?,?)", (repo, int(issue), priority, time.time()))
    db.commit()


def release(db, repo: str, issue: int, labels: list[str]) -> None:
    """Hand the priority back to the project manager: no pin, and the priority label on the ticket now (if exactly one) counts as
    the PM's own, so its next sweep may change it."""
    ensure_tables(db)
    db.execute("DELETE FROM priority_pins WHERE repo=? AND issue=?", (repo, int(issue)))
    current = {n.strip().lower() for n in labels if n.strip().lower().startswith("priority:")}
    try:
        db.execute("UPDATE pm_assessments SET overridden=0, applied_label=? WHERE repo=? AND issue=?",
                   (current.pop() if len(current) == 1 else "", repo, int(issue)))
    except sqlite3.OperationalError:
        pass
    db.commit()


def pin_from_label(label: str, added: bool) -> str | None:
    """The pin a person's label edit means: adding a priority label pins it, removing one pins normal. None: not a priority label."""
    name = (label or "").strip().lower()
    if not name.startswith("priority:"):
        return None
    return FROM_LABEL.get(name, "normal") if added else "normal"


def priority_of(labels: list[str]) -> str:
    ranks = [FROM_LABEL[n] for n in (x.strip().lower() for x in labels) if n in FROM_LABEL]
    return min(ranks, key=RANK.get, default="normal")


def _decide(pinned, assessed, labels) -> tuple[str, str]:
    if pinned:
        return pinned, "you"
    if labels is not None:
        prio = priority_of(labels)
        has = any(n.strip().lower().startswith("priority:") for n in labels)
        if assessed and not assessed[2] and (assessed[1] or not has):
            return prio, "pm"
        return prio, "label" if has else ""
    if assessed and not assessed[2]:
        return assessed[0], "pm"
    return "normal", ""


def who_set(db, repo: str, issue: int, labels: list[str] | None = None) -> tuple[str, str]:
    """(priority, source): source is 'you' (pinned by a person), 'pm' (the project manager's own), 'label' (a priority label
    nobody pinned here, e.g. set on GitHub) or '' (nobody set one: normal). labels: the ticket's labels when known."""
    p = pin(db, repo, issue)
    a = _read(db, "SELECT priority, applied_label, overridden FROM pm_assessments WHERE repo=? AND issue=?", (repo, int(issue)))
    return _decide(p and p["priority"], a[0] if a else None, labels)


def who_set_all(db) -> dict:
    """(repo, issue) -> (priority, source) for every ticket anyone set a priority on. A local ticket's labels are read from the
    local store; a GitHub ticket's labels are not known here, so its priority is the pin or the project manager's."""
    pinned = pins(db)
    assessed = {(r, n): (p, a, o) for r, n, p, a, o in _read(db, "SELECT repo, issue, priority, applied_label, overridden FROM pm_assessments")}
    labels: dict = {}
    for r, n, lb in _read(db, "SELECT l.repo, l.number, l.label FROM local_labels l JOIN local_tickets t ON t.repo=l.repo AND t.number=l.number"):
        labels.setdefault((r, n), []).append(lb)
    for r, n in _read(db, "SELECT repo, number FROM local_tickets"):
        labels.setdefault((r, n), [])
    keys = set(pinned) | set(assessed) | {k for k, v in labels.items() if any(x.lower().startswith("priority:") for x in v)}
    return {k: _decide(pinned.get(k), assessed.get(k), labels.get(k)) for k in keys}


# ---------------------------------------------------------------- scopes: a project, or a repository in none
def scope_of(cfg, repo: str) -> str:
    return next((p.name for p in cfg.projects if any(r.repo == repo for r in p.repos)), repo)


def scope_repos(cfg, scope: str) -> list[str]:
    for p in cfg.projects:
        if p.name == scope:
            return [r.repo for r in p.repos]
    return [scope] if scope in cfg.repos else []


def scopes(cfg) -> list[tuple[str, str]]:
    """(scope, label): each configured project, then each repository that belongs to none."""
    grouped = {r.repo for p in cfg.projects for r in p.repos}
    return [(p.name, p.name) for p in cfg.projects] + [(r, r) for r in cfg.repos if r not in grouped]


def migrate(db, scope_for) -> int:
    """Move milestones kept under a repository into its project's scope (scope_for: repository -> scope), keeping names, order,
    descriptions, assignments and who owns them. Same-named milestones merge; a person's ownership wins. Safe to run again."""
    ensure_tables(db)
    moved = 0
    for (src,) in db.execute("SELECT DISTINCT repo FROM milestones WHERE repo LIKE '%/%'").fetchall():
        dst = scope_for(src)
        if dst == src:
            continue
        owner_of = lambda s, n: (db.execute("SELECT owner FROM milestone_owners WHERE scope=? AND name=?", (s, n)).fetchone() or [None])[0]
        for (name,) in db.execute("SELECT name FROM milestones WHERE repo=? ORDER BY position, name", (src,)).fetchall():
            there = db.execute("SELECT 1 FROM milestones WHERE repo=? AND name=?", (dst, name)).fetchone()
            if there is None:
                pos = db.execute("SELECT COUNT(*) FROM milestones WHERE repo=?", (dst,)).fetchone()[0]
                db.execute("INSERT INTO milestones (repo, name, position) VALUES (?,?,?)", (dst, name, pos))
            a, b = owner_of(src, name), owner_of(dst, name) if there is not None else None
            owner = "person" if "person" in (a, b) or (there is not None and (a is None or b is None)) else (a or b)
            db.execute("DELETE FROM milestone_owners WHERE scope IN (?,?) AND name=?", (src, dst, name))
            if owner:
                db.execute("INSERT INTO milestone_owners VALUES (?,?,?)", (dst, name, owner))
            got = pm_tickets(db, dst).get(name, set()) | pm_tickets(db, src).get(name, set())
            db.execute("DELETE FROM pm_milestone_tickets WHERE scope IN (?,?) AND name=?", (src, dst, name))
            if owner == "pm":
                db.execute("INSERT INTO pm_milestone_tickets VALUES (?,?,?)", (dst, name, json.dumps(sorted(got))))
            moved += 1
        db.execute("DELETE FROM milestones WHERE repo=?", (src,))
    for repo, issue, name, scope in db.execute("SELECT repo, issue, milestone, scope FROM ticket_milestones").fetchall():
        dst = scope_for(repo)
        if scope == dst:
            continue
        if db.execute("SELECT 1 FROM milestones WHERE repo=? AND name=?", (dst, name)).fetchone():
            db.execute("UPDATE ticket_milestones SET scope=? WHERE repo=? AND issue=?", (dst, repo, issue))
        else:
            db.execute("DELETE FROM ticket_milestones WHERE repo=? AND issue=?", (repo, issue))
        moved += 1
    db.commit()
    return moved


# ---------------------------------------------------------------- milestones
def milestones(db, scope: str) -> list[str]:
    return [n for (n,) in _read(db, "SELECT name FROM milestones WHERE repo=? ORDER BY position, name", (scope,))]


def clean_name(name: str) -> str:
    return " ".join((name or "").split())[:MAX_NAME]


def add_milestone(db, scope: str, name: str) -> bool:
    name = clean_name(name)
    have = milestones(db, scope)
    if not name or name in have or len(have) >= MAX_MILESTONES:
        return False
    ensure_tables(db)
    db.execute("INSERT INTO milestones (repo, name, position) VALUES (?,?,?)", (scope, name, len(have)))
    db.commit()
    return True


def rename_milestone(db, scope: str, name: str, new_name: str) -> bool:
    """Reword a milestone; its tickets and who owns it move with it. False: no such milestone, or the new line is empty or taken."""
    new_name, have = clean_name(new_name), milestones(db, scope)
    if name not in have or not new_name or (new_name != name and new_name in have):
        return False
    ensure_tables(db)
    db.execute("UPDATE milestones SET name=? WHERE repo=? AND name=?", (new_name, scope, name))
    db.execute("UPDATE ticket_milestones SET milestone=? WHERE scope=? AND milestone=?", (new_name, scope, name))
    db.execute("UPDATE OR REPLACE milestone_owners SET name=? WHERE scope=? AND name=?", (new_name, scope, name))
    db.execute("UPDATE OR REPLACE pm_milestone_tickets SET name=? WHERE scope=? AND name=?", (new_name, scope, name))
    db.commit()
    return True


def move_milestone(db, scope: str, name: str, step: int) -> bool:
    have = milestones(db, scope)
    if name not in have:
        return False
    i = have.index(name)
    j = max(0, min(len(have) - 1, i + step))
    have.insert(j, have.pop(i))
    db.executemany("UPDATE milestones SET position=? WHERE repo=? AND name=?", [(k, scope, n) for k, n in enumerate(have)])
    db.commit()
    return i != j


def set_order(db, scope: str, names: list[str]) -> None:
    """Put the scope's milestones in this order (names it does not hold are ignored, ones it leaves out go last)."""
    have = milestones(db, scope)
    order = [n for n in names if n in have] + [n for n in have if n not in names]
    db.executemany("UPDATE milestones SET position=? WHERE repo=? AND name=?", [(k, scope, n) for k, n in enumerate(order)])
    db.commit()


def delete_milestone(db, scope: str, name: str) -> bool:
    ensure_tables(db)
    cur = db.execute("DELETE FROM milestones WHERE repo=? AND name=?", (scope, name))
    db.execute("DELETE FROM ticket_milestones WHERE scope=? AND milestone=?", (scope, name))
    db.execute("DELETE FROM pm_milestone_tickets WHERE scope=? AND name=?", (scope, name))
    db.commit()
    return bool(cur.rowcount)


def ticket_milestones(db, scope: str) -> dict:
    """(repo, issue) -> milestone name, for the tickets of every repository of the scope."""
    return {(r, n): m for r, n, m in _read(db, "SELECT repo, issue, milestone FROM ticket_milestones WHERE scope=?", (scope,))}


def set_ticket_milestone(db, scope: str, repo: str, issue: int, name: str) -> bool:
    """name '' takes the ticket out of its milestone. False: no such milestone in the ticket's scope."""
    ensure_tables(db)
    if not name:
        db.execute("DELETE FROM ticket_milestones WHERE repo=? AND issue=?", (repo, int(issue)))
    elif name in milestones(db, scope):
        db.execute("INSERT OR REPLACE INTO ticket_milestones (repo, issue, milestone, scope) VALUES (?,?,?,?)", (repo, int(issue), name, scope))
    else:
        return False
    db.commit()
    return True


# ---------------------------------------------------------------- who made a milestone or put a ticket in one
def mark_milestone(db, scope: str, name: str, owner: str) -> None:
    """owner person or pm. A person's mark is never taken back by the project manager."""
    ensure_tables(db)
    db.execute("INSERT OR REPLACE INTO milestone_owners VALUES (?,?,?)", (scope, name, owner))
    db.commit()


def mark_ticket(db, repo: str, issue: int, owner: str) -> None:
    ensure_tables(db)
    db.execute("INSERT OR REPLACE INTO ticket_owners VALUES (?,?,?)", (repo, int(issue), owner))
    db.commit()


def milestone_owners(db, scope: str) -> dict:
    """name -> 'person' | 'pm'. A milestone with no mark predates the project manager's: treat it as a person's."""
    return {n: o for n, o in _read(db, "SELECT name, owner FROM milestone_owners WHERE scope=?", (scope,))}


def ticket_owners(db, repos) -> dict:
    """(repo, issue) -> 'person' | 'pm', for these repositories."""
    repos = list(repos)
    if not repos:
        return {}
    marks = ",".join("?" * len(repos))
    return {(r, n): o for r, n, o in _read(db, f"SELECT repo, issue, owner FROM ticket_owners WHERE repo IN ({marks})", tuple(repos))}


def pm_tickets(db, scope: str) -> dict:
    """name -> {(repo, issue)}: the tickets the project manager last put in a milestone of its own (its stability rule compares them)."""
    out = {}
    for n, t in _read(db, "SELECT name, tickets FROM pm_milestone_tickets WHERE scope=?", (scope,)):
        try:
            out[n] = {(str(r), int(i)) for r, i in json.loads(t)}
        except (ValueError, TypeError):
            out[n] = set()
    return out


def set_pm_tickets(db, scope: str, name: str, tickets) -> None:
    ensure_tables(db)
    db.execute("INSERT OR REPLACE INTO pm_milestone_tickets VALUES (?,?,?)", (scope, name, json.dumps(sorted([r, int(i)] for r, i in tickets))))
    db.commit()



# ---------------------------------------------------------------- a ticket as a feature: a short name and a business category
def features(db, repos) -> dict:
    """(repo, issue) -> {short, category, short_by, category_by}; *_by is 'person', 'pm' or '' (nobody wrote it)."""
    repos = list(repos)
    if not repos:
        return {}
    marks = ",".join("?" * len(repos))
    return {(r, n): {"short": s, "category": c, "short_by": sb, "category_by": cb} for r, n, s, c, sb, cb in _read(
        db, f"SELECT repo, issue, short, category, short_by, category_by FROM ticket_features WHERE repo IN ({marks})", tuple(repos))}


def set_feature(db, repo: str, issue: int, field: str, value: str, owner: str) -> None:
    """field short or category, owner person or pm. Callers check ownership: a person's value is never overwritten by the PM."""
    if field not in ("short", "category") or owner not in ("person", "pm"):
        raise ValueError("bad field")
    value = " ".join((value or "").split())[:MAX_SHORT if field == "short" else MAX_CATEGORY]
    ensure_tables(db)
    db.execute("INSERT OR IGNORE INTO ticket_features (repo, issue) VALUES (?,?)", (repo, int(issue)))
    db.execute(f"UPDATE ticket_features SET {field}=?, {field}_by=? WHERE repo=? AND issue=?", (value, owner, repo, int(issue)))
    db.commit()


# ---------------------------------------------------------------- the order of operations
def steps(waits: dict) -> dict:
    """ticket -> step (1 = can start now): one more than the latest step of the tickets it waits for, among the tickets in waits
    (ticket -> the tickets it waits for). Tickets that wait for each other are cut at the loop, so every ticket gets a step."""
    out: dict = {}

    def visit(n, path):
        if n in out:
            return out[n]
        before = [visit(b, path | {n}) for b in waits.get(n, ()) if b in waits and b not in path and b != n]
        out[n] = 1 + max(before, default=0)
        return out[n]

    for n in sorted(waits):
        visit(n, frozenset())
    return out


def chain(waits: dict, focus: int) -> set:
    """focus, everything it waits for and everything that waits for it (directly or not)."""
    seen, todo = {focus}, [focus]
    while todo:
        n = todo.pop()
        for b in waits.get(n, ()):
            if b in waits and b not in seen:
                seen.add(b)
                todo.append(b)
    todo = [focus]
    after = {focus}
    while todo:
        n = todo.pop()
        for t, bs in waits.items():
            if n in bs and t not in after:
                after.add(t)
                todo.append(t)
    return seen | after


def queue(tickets: list[dict], step: dict) -> list[dict]:
    """The order the factory takes the open tickets: those that can start now (nothing they wait for is still open) by priority,
    then step, then number; then the ones still waiting, by step. A ticket already being worked on is not in it.
    tickets: {issue, priority, done, waits, state}, and key when tickets of several repositories mix (waits and step use it)."""
    k = lambda t: t.get("key", t["issue"])
    open_ = {k(t) for t in tickets if not t["done"]}
    todo = [t for t in tickets if not t["done"] and t.get("state") not in ("working", "prs")]       # already being worked on
    ready = lambda t: not any(b in open_ for b in t["waits"])
    first = lambda t: (RANK[t["priority"]], step.get(k(t), 1), t["issue"], k(t))
    return (sorted((t for t in todo if ready(t)), key=first)
            + sorted((t for t in todo if not ready(t)), key=lambda t: (step.get(k(t), 1), RANK[t["priority"]], t["issue"], k(t))))
