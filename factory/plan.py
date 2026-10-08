"""A person's plan for a repository's tickets: priorities they pinned, milestones, and the order of operations they lead to.

A pinned priority is a person's choice (high, normal or low) made on the Tickets page or with a priority label in the label
editor. The project manager never changes a pinned priority; "Let the project manager decide" takes the pin off. Normal is a pin
of its own, so a person can keep a ticket at normal although no label says so.

Milestones are named, ordered groups of tickets kept per repository in the factory's database (so local and GitHub tickets work
alike, and nothing is written to GitHub). The roadmap orders tickets into steps: a ticket's step is one more than the latest step
of the tickets it waits for; within a step, higher priority comes first. There are no dates, only what comes before what.
"""
import sqlite3
import time

PRIORITIES = ("high", "normal", "low")
LABEL_FOR = {"high": "priority: high", "normal": "", "low": "priority: low"}
FROM_LABEL = {"priority: high": "high", "priority: low": "low"}
RANK = {"high": 0, "normal": 1, "low": 2}
MAX_MILESTONES, MAX_NAME = 30, 60


def ensure_tables(db) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS priority_pins (
            repo TEXT NOT NULL, issue INTEGER NOT NULL, priority TEXT NOT NULL CHECK (priority IN ('high','normal','low')),
            at REAL NOT NULL, PRIMARY KEY (repo, issue));
        CREATE TABLE IF NOT EXISTS milestones (
            repo TEXT NOT NULL, name TEXT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY (repo, name));
        CREATE TABLE IF NOT EXISTS ticket_milestones (
            repo TEXT NOT NULL, issue INTEGER NOT NULL, milestone TEXT NOT NULL, PRIMARY KEY (repo, issue));
    """)
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


# ---------------------------------------------------------------- milestones
def milestones(db, repo: str) -> list[str]:
    return [n for (n,) in _read(db, "SELECT name FROM milestones WHERE repo=? ORDER BY position, name", (repo,))]


def clean_name(name: str) -> str:
    return " ".join((name or "").split())[:MAX_NAME]


def add_milestone(db, repo: str, name: str) -> bool:
    name = clean_name(name)
    have = milestones(db, repo)
    if not name or name in have or len(have) >= MAX_MILESTONES:
        return False
    ensure_tables(db)
    db.execute("INSERT INTO milestones VALUES (?,?,?)", (repo, name, len(have)))
    db.commit()
    return True


def move_milestone(db, repo: str, name: str, step: int) -> bool:
    have = milestones(db, repo)
    if name not in have:
        return False
    i = have.index(name)
    j = max(0, min(len(have) - 1, i + step))
    have.insert(j, have.pop(i))
    db.executemany("UPDATE milestones SET position=? WHERE repo=? AND name=?", [(k, repo, n) for k, n in enumerate(have)])
    db.commit()
    return i != j


def delete_milestone(db, repo: str, name: str) -> bool:
    ensure_tables(db)
    cur = db.execute("DELETE FROM milestones WHERE repo=? AND name=?", (repo, name))
    db.execute("DELETE FROM ticket_milestones WHERE repo=? AND milestone=?", (repo, name))
    db.commit()
    return bool(cur.rowcount)


def ticket_milestones(db, repo: str) -> dict:
    """issue -> milestone name."""
    return {n: m for n, m in _read(db, "SELECT issue, milestone FROM ticket_milestones WHERE repo=?", (repo,))}


def set_ticket_milestone(db, repo: str, issue: int, name: str) -> bool:
    """name '' takes the ticket out of its milestone. False: no such milestone."""
    ensure_tables(db)
    if not name:
        db.execute("DELETE FROM ticket_milestones WHERE repo=? AND issue=?", (repo, int(issue)))
    elif name in milestones(db, repo):
        db.execute("INSERT OR REPLACE INTO ticket_milestones VALUES (?,?,?)", (repo, int(issue), name))
    else:
        return False
    db.commit()
    return True


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
    tickets: {issue, priority, done, waits, state}."""
    open_ = {t["issue"] for t in tickets if not t["done"]}
    todo = [t for t in tickets if not t["done"] and t.get("state") not in ("working", "prs")]       # already being worked on
    ready = lambda t: not any(b in open_ for b in t["waits"])
    key = lambda t: (RANK[t["priority"]], step.get(t["issue"], 1), t["issue"])
    return (sorted((t for t in todo if ready(t)), key=key)
            + sorted((t for t in todo if not ready(t)), key=lambda t: (step.get(t["issue"], 1), RANK[t["priority"]], t["issue"])))
