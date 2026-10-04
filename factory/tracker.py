"""The local ticket tracker, and the import of GitHub issues into it.

A local ticket lives in the factory's own database under a configured repository's name, with a number from a reserved range
(LOCAL_BASE + n, shown as L-n), so every (repo, issue) table keeps working. `Hub` is a GitHub client whose issue-side calls go to
the local store for such a number and to GitHub for any other; it returns GitHub-shaped dicts, so the pipeline reads both alike.

Trust: a local trigger label counts only if the last 'labeled' event for it was written by the admin UI or the factory itself
(`TRUSTED_ACTORS`). Imported labels and comments carry the actor 'imported', which never counts. Stage documents and answers are
trusted only when written by the factory (author FACTORY_AUTHOR, read back as the factory's GitHub login)."""
import logging
import sqlite3
import time
from datetime import datetime, timezone

from .github import GitHub

log = logging.getLogger("factory.tracker")

LOCAL_BASE = 100_000_000
MOVED_LABEL = "factory:moved"
FACTORY_AUTHOR, UI_ACTOR, IMPORTED = "@factory", "ui", "imported"
FACTORY_ACTOR = "factory"
TRUSTED_ACTORS = frozenset({UI_ACTOR, FACTORY_ACTOR})
MAX_BULK = 25
MOVED_COMMENT = "Moved to the factory's local tracker as {ref}.{link} This issue is no longer watched by the factory."
RUNNING = ("running", "queued", "starting")


def is_local(number: int) -> bool:
    return int(number) >= LOCAL_BASE


def display(number: int) -> str:
    return f"L-{int(number) - LOCAL_BASE}" if is_local(number) else f"#{int(number)}"


def ref(repo: str, number: int) -> str:
    """How a ticket is named in text that leaves the factory (PR bodies, commit messages): never `repo#100000012`, which
    GitHub would link to an unrelated issue."""
    return f"factory ticket {display(number)} of {repo}" if is_local(number) else f"{repo}#{number}"


def ensure_tables(db: sqlite3.Connection) -> None:
    db.executescript("""
        CREATE TABLE IF NOT EXISTS local_tickets (
            repo TEXT NOT NULL, number INTEGER NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL DEFAULT '',
            state TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open','closed')), author TEXT NOT NULL DEFAULT '',
            created REAL NOT NULL, updated REAL NOT NULL, PRIMARY KEY (repo, number));
        CREATE TABLE IF NOT EXISTS local_labels (
            repo TEXT NOT NULL, number INTEGER NOT NULL, label TEXT NOT NULL, PRIMARY KEY (repo, number, label));
        CREATE TABLE IF NOT EXISTS local_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, repo TEXT NOT NULL, number INTEGER NOT NULL, author TEXT NOT NULL,
            body TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS local_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, repo TEXT NOT NULL, number INTEGER NOT NULL, actor TEXT NOT NULL,
            action TEXT NOT NULL, label TEXT NOT NULL, at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS local_imports (
            repo TEXT NOT NULL, gh_number INTEGER NOT NULL, local_number INTEGER NOT NULL, close INTEGER NOT NULL DEFAULT 0,
            step INTEGER NOT NULL DEFAULT 0, moved_at REAL NOT NULL, PRIMARY KEY (repo, gh_number));
        CREATE TABLE IF NOT EXISTS import_requests (
            repo TEXT NOT NULL, gh_number INTEGER NOT NULL, close INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL,
            PRIMARY KEY (repo, gh_number));
    """)
    db.commit()


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def request_import(path: str, repo: str, numbers: list[int], close: bool) -> None:
    """Queue GitHub issues for the orchestrator to move (the admin UI has no write path to GitHub of its own for this).
    The caller has validated repo and numbers."""
    db = sqlite3.connect(path, timeout=10)
    try:
        ensure_tables(db)
        db.executemany("INSERT OR REPLACE INTO import_requests VALUES (?,?,?,?)",
                       [(repo, int(n), int(bool(close)), time.time()) for n in numbers[:MAX_BULK]])
        db.commit()
    finally:
        db.close()


