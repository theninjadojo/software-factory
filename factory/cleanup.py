"""Close a ticket's leftover pull requests once its build has merged.

A ticket can leave PRs behind: the designer's draft mockup PRs, and PRs superseded when the ticket was sent back to a stage. Once
every build PR of the ticket has merged, the factory closes those leftovers itself ([cleanup] enabled, on by default). Only PRs
the factory recorded for that ticket in its own database are considered, never a PR named in ticket or comment text, and
GitHub.close_pr re-reads each one and closes it only while it is open, unmerged and on a `factory/` branch of the same
repository. Branches are never deleted. The ticket's Done state does not wait for this.

* A failed close is tried again on later passes (a GitHub timeout does not count); after MAX_ATTEMPTS, or a PR that is not the
  factory's, the PR is left for a person ("needs manual closing", one alert).
* A build PR closed without a merge, or a kept child of a split closed without a merged build PR, blocks the ticket's leftovers:
  nothing is closed and a person is told once.
* A split parent's leftovers close once every kept child's build has merged (and the parent's own build PRs, if any).
main.py runs `tick` in the poll, after the release waves."""
import logging
import sqlite3
import time

from . import split, tracker
from .github import _transient

log = logging.getLogger("factory.cleanup")
MAX_ATTEMPTS = 3                    # failed closes of one PR, then a person closes it
MAX_CLOSES = 20                     # PR closes per pass, across every ticket
MAX_TICKETS = 50                    # tickets looked at per pass
BACKFILL_DAYS = 30                  # on the first pass, tickets whose PRs changed this recently are picked up
SPLIT_DEPTH = 3
FRESH, EVERY_FRESH, EVERY_OLD = 7 * 86400, 15 * 60, 6 * 3600    # how often GitHub is read for a ticket: often in its first week
LEFTOVER = ("design", "superseded")
LEFTOVER_PR = ("Closed by the factory: the build pull requests for {ticket} have merged. The mockups stay in the factory. "
               "The branch is kept.")


def ensure_tables(db) -> None:
    db.execute(
        """CREATE TABLE IF NOT EXISTS pr_cleanup (
            repo TEXT NOT NULL, number INTEGER NOT NULL, issue_repo TEXT NOT NULL, issue_num INTEGER NOT NULL,
            role TEXT NOT NULL,                     -- build | design | superseded
            state TEXT NOT NULL DEFAULT 'pending',  -- build: pending|merged|unmerged; leftover: pending|closed|gone|needs-person|blocked
            attempts INTEGER NOT NULL DEFAULT 0,    -- failed closes (a GitHub timeout does not count)
            detail TEXT NOT NULL DEFAULT '',        -- built from factory values only (an HTTP status, a ticket number)
            checked REAL NOT NULL DEFAULT 0, seeded REAL NOT NULL, updated REAL NOT NULL,
            PRIMARY KEY (repo, number))"""
    )
    db.execute("CREATE INDEX IF NOT EXISTS pr_cleanup_ticket ON pr_cleanup (issue_repo, issue_num)")
    db.commit()


def _roles(conn, repo: str, num: int) -> dict[tuple[str, int], str]:
    """Every PR the factory recorded for a ticket and its role, from the database only."""
    from . import db as dbm
    args = (repo, num)
    out: dict[tuple[str, int], str] = {}
    for r, n, st in conn.execute("SELECT repo, number, state FROM pr_conflicts WHERE issue_repo=? AND issue_num=?", args):
        out[(r, int(n))] = "superseded" if st == "superseded" else "build"
    for r, n, st in conn.execute("SELECT repo, number, status FROM prs WHERE issue_repo=? AND issue_num=?", args):
        if st == "superseded" or (r, int(n)) not in out:
            out[(r, int(n))] = "superseded" if st == "superseded" else "build"
    for key in dbm.design_prs(conn, repo, num):
        out[key] = "design"
    return out


