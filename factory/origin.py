"""Tell the original GitHub issue what happened to the local ticket that was copied from it.

Importing an issue moves the work to a local ticket (`L-n`) and the factory stops reading the original, so without this nothing
on GitHub shows that the work was done. For a ticket with a `local_imports` row (tickets made in the UI have none, and every
function here then does nothing):

* `note` posts a short outcome comment (the PR links, or that no PR was made) once per `kind`.
* `tick` closes the original once every build PR of the ticket has merged (cleanup.verdict decides), with a last comment.

A GitHub problem is logged and tried again; it never undoes a finished build. Nothing is written in dry-run.
main.py calls `note` from dispatch and `tick` in the poll, after the leftover PR cleanup."""
import logging
import sqlite3
import time

from . import cleanup, tracker
from . import db as dbm

log = logging.getLogger("factory.origin")
MAX_CLOSES = 10                     # originals closed per pass
CLOSED = "closed"                   # the origin_notes kind that marks a finished ticket


def origin(db, repo: str, number: int) -> int | None:
    """The number of the GitHub issue a local ticket was imported from, or None."""
    if not tracker.is_local(number):
        return None
    try:
        row = db.execute("SELECT gh_number FROM local_imports WHERE repo=? AND local_number=?", (repo, int(number))).fetchone()
    except sqlite3.OperationalError:               # the tables are made when the orchestrator starts
        return None
    return int(row[0]) if row else None


def refs(cfg, repo: str, number: int) -> str:
    """A "Refs owner/repo#N" paragraph for a PR body, so GitHub lists the PR on the original issue; "" for any other ticket.
    Never `Closes`: the factory closes the original itself once every PR has merged."""
    if not tracker.is_local(number):
        return ""
    try:
        gh_number = origin(dbm.local(cfg.db_path), repo, number)
    except (sqlite3.Error, OSError):
        return ""
    return f"Refs {repo}#{gh_number}\n\n" if gh_number else ""


def _posted(db, repo: str, gh_number: int, kind: str) -> bool:
    return db.execute("SELECT 1 FROM origin_notes WHERE repo=? AND gh_number=? AND kind=?", (repo, gh_number, kind)).fetchone() is not None


def _record(db, repo: str, gh_number: int, kind: str) -> None:
    db.execute("INSERT OR IGNORE INTO origin_notes VALUES (?,?,?,?)", (repo, gh_number, kind, time.time()))
    db.commit()


def ui_link(cfg, repo: str, number: int) -> str:
    base = getattr(cfg, "telegram_ui_url", None)
    return f" Details: {base.rstrip('/')}/labels/issue?repo={repo}&n={number}" if base else ""


def note(cfg, gh, db, repo: str, number: int, kind: str, body: str) -> bool:
    """Comment on the original issue, once per `kind`. True when the comment is there (now or earlier)."""
    gh_number = origin(db, repo, number)
    if gh_number is None or cfg.dry_run:
        return False
    try:
        tracker.ensure_tables(db)
        if _posted(db, repo, gh_number, kind):
            return True
        gh.comment(repo, gh_number, body + ui_link(cfg, repo, number))
        _record(db, repo, gh_number, kind)
        return True
    except Exception:
        log.warning("could not write to %s#%s about %s", repo, gh_number, tracker.display(number), exc_info=True)
        return False


def _links(db, repo: str, number: int) -> str:
    rows = db.execute("SELECT repo, number FROM pr_cleanup WHERE issue_repo=? AND issue_num=? AND role='build' ORDER BY repo, number",
                      (repo, number)).fetchall()
    return " ".join(f"https://github.com/{r}/pull/{n}" for r, n in rows)


def tick(cfg, gh, conn, busy, emit=lambda *a, **k: None) -> None:
    """Close the original issue of each imported ticket whose build PRs have all merged."""
    if cfg.dry_run:
        return
    tracker.ensure_tables(conn)
    rows = conn.execute("SELECT repo, gh_number, local_number FROM local_imports WHERE step >= 3 AND NOT EXISTS ("
                        "SELECT 1 FROM origin_notes n WHERE n.repo=local_imports.repo AND n.gh_number=local_imports.gh_number AND n.kind=?) "
                        "ORDER BY moved_at", (CLOSED,)).fetchall()
    closes = 0
    for repo, gh_number, local in rows:
        if closes >= MAX_CLOSES:
            break
        try:
            cleanup.seed(conn, repo, int(local))
            if not conn.execute("SELECT 1 FROM pr_cleanup WHERE issue_repo=? AND issue_num=? AND role='build'", (repo, int(local))).fetchone():
                continue                            # nothing was built: there is nothing to wait for or to report
            verdict, _ = cleanup.verdict(conn, gh, repo, int(local), busy)
            if verdict != "ready":
                continue
            links = _links(conn, repo, int(local))
            if not note(cfg, gh, conn, repo, int(local), "merged",      # its own kind: a failed close below does not post it twice
                        f"All pull requests for {tracker.display(int(local))} have merged: {links}. Closing this issue."):
                continue
            if gh.get_issue(repo, gh_number).get("state") != "closed":
                gh.update_issue(repo, gh_number, state="closed")
            _record(conn, repo, gh_number, CLOSED)
            closes += 1
            emit("origin", f"closed {repo}#{gh_number}: {tracker.display(int(local))} has merged", repo, int(local))
        except Exception:
            log.warning("could not close %s#%s", repo, gh_number, exc_info=True)