class LocalTracker:
    """Issue-side calls against the database, in the shapes GitHub returns. `actor` is recorded on every label change."""

    def __init__(self, db, actor: str = FACTORY_ACTOR):
        self.db, self.actor = db, actor
        ensure_tables(db)

    def _touch(self, repo: str, number: int) -> None:
        self.db.execute("UPDATE local_tickets SET updated=MAX(?, updated + 0.000001) WHERE repo=? AND number=?",
                        (time.time(), repo, number))

    def create(self, repo: str, title: str, body: str = "", author: str = UI_ACTOR, labels: tuple = (), label_actor: str | None = None) -> int:
        number = (self.db.execute("SELECT MAX(number) FROM local_tickets WHERE repo=?", (repo,)).fetchone()[0] or LOCAL_BASE) + 1   # tickets are never deleted, so a number is never reused
        now = time.time()
        self.db.execute("INSERT INTO local_tickets VALUES (?,?,?,?,'open',?,?,?)", (repo, number, title, body, author, now, now))
        for label in labels:
            self._label(repo, number, label, label_actor or self.actor)
        self.db.commit()
        return number

    def _label(self, repo: str, number: int, label: str, actor: str) -> None:
        self.db.execute("INSERT OR IGNORE INTO local_labels VALUES (?,?,?)", (repo, number, label))
        self.db.execute("INSERT INTO local_events (repo, number, actor, action, label, at) VALUES (?,?,?,?,?,?)",
                        (repo, number, actor, "labeled", label, time.time()))

    def issue(self, repo: str, number: int) -> dict:
        t = self.db.execute("SELECT title, body, state, author, created, updated FROM local_tickets WHERE repo=? AND number=?",
                            (repo, number)).fetchone()
        if t is None:
            raise LookupError(f"no local ticket {display(number)}")
        labels = [r[0] for r in self.db.execute("SELECT label FROM local_labels WHERE repo=? AND number=? ORDER BY label", (repo, number))]
        n = self.db.execute("SELECT COUNT(*) FROM local_comments WHERE repo=? AND number=?", (repo, number)).fetchone()[0]
        return {"number": number, "title": t[0], "body": t[1], "state": t[2], "labels": [{"name": x} for x in labels],
                "user": {"login": t[3]}, "created_at": _iso(t[4]), "updated_at": _iso(t[5]), "html_url": "", "comments": n,
                "local": True}

    def labeled(self, repo: str, label: str) -> list[dict]:
        nums = [r[0] for r in self.db.execute(
            "SELECT t.number FROM local_tickets t JOIN local_labels l ON l.repo=t.repo AND l.number=t.number "
            "WHERE t.repo=? AND t.state='open' AND l.label=? ORDER BY t.number", (repo, label))]
        return [self.issue(repo, n) for n in nums]

    def comments(self, repo: str, number: int, factory_login: str | None = None) -> list[dict]:
        out = []
        for cid, author, body, created in self.db.execute(
                "SELECT id, author, body, created FROM local_comments WHERE repo=? AND number=? ORDER BY id", (repo, number)):
            if author == FACTORY_AUTHOR and factory_login:
                author = factory_login                  # what the stage-output check compares with the factory's login
            out.append({"id": cid, "body": body, "user": {"login": author}, "created_at": _iso(created)})
        return out

    def add_comment(self, repo: str, number: int, author: str, body: str) -> None:
        self.db.execute("INSERT INTO local_comments (repo, number, author, body, created) VALUES (?,?,?,?,?)",
                        (repo, number, author, body, time.time()))
        self._touch(repo, number)
        self.db.commit()

    def add_labels(self, repo: str, number: int, labels: list[str]) -> None:
        for label in labels:
            self._label(repo, number, label, self.actor)
        self._touch(repo, number)
        self.db.commit()

    def remove_label(self, repo: str, number: int, label: str) -> None:
        self.db.execute("DELETE FROM local_labels WHERE repo=? AND number=? AND label=?", (repo, number, label))
        self.db.execute("INSERT INTO local_events (repo, number, actor, action, label, at) VALUES (?,?,?,?,?,?)",
                        (repo, number, self.actor, "unlabeled", label, time.time()))
        self._touch(repo, number)
        self.db.commit()

    def label_actor(self, repo: str, number: int, label: str) -> str | None:
        """Who applied `label` last, but only while it is on the ticket (a removed label has no applier)."""
        if not self.db.execute("SELECT 1 FROM local_labels WHERE repo=? AND number=? AND label=?", (repo, number, label)).fetchone():
            return None
        r = self.db.execute("SELECT actor FROM local_events WHERE repo=? AND number=? AND label=? AND action='labeled' "
                            "ORDER BY id DESC LIMIT 1", (repo, number, label)).fetchone()
        return r[0] if r else None

    def set_state(self, repo: str, number: int, state: str) -> None:
        self.db.execute("UPDATE local_tickets SET state=? WHERE repo=? AND number=?", (state, repo, number))
        self._touch(repo, number)
        self.db.commit()