def seed(conn, repo: str, num: int, now: float | None = None) -> None:
    """Record a ticket's PRs. A build PR that has since become a leftover (the ticket was sent back) starts again as pending."""
    now = time.time() if now is None else now
    for (r, n), role in _roles(conn, repo, num).items():
        conn.execute("INSERT INTO pr_cleanup (repo, number, issue_repo, issue_num, role, seeded, updated) VALUES (?,?,?,?,?,?,?) "
                     "ON CONFLICT(repo, number) DO UPDATE SET role=excluded.role, state='pending', attempts=0, detail='', "
                     "updated=excluded.updated WHERE pr_cleanup.role='build' AND excluded.role!='build'",
                     (r, n, repo, num, role, now, now))
    conn.commit()


def _seed_recent(conn, now: float) -> None:
    """Pick up the tickets whose PRs changed since the last pass (on the first pass, the last BACKFILL_DAYS)."""
    from . import db as dbm
    st = dbm.get_status(conn)
    if "cleanup:since" not in st:
        dbm.set_status(conn, "cleanup:since", str(now))
    last = float((st.get("cleanup:seeded") or {}).get("value") or 0)
    since = last - 3600 if last else now - BACKFILL_DAYS * 86400       # an hour of overlap: a row written during the last pass
    tickets = conn.execute("SELECT issue_repo, issue_num FROM pr_conflicts WHERE updated > ? UNION "
                           "SELECT issue_repo, issue_num FROM prs WHERE updated > ? UNION "
                           "SELECT r.repo, r.issue FROM design_files f JOIN runs r ON r.id=f.run_id WHERE f.created > ? AND f.pr != ''",
                           (since, since, since)).fetchall()
    for repo, num in tickets:
        seed(conn, repo, int(num), now)
    dbm.set_status(conn, "cleanup:seeded", str(now))


def _set(conn, repo: str, number: int, **fields) -> None:
    sets = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE pr_cleanup SET {sets}, updated=? WHERE repo=? AND number=?", (*fields.values(), time.time(), repo, number))
    conn.commit()


def _rows(conn, repo: str, num: int) -> list[dict]:
    cols = ("repo", "number", "role", "state", "attempts", "checked", "seeded")
    return [dict(zip(cols, r)) for r in conn.execute(f"SELECT {', '.join(cols)} FROM pr_cleanup WHERE issue_repo=? AND issue_num=? "
                                                     "ORDER BY repo, number", (repo, num))]


def _merged(conn, gh, row: dict) -> str:
    """merged | unmerged | pending, for one build PR; a final answer is stored."""
    if row["state"] in ("merged", "unmerged"):
        return row["state"]
    hit = conn.execute("SELECT status, summary FROM prs WHERE repo=? AND number=?", (row["repo"], row["number"])).fetchone()
    if hit and hit[0] == "closed" and hit[1] == "merged":
        state = "merged"
    else:
        try:
            pr = gh.get_pr(row["repo"], row["number"])
        except Exception:
            log.warning("could not read %s#%s for the cleanup check", row["repo"], row["number"])
            return "pending"
        state = "merged" if pr.get("merged") or pr.get("merged_at") else "unmerged" if pr.get("state") == "closed" else "pending"
    if state != "pending":
        _set(conn, row["repo"], row["number"], state=state)
    return state


def _built(conn, gh, repo: str, num: int, busy, need_build: bool = True) -> tuple[str, str]:
    """(ready | wait | blocked, why) for one ticket's own build PRs."""
    builds = [r for r in _rows(conn, repo, num) if r["role"] == "build"]
    if need_build and not builds:
        return "wait", ""
    states = [_merged(conn, gh, r) for r in builds]
    if "unmerged" in states:
        return "blocked", "a build PR was closed without a merge"
    if "pending" in states or busy(repo, num):
        return "wait", ""
    if conn.execute("SELECT 1 FROM waves WHERE issue_repo=? AND issue_num=? AND state NOT IN ('done', 'closed')", (repo, num)).fetchone():
        return "wait", ""                       # a release wave still has repositories to build
    return "ready", ""