class Hub(GitHub):
    """GitHub for real issues and pull requests; the local store for local ticket numbers. Built once by the orchestrator
    (actor 'factory') and per request by the admin UI (actor 'ui')."""

    def __init__(self, token: str | None, db_path: str | None = None, actor: str = FACTORY_ACTOR):
        super().__init__(token)
        self.db_path, self.actor = db_path, actor
        self.github_due = True              # the orchestrator turns this off on polls that must not read GitHub

    def _local(self) -> LocalTracker:
        from . import db as dbm
        return LocalTracker(dbm.local(self.db_path), self.actor)

    def _is_local(self, issue: int) -> bool:
        return bool(self.db_path) and is_local(issue)

    def labeled_issues(self, repo: str, label: str) -> list[dict]:
        local = self._local().labeled(repo, label) if self.db_path else []
        if not self.github_due:
            return local
        try:
            remote = super().labeled_issues(repo, label)
        except OSError:
            if not local:
                raise
            log.warning("GitHub could not be read; handling local tickets only")
            return local
        moved = self._moved(repo)
        return local + [i for i in remote if i["number"] not in moved
                        and MOVED_LABEL not in {x.get("name") for x in i.get("labels", [])}]

    def _moved(self, repo: str) -> set[int]:
        if not self.db_path:
            return set()
        from . import db as dbm
        db = dbm.local(self.db_path)
        ensure_tables(db)
        return {r[0] for r in db.execute("SELECT gh_number FROM local_imports WHERE repo=?", (repo,))}

    def get_issue(self, repo: str, issue: int) -> dict:
        return self._local().issue(repo, issue) if self._is_local(issue) else super().get_issue(repo, issue)

    def issue_comments(self, repo: str, issue: int) -> list[dict]:
        if not self._is_local(issue):
            return super().issue_comments(repo, issue)
        try:
            login = self.login()
        except Exception:
            login = None                    # no token or no network: factory comments keep the '@factory' author
        return self._local().comments(repo, issue, login)

    def comment(self, repo: str, issue: int, body: str) -> str:
        if not self._is_local(issue):
            return super().comment(repo, issue, body)
        self._local().add_comment(repo, issue, FACTORY_AUTHOR, body)      # the UI posts as the factory too, as it does on GitHub
        return ""

    def add_labels(self, repo: str, issue: int, labels: list[str]) -> None:
        if self._is_local(issue):
            return self._local().add_labels(repo, issue, labels)
        super().add_labels(repo, issue, labels)

    def remove_label(self, repo: str, issue: int, label: str) -> None:
        if self._is_local(issue):
            return self._local().remove_label(repo, issue, label)
        super().remove_label(repo, issue, label)

    def label_actor(self, repo: str, issue: int, label: str) -> str | None:
        return self._local().label_actor(repo, issue, label) if self._is_local(issue) else super().label_actor(repo, issue, label)

    def update_issue(self, repo: str, number: int, body: str | None = None, state: str | None = None) -> dict:
        if not self._is_local(number):
            return super().update_issue(repo, number, body, state)
        if state in ("open", "closed"):
            self._local().set_state(repo, number, state)
        return self.get_issue(repo, number)


# ------------------------------------------------------------------ import
def refusal(db, repo: str, issue: dict) -> str | None:
    """Why this GitHub issue cannot be moved now, or None. Fixed messages."""
    num = issue["number"]
    if "pull_request" in issue:
        return "That is a pull request, not an issue."
    if db.execute("SELECT 1 FROM local_imports WHERE repo=? AND gh_number=?", (repo, num)).fetchone():
        return "That issue was already moved."
    names = {x.get("name") for x in issue.get("labels", [])}
    if MOVED_LABEL in names:
        return "That issue was already moved."
    if issue.get("state") == "closed":
        return "That issue is closed."
    if any(n and (n.startswith("factory:working") or n == "factory:needs-answers") for n in names):
        return "That issue is busy: wait for the run to finish or answer its question first."
    r = db.execute("SELECT status FROM runs WHERE repo=? AND issue=? ORDER BY id DESC LIMIT 1", (repo, num)).fetchone()
    if r and r[0] in RUNNING:
        return "That issue is busy: wait for the run to finish or answer its question first."
    return None