def verdict(conn, gh, repo: str, num: int, busy, cache: dict | None = None, depth: int = 0) -> tuple[str, str]:
    """(ready | wait | blocked, why): whether a ticket's leftover PRs may be closed now."""
    cache = {} if cache is None else cache
    try:
        prop = split.for_ticket(conn, repo, num, ("done",))
    except sqlite3.OperationalError:
        prop = None
    if not prop:
        return _built(conn, gh, repo, num, busy)
    if depth >= SPLIT_DEPTH:
        return "wait", ""
    kids = split.kept(split.items(conn, prop["id"]))
    if not kids or any(k["child_issue"] is None for k in kids):
        return "wait", ""
    waiting = False
    for k in kids:
        crepo, cnum = k["child_repo"], int(k["child_issue"])
        seed(conn, crepo, cnum)
        if split.for_ticket(conn, crepo, cnum, ("done",)):
            v, why = verdict(conn, gh, crepo, cnum, busy, cache, depth + 1)
        elif not any(r["role"] == "build" for r in _rows(conn, crepo, cnum)):
            if split._closed(gh, crepo, cnum, cache):          # an unreadable ticket counts as open, so this waits
                return "blocked", f"split child {tracker.display(cnum)} was closed without a merged build PR"
            v, why = "wait", ""
        else:
            v, why = _built(conn, gh, crepo, cnum, busy)
            if v == "blocked":
                why = f"split child {tracker.display(cnum)}: {why}"
        if v == "blocked":
            return v, why
        waiting = waiting or v != "ready"
    if waiting:
        return "wait", ""
    return _built(conn, gh, repo, num, busy, need_build=False)    # an umbrella may have no build PR of its own


def _due(rows: list[dict], now: float) -> bool:
    if not rows:
        return False
    every = EVERY_FRESH if now - min(r["seeded"] for r in rows) < FRESH else EVERY_OLD
    return now - max(r["checked"] for r in rows) >= every


def _error(e: Exception) -> str:
    """What is kept of a failure: its kind only, never a message that could carry a URL or a token."""
    code = getattr(e, "code", None)
    return f"HTTP {int(code)}" if isinstance(code, int) else type(e).__name__


def _mark_closed(conn, repo: str, number: int) -> None:
    """Keep the factory's other records of the PR in step."""
    now = time.time()
    conn.execute("UPDATE prs SET status='closed', updated=? WHERE repo=? AND number=? AND status!='closed'", (now, repo, number))
    conn.execute("UPDATE pr_conflicts SET state='closed', updated=? WHERE repo=? AND number=? AND state NOT IN ('closed', 'superseded')",
                 (now, repo, number))
    conn.commit()


def _close(conn, gh, row: dict, ticket: str, notify, emit, issue_repo: str, issue_num: int) -> str:
    """Close one leftover PR: closed | gone | needs-person | pending (try again on a later pass)."""
    repo, number = row["repo"], row["number"]
    try:
        out = gh.close_pr(repo, number)
    except Exception as e:
        if _transient(e):
            log.warning("GitHub did not answer closing the leftover PR %s#%s; trying again later", repo, number)
            return "pending"
        attempts = row["attempts"] + 1
        log.warning("could not close the leftover PR %s#%s (%s, attempt %d of %d)", repo, number, _error(e), attempts, MAX_ATTEMPTS)
        if attempts < MAX_ATTEMPTS:
            _set(conn, repo, number, attempts=attempts, detail=_error(e))
            return "pending"
        return _needs_person(conn, repo, number, attempts, f"{_error(e)} after {attempts} attempts", notify, emit, issue_repo, issue_num)
    if out == "refused":
        return _needs_person(conn, repo, number, row["attempts"], "not on a factory/ branch of this repository", notify, emit,
                             issue_repo, issue_num)
    _set(conn, repo, number, state=out, detail="")
    if out == "closed":
        _mark_closed(conn, repo, number)
        try:                                    # after the row is closed, so a restart never comments twice
            gh.comment(repo, number, LEFTOVER_PR.format(ticket=ticket))
        except Exception:
            log.warning("could not comment on the closed leftover PR %s#%s", repo, number)
    return out


def _needs_person(conn, repo, number, attempts, detail, notify, emit, issue_repo, issue_num) -> str:
    _set(conn, repo, number, state="needs-person", attempts=attempts, detail=detail)
    emit("cleanup", f"leftover PR {repo}#{number} needs manual closing: {detail}", issue_repo, issue_num)
    notify(f"Leftover PR needs manual closing: {repo}#{number} (ticket {tracker.ref(issue_repo, issue_num)}): {detail}\n"
           f"https://github.com/{repo}/pull/{number}", "failure")
    return "needs-person"


def tick(cfg, gh, conn, notify, emit, busy, now: float | None = None) -> None:
    """One pass. `notify(text, event)` sends an alert; `busy(repo, num)` is true while a job runs for the ticket."""
    if not cfg.cleanup.enabled:
        return
    now = time.time() if now is None else now
    _seed_recent(conn, now)
    tickets = conn.execute("SELECT issue_repo, issue_num FROM pr_cleanup WHERE state='pending' AND role IN ('design', 'superseded') "
                           "GROUP BY issue_repo, issue_num ORDER BY MIN(checked), MIN(seeded) LIMIT ?", (MAX_TICKETS,)).fetchall()
    closes, cache = 0, {}
    for repo, num in tickets:
        if closes >= MAX_CLOSES:
            break
        num = int(num)
        rows = [r for r in _rows(conn, repo, num) if r["role"] in LEFTOVER and r["state"] == "pending"]
        if not _due(rows, now):
            continue
        try:
            v, why = verdict(conn, gh, repo, num, busy, cache)
        except Exception:
            log.exception("cleanup check failed for %s#%s", repo, num)
            continue
        if v == "wait":
            conn.execute("UPDATE pr_cleanup SET checked=? WHERE issue_repo=? AND issue_num=? AND state='pending' AND role IN ('design', 'superseded')",
                         (now, repo, num))
            conn.commit()
            continue
        if v == "blocked":
            for r in rows:
                _set(conn, r["repo"], r["number"], state="blocked", detail=why[:200])
            emit("cleanup", f"{_n(len(rows))} left for a person to close: {why}", repo, num)
            notify(f"Leftover PRs need a person: {tracker.ref(repo, num)} has {_n(len(rows))} the factory will not close, "
                   f"because {why}.\n" + "\n".join(f"https://github.com/{r['repo']}/pull/{r['number']}" for r in rows), "failure")
            continue
        done, ticket = 0, tracker.ref(repo, num)
        for r in rows:
            if closes >= MAX_CLOSES:
                break
            closes += 1
            done += _close(conn, gh, r, ticket, notify, emit, repo, num) == "closed"
        if done:
            emit("cleanup", f"closed {_n(done)}: the build has merged", repo, num)


def _n(k: int) -> str:
    return f"{k} leftover PR{'s' if k != 1 else ''}"


def summary(conn, repo: str, num: int) -> str:
    """The cleanup status shown beside Done, e.g. '2 leftover PRs closed · 1 leftover PR needs manual closing'; '' for none."""
    try:
        got = dict(conn.execute("SELECT state, COUNT(*) FROM pr_cleanup WHERE issue_repo=? AND issue_num=? AND role IN ('design', 'superseded') "
                                "GROUP BY state", (repo, int(num))).fetchall())
    except sqlite3.OperationalError:            # an older database (the UI reads it read-only)
        return ""
    parts = []
    if got.get("closed"):
        parts.append(f"{_n(got['closed'])} closed")
    if got.get("needs-person"):
        parts.append(f"{_n(got['needs-person'])} need{'s' if got['needs-person'] == 1 else ''} manual closing")
    if got.get("blocked"):
        parts.append(f"{_n(got['blocked'])} left for a person to close")
    if got.get("pending"):
        parts.append(f"{_n(got['pending'])} to close once the build has merged")
    return " · ".join(parts)