def _import_one(cfg, gh: GitHub, db, repo: str, num: int, close: bool, triggers: frozenset, emit, ui_url: str | None) -> str | None:
    """Move one issue. Returns None when finished, or a reason to stop (a refusal is final, a GitHub failure is retried)."""
    local = LocalTracker(db, IMPORTED)
    row = db.execute("SELECT local_number, close, step FROM local_imports WHERE repo=? AND gh_number=?", (repo, num)).fetchone()
    issue = gh.get_issue(repo, num)
    if row is None:
        if (why := refusal(db, repo, issue)):
            return why
        comments = gh.issue_comments(repo, num)
        login = gh.login() if gh.token else ""
        skip = triggers | {cfg.auto_label}
        names = [x["name"] for x in issue.get("labels", []) if x.get("name") and x["name"] not in skip
                 and not x["name"].startswith("factory:working") and x["name"] != "factory:needs-answers"]
        author = (issue.get("user") or {}).get("login") or "unknown"
        body = f"Imported from {repo}#{num} (opened by {author}).\n\n{issue.get('body') or ''}"
        number = local.create(repo, issue.get("title") or f"{repo}#{num}", body, author=IMPORTED, labels=names)
        for c in comments:                      # only the factory's own comments keep the factory's authorship
            who = (c.get("user") or {}).get("login") or "unknown"
            if login and who == login:
                local.add_comment(repo, number, FACTORY_AUTHOR, c.get("body") or "")
            else:
                local.add_comment(repo, number, IMPORTED, f"Comment by {who} on GitHub:\n\n{c.get('body') or ''}")
        db.execute("INSERT INTO local_imports VALUES (?,?,?,?,0,?)", (repo, num, number, int(close), time.time()))
        db.commit()
        row = (number, int(close), 0)
    number, close, step = row
    if step < 1:                                # the trigger labels go first, so the issue cannot start again
        for label in sorted(triggers | {cfg.auto_label}):
            gh.remove_label(repo, num, label)
        gh.add_labels(repo, num, [MOVED_LABEL])
        db.execute("UPDATE local_imports SET step=1 WHERE repo=? AND gh_number=?", (repo, num))
        db.commit()
    if step < 2:
        link = f" See {ui_url.rstrip('/')}/labels/issue?repo={repo}&n={number}." if ui_url else ""
        gh.comment(repo, num, MOVED_COMMENT.format(ref=display(number), link=link))
        db.execute("UPDATE local_imports SET step=2 WHERE repo=? AND gh_number=?", (repo, num))
        db.commit()
    if step < 3:
        if close:
            gh.update_issue(repo, num, state="closed")
        db.execute("UPDATE local_imports SET step=3 WHERE repo=? AND gh_number=?", (repo, num))
        db.commit()
    emit("import", f"moved {repo}#{num} to local ticket {display(number)}", repo, number)
    return None


def process_imports(cfg, gh: GitHub, db, triggers: frozenset, emit) -> None:
    """Move the issues a person queued in the UI. A refused request is dropped with an event; a GitHub failure keeps it queued."""
    ensure_tables(db)
    for repo, num, close in db.execute("SELECT repo, gh_number, close FROM import_requests ORDER BY created").fetchall():
        done = True
        try:
            if repo not in cfg.repos:
                why = "That repository is not configured."
            else:
                why = _import_one(cfg, gh, db, repo, num, bool(close), triggers, emit, cfg.telegram_ui_url)
            if why:
                emit("import:refused", f"{repo}#{num} was not moved: {why}", repo, num)
        except Exception as e:
            done = False
            log.warning("import of %s#%s failed (%s); it stays queued", repo, num, type(e).__name__)
        if done:
            db.execute("DELETE FROM import_requests WHERE repo=? AND gh_number=?", (repo, num))
            db.commit()
