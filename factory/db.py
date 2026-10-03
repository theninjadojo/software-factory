import re
import sqlite3
import threading
import time

from . import designfiles, jobs, schedules
from .render import png_ok

_local = threading.local()
TOKEN_COLS = ("tokens_in", "tokens_out", "tokens_cache_read", "tokens_cache_write")   # per run; NULL when the harness did not say


def connect(path: str) -> sqlite3.Connection:
    db = sqlite3.connect(path, timeout=10)
    db.execute("PRAGMA journal_mode=WAL")           # the UI reads while the orchestrator writes
    db.execute("PRAGMA busy_timeout=10000")
    db.execute(
        """CREATE TABLE IF NOT EXISTS decisions (
            repo TEXT NOT NULL, issue INTEGER NOT NULL, updated_at TEXT NOT NULL,
            outcome TEXT NOT NULL, detail TEXT NOT NULL, decided_at REAL NOT NULL,
            PRIMARY KEY (repo, issue, updated_at))"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS approvals (
            repo TEXT NOT NULL, issue INTEGER NOT NULL, action TEXT NOT NULL, created REAL NOT NULL,
            PRIMARY KEY (repo, issue))"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS prs (
            repo TEXT NOT NULL, number INTEGER NOT NULL, issue_repo TEXT NOT NULL, issue_num INTEGER NOT NULL,
            status TEXT NOT NULL, rounds INTEGER NOT NULL DEFAULT 0, watch_started REAL NOT NULL,
            updated REAL NOT NULL, summary TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (repo, number))"""
    )
    # Every PR the factory opened (build and design), tracked for merge conflicts whether or not CI is watched. A separate
    # table: `prs` is written positionally and feeds the CI watcher and the reviewer.
    db.execute(
        """CREATE TABLE IF NOT EXISTS pr_conflicts (
            repo TEXT NOT NULL, number INTEGER NOT NULL, issue_repo TEXT NOT NULL, issue_num INTEGER NOT NULL,
            state TEXT NOT NULL DEFAULT 'unknown', notified_sha TEXT NOT NULL DEFAULT '',
            attempts INTEGER NOT NULL DEFAULT 0, detail TEXT NOT NULL DEFAULT '', updated REAL NOT NULL,
            PRIMARY KEY (repo, number))"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, stage TEXT, repo TEXT NOT NULL,
            issue INTEGER NOT NULL, title TEXT NOT NULL DEFAULT '', harness TEXT, model TEXT, effort TEXT,
            classification TEXT NOT NULL DEFAULT '', started REAL NOT NULL, finished REAL,
            status TEXT NOT NULL DEFAULT 'running', detail TEXT NOT NULL DEFAULT '',
            pr_urls TEXT NOT NULL DEFAULT '', output TEXT NOT NULL DEFAULT '', log_tail TEXT NOT NULL DEFAULT '')"""
    )
    db.execute(
        """CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, kind TEXT NOT NULL, repo TEXT, issue INTEGER,
            run_id INTEGER, message TEXT NOT NULL)"""
    )
    db.execute("CREATE INDEX IF NOT EXISTS events_kind ON events(kind, id)")
    db.execute("CREATE INDEX IF NOT EXISTS events_ticket ON events(repo, issue, id)")
    have = {r[1] for r in db.execute("PRAGMA table_info(runs)")}
    for col in TOKEN_COLS:                          # added later: nullable, so older rows read as "not reported"
        if col not in have:
            db.execute(f"ALTER TABLE runs ADD COLUMN {col} INTEGER")
    db.execute(
        """CREATE TABLE IF NOT EXISTS status (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated REAL NOT NULL)"""
    )
    db.execute(                                     # a hint for the UI: tickets whose latest stage left questions for a person
        """CREATE TABLE IF NOT EXISTS open_questions (
            repo TEXT NOT NULL, issue INTEGER NOT NULL, stage TEXT NOT NULL, pending INTEGER NOT NULL, updated REAL NOT NULL,
            PRIMARY KEY (repo, issue))"""
    )
    db.execute(                                     # the design files each run published, for the UI to link
        """CREATE TABLE IF NOT EXISTS design_files (
            run_id INTEGER NOT NULL, repo TEXT NOT NULL, path TEXT NOT NULL, url TEXT NOT NULL, pr TEXT NOT NULL DEFAULT '',
            created REAL NOT NULL, PRIMARY KEY (run_id, repo, path))"""
    )
    db.execute(                                     # the rendered mockup PNGs, so the admin UI can show them without a GitHub token
        """CREATE TABLE IF NOT EXISTS mockup_images (
            repo TEXT NOT NULL, path TEXT NOT NULL, png BLOB NOT NULL, created REAL NOT NULL, PRIMARY KEY (repo, path))"""
    )
    db.execute(                                     # screenshots of what a run built (and diffs against the baselines), for the UI
        """CREATE TABLE IF NOT EXISTS run_images (
            run_id INTEGER NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL, png BLOB NOT NULL, created REAL NOT NULL,
            PRIMARY KEY (run_id, kind, name))"""
    )
    db.execute(                                     # the project manager's latest validated assessment of each ticket
        """CREATE TABLE IF NOT EXISTS pm_assessments (
            repo TEXT NOT NULL, issue INTEGER NOT NULL, priority TEXT NOT NULL,
            applied_label TEXT NOT NULL DEFAULT '',     -- the priority label the PM itself last put on the ticket ('' = none)
            overridden INTEGER NOT NULL DEFAULT 0,      -- 1 = a person's priority label differs, so the PM left it alone
            blocked_by TEXT NOT NULL DEFAULT '',        -- comma-separated issue numbers in the same repository
            reason TEXT NOT NULL DEFAULT '', issue_updated TEXT NOT NULL DEFAULT '', run_id INTEGER, assessed REAL NOT NULL,
            PRIMARY KEY (repo, issue))"""
    )
    ensure_step_tables(db)
    jobs.ensure_tables(db)
    schedules.ensure_tables(db)
    return db


def local(path: str) -> sqlite3.Connection:
    """This thread's own connection to path: a sqlite connection must not be shared between threads."""
    conns = getattr(_local, "conns", None)
    if conns is None:
        conns = _local.conns = {}
    if path not in conns:
        conns[path] = connect(path)
    return conns[path]


def seen(db, repo: str, issue: int, updated_at: str) -> bool:
    return db.execute(
        "SELECT 1 FROM decisions WHERE repo=? AND issue=? AND updated_at=?",
        (repo, issue, updated_at),
    ).fetchone() is not None


def record(db, repo: str, issue: int, updated_at: str, outcome: str, detail: str) -> None:
    db.execute(
        "INSERT OR REPLACE INTO decisions VALUES (?,?,?,?,?,?)",
        (repo, issue, updated_at, outcome, detail, time.time()),
    )
    db.commit()


def approvals(db) -> list[tuple[str, int, str]]:
    return db.execute("SELECT repo, issue, action FROM approvals ORDER BY created").fetchall()


def drop_approval(db, repo: str, issue: int, action: str) -> None:
    """Delete an approval once it is handled (one waiting for a free slot stays). A newer, different answer is kept."""
    db.execute("DELETE FROM approvals WHERE repo=? AND issue=? AND action=?", (repo, issue, action))
    db.commit()


def set_questions(db, repo: str, issue: int, stage: str, pending: int) -> None:
    if pending:
        db.execute("INSERT OR REPLACE INTO open_questions VALUES (?,?,?,?,?)", (repo, issue, stage, pending, time.time()))
    else:
        db.execute("DELETE FROM open_questions WHERE repo=? AND issue=?", (repo, issue))
    db.commit()


def questions_waiting(db, repo: str) -> set[int]:
    """Only a hint: the UI re-reads the questions and answers from GitHub before showing or recording anything."""
    try:
        return {n for (n,) in db.execute("SELECT issue FROM open_questions WHERE repo=?", (repo,))}
    except Exception:                               # an older database the orchestrator has not upgraded yet
        return set()


def pm_assessment(db, repo: str, issue: int) -> dict | None:
    cur = db.execute("SELECT * FROM pm_assessments WHERE repo=? AND issue=?", (repo, issue))
    row = cur.fetchone()
    return dict(zip([c[0] for c in cur.description], row)) if row else None


def pm_blocked_by(db, repo: str, issue: int) -> list[int]:
    row = db.execute("SELECT blocked_by FROM pm_assessments WHERE repo=? AND issue=?", (repo, issue)).fetchone()
    return [int(x) for x in row[0].split(",") if x.isdigit()] if row else []


def set_pm_assessment(db, repo: str, issue: int, priority: str, applied_label: str, overridden: bool, blocked_by: list,
                      reason: str, issue_updated: str, run_id: int | None) -> None:
    db.execute("INSERT OR REPLACE INTO pm_assessments VALUES (?,?,?,?,?,?,?,?,?,?)",
               (repo, issue, priority, applied_label, int(bool(overridden)), ",".join(str(int(n)) for n in blocked_by),
                reason, issue_updated, run_id, time.time()))
    db.commit()


def watch_pr(db, repo: str, number: int, issue_repo: str, issue_num: int) -> None:
    now = time.time()
    db.execute("INSERT OR REPLACE INTO prs VALUES (?,?,?,?,?,?,?,?,?)",
               (repo, number, issue_repo, issue_num, "watching", 0, now, now, ""))
    db.commit()


def watching(db) -> list[tuple]:
    return db.execute("SELECT repo, number, issue_repo, issue_num, rounds, watch_started, summary FROM prs "
                      "WHERE status='watching' ORDER BY watch_started").fetchall()


def failed_prs(db) -> list[tuple]:
    """PRs whose CI gave up (no fix rounds left, or a fix round changed nothing). Still re-checked, so a person's fix or merge clears them."""
    return db.execute("SELECT repo, number, issue_repo, issue_num FROM prs WHERE status='failed' ORDER BY updated").fetchall()


def update_pr(db, repo: str, number: int, **fields) -> None:
    allowed = {"status", "rounds", "watch_started", "summary"}
    assert set(fields) <= allowed
    sets = ", ".join(f"{k}=?" for k in fields) + ", updated=?"
    db.execute(f"UPDATE prs SET {sets} WHERE repo=? AND number=?", (*fields.values(), time.time(), repo, number))
    db.commit()


# state: unknown | clean | conflicting | needs-person | closed. notified_sha: the head last reported as conflicting.
def track_pr(db, repo: str, number: int, issue_repo: str, issue_num: int) -> None:
    db.execute("INSERT OR IGNORE INTO pr_conflicts (repo, number, issue_repo, issue_num, updated) VALUES (?,?,?,?,?)",
               (repo, number, issue_repo, issue_num, time.time()))
    db.commit()


def tracked_prs(db) -> list[dict]:
    return _dicts(db.execute("SELECT repo, number, issue_repo, issue_num, state, notified_sha, attempts FROM pr_conflicts "
                             "WHERE state != 'closed' ORDER BY updated"))


def conflicts_for_issue(db, issue_repo: str, issue_num: int) -> list[dict]:
    return _dicts(db.execute("SELECT repo, number, state, notified_sha, attempts FROM pr_conflicts "
                             "WHERE issue_repo=? AND issue_num=? AND state != 'closed' ORDER BY repo, number", (issue_repo, issue_num)))


def update_conflict(db, repo: str, number: int, **fields) -> None:
    allowed = {"state", "notified_sha", "attempts", "detail"}
    assert set(fields) <= allowed
    sets = ", ".join(f"{k}=?" for k in fields) + ", updated=?"
    db.execute(f"UPDATE pr_conflicts SET {sets} WHERE repo=? AND number=?", (*fields.values(), time.time(), repo, number))
    db.commit()


# ---- observability: runs, events, heartbeat (read by the UI) ----
MAX_OUTPUT, MAX_LOG, KEEP_EVENTS = 60000, 8000, 20000


def _dicts(cur) -> list[dict]:
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def start_run(db, kind: str, repo: str, issue: int, title: str, harness: str | None, model: str | None,
              effort: str | None, classification: str = "", stage: str | None = None) -> int:
    cur = db.execute(
        "INSERT INTO runs (kind, stage, repo, issue, title, harness, model, effort, classification, started) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)", (kind, stage, repo, issue, title[:300], harness, model, effort, classification, time.time()))
    db.commit()
    return cur.lastrowid


def finish_run(db, run_id: int, status: str, detail: str = "", pr_urls: str = "", output: str = "", log_tail: str = "",
               usage: dict | None = None) -> None:
    """usage: token counts by TOKEN_COLS name (runner.parse_usage validates them); anything missing is stored as NULL."""
    u = usage or {}
    toks = [u.get(c) if type(u.get(c)) is int else None for c in TOKEN_COLS]
    db.execute("UPDATE runs SET finished=?, status=?, detail=?, pr_urls=?, output=?, log_tail=?, "
               + ", ".join(f"{c}=?" for c in TOKEN_COLS) + " WHERE id=?",
               (time.time(), status, detail[:1000], pr_urls, output[:MAX_OUTPUT], log_tail[-MAX_LOG:], *toks, run_id))
    db.commit()


def add_design_files(db, run_id: int, files: list) -> None:
    """Record a run's published design files; entries that fail designfiles.link_ok are dropped."""
    for f in files:
        if designfiles.link_ok(f):
            png = f.get("png")
            if isinstance(png, bytes) and f["path"].endswith(".png") and png_ok(png):
                db.execute("INSERT OR REPLACE INTO mockup_images (repo, path, png, created) VALUES (?,?,?,?)", (f["repo"], f["path"], png, time.time()))
            db.execute("INSERT OR IGNORE INTO design_files (run_id, repo, path, url, pr, created) VALUES (?,?,?,?,?,?)",
                       (run_id, f["repo"], f["path"], f["url"], f.get("pr") or "", time.time()))
    db.commit()


def design_files_for_runs(db, run_ids: list) -> dict[int, list[dict]]:
    """Design files by run id. Empty when the table does not exist yet (the read-only UI may start before the orchestrator)."""
    out: dict[int, list[dict]] = {}
    ids = [int(i) for i in run_ids]
    if not ids:
        return out
    try:
        rows = _dicts(db.execute("SELECT run_id, repo, path, url, pr FROM design_files WHERE run_id IN (%s) ORDER BY rowid"
                                 % ",".join("?" * len(ids)), ids))
    except sqlite3.OperationalError:
        return out
    for r in rows:
        out.setdefault(r["run_id"], []).append(r)
    return out


IMAGE_KINDS = ("built", "diff", "verify")      # verify: screenshots a verification worker returned
IMAGE_NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,80}")


def add_run_images(db, run_id: int, images: list) -> None:
    """Keep a run's screenshots: [{kind: built|diff|verify, name: <page>-<viewport>, png}], only well-formed ones."""
    for i in images or []:
        if i.get("kind") in IMAGE_KINDS and IMAGE_NAME.fullmatch(str(i.get("name", ""))) and isinstance(i.get("png"), bytes) and png_ok(i["png"]):
            db.execute("INSERT OR REPLACE INTO run_images (run_id, kind, name, png, created) VALUES (?,?,?,?,?)",
                       (run_id, i["kind"], i["name"], i["png"], time.time()))
    db.commit()


def run_images(db, run_id: int) -> list[dict]:
    """(kind, name) of the images a run kept, built first."""
    try:
        return _dicts(db.execute("SELECT kind, name FROM run_images WHERE run_id=? ORDER BY kind, name", (int(run_id),)))
    except sqlite3.OperationalError:
        return []


def run_image(db, run_id: int, kind: str, name: str) -> bytes | None:
    try:
        row = db.execute("SELECT png FROM run_images WHERE run_id=? AND kind=? AND name=?", (int(run_id), kind, name)).fetchone()
    except sqlite3.OperationalError:
        return None
    return bytes(row[0]) if row else None


def mockup_image(db, repo: str, path: str) -> bytes | None:
    """The stored PNG for a recorded preview (repo, path), or None."""
    try:
        row = db.execute("SELECT png FROM mockup_images WHERE repo=? AND path=?", (repo, path)).fetchone()
    except sqlite3.OperationalError:
        return None
    return bytes(row[0]) if row else None


def mockup_previews(db, repo: str, issue: int) -> list[dict]:
    """The rendered mockup PNGs of a ticket's latest design run that published any (repo, path, url, pr)."""
    try:
        run = db.execute("SELECT r.id FROM runs r WHERE r.repo=? AND r.issue=? AND r.stage='designer' AND EXISTS "
                         "(SELECT 1 FROM design_files f WHERE f.run_id=r.id AND f.path LIKE '%/previews/%.png') "
                         "ORDER BY r.id DESC LIMIT 1", (repo, int(issue))).fetchone()
        if not run:
            return []
        return _dicts(db.execute("SELECT repo, path, url, pr FROM design_files WHERE run_id=? AND path LIKE '%/previews/%.png' ORDER BY rowid",
                                 (run[0],)))
    except sqlite3.OperationalError:
        return []


def mark_interrupted(db) -> int:
    """Runs still marked running when a new orchestrator starts were killed with the old one."""
    cur = db.execute("UPDATE runs SET status='interrupted', finished=?, detail='orchestrator restarted' WHERE status='running'", (time.time(),))
    db.commit()
    return cur.rowcount


def add_event(db, kind: str, message: str, repo: str | None = None, issue: int | None = None, run_id: int | None = None) -> None:
    cur = db.execute("INSERT INTO events (ts, kind, repo, issue, run_id, message) VALUES (?,?,?,?,?,?)",
                     (time.time(), kind, repo, issue, run_id, message[:2000]))
    if cur.lastrowid % 500 == 0:                      # keep the timeline bounded
        db.execute("DELETE FROM events WHERE id < ?", (cur.lastrowid - KEEP_EVENTS,))
    db.commit()


def set_status(db, key: str, value: str) -> None:
    db.execute("INSERT INTO status (key, value, updated) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
               (key, value, time.time()))
    db.commit()


def get_status(db) -> dict:
    return {k: {"value": v, "updated": u} for k, v, u in db.execute("SELECT key, value, updated FROM status")}


def recent_runs(db, limit: int = 50, offset: int = 0, status: str | None = None, repo: str | None = None) -> list[dict]:
    where, args = [], []
    if status == "passed":
        where.append("status IN ('pr','stage')")
    elif status:
        where.append("status=?"); args.append(status)
    if repo:
        where.append("repo=?"); args.append(repo)
    tail = " FROM runs" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ? OFFSET ?"
    cols = "id, kind, stage, repo, issue, title, harness, model, effort, started, finished, status, detail, pr_urls"
    try:
        return _dicts(db.execute("SELECT " + cols + ", " + ", ".join(TOKEN_COLS) + tail, (*args, limit, offset)))
    except sqlite3.OperationalError:                # a database the orchestrator has not upgraded yet: no token counts
        return _dicts(db.execute("SELECT " + cols + tail, (*args, limit, offset)))


def runs_summary(db, since: float, repo: str | None = None) -> dict:
    """Counts for the Runs summary line: started since `since`, how many passed or failed, and the average length of the finished ones."""
    where, args = "started >= ?", [since]
    if repo:
        where += " AND repo=?"; args.append(repo)
    row = db.execute(f"SELECT COUNT(*), COALESCE(SUM(status IN ('pr','stage')),0), COALESCE(SUM(status='failed'),0), "
                     f"AVG(CASE WHEN finished IS NOT NULL THEN finished-started END) FROM runs WHERE {where}", args).fetchone()
    out = {"total": row[0], "passed": row[1], "failed": row[2], "avg": row[3], "tokens": {"in": None, "out": None}}
    try:
        t = db.execute("SELECT " + ", ".join(f"SUM({c})" for c in TOKEN_COLS) + f" FROM runs WHERE {where}", args).fetchone()
        out["tokens"] = run_tokens(dict(zip(TOKEN_COLS, t)))
    except sqlite3.OperationalError:
        pass
    return out


def get_run(db, run_id: int) -> dict | None:
    rows = _dicts(db.execute("SELECT * FROM runs WHERE id=?", (run_id,)))
    return rows[0] if rows else None


ROUTINE_KINDS = ("run:start", "rate-limit", "answers", "startup", "restart")


def recent_events(db, limit: int = 100, kind_prefix: str | None = None, before_id: int | None = None) -> list[dict]:
    """kind_prefix: a kind prefix, or "important" (hide routine kinds and ignored/dry-run decisions) or "alerts" (alert* and error*)."""
    where, args = [], []
    if kind_prefix == "important":
        where.append(f"kind NOT IN ({','.join('?' * len(ROUTINE_KINDS))})"); args.extend(ROUTINE_KINDS)
        where.append("NOT (kind LIKE 'decision%' AND (message LIKE 'ignored%' OR message LIKE 'dry-run%'))")
    elif kind_prefix == "alerts":
        where.append("(kind LIKE 'alert%' OR kind LIKE 'error%')")
    elif kind_prefix:
        where.append("kind LIKE ?"); args.append(kind_prefix + "%")
    if before_id:
        where.append("id < ?"); args.append(before_id)
    sql = "SELECT id, ts, kind, repo, issue, run_id, message FROM events" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
    return _dicts(db.execute(sql, (*args, limit)))


def latest_decisions(db, limit: int = 100) -> list[dict]:
    return _dicts(db.execute("SELECT repo, issue, outcome, detail, decided_at FROM decisions ORDER BY decided_at DESC LIMIT ?", (limit,)))


def watched_prs(db, limit: int = 100) -> list[dict]:
    return _dicts(db.execute(
        "SELECT p.repo, p.number, p.issue_repo, p.issue_num, p.status, p.rounds, p.watch_started, p.updated, p.summary, "
        "(SELECT r.title FROM runs r WHERE r.repo=p.issue_repo AND r.issue=p.issue_num ORDER BY r.id DESC LIMIT 1) AS title "
        "FROM prs p ORDER BY p.updated DESC LIMIT ?", (limit,)))


def tickets(db, limit: int = 100) -> list[dict]:
    """One row per ticket the factory has looked at: its latest decision and how many runs it has had."""
    return _dicts(db.execute(
        """SELECT d.repo, d.issue, d.outcome, d.detail, d.decided_at,
                  (SELECT COUNT(*) FROM runs r WHERE r.repo=d.repo AND r.issue=d.issue) AS runs,
                  (SELECT r.status FROM runs r WHERE r.repo=d.repo AND r.issue=d.issue ORDER BY r.id DESC LIMIT 1) AS last_run,
                  (SELECT r.title FROM runs r WHERE r.repo=d.repo AND r.issue=d.issue ORDER BY r.id DESC LIMIT 1) AS title
           FROM decisions d
           WHERE d.decided_at = (SELECT MAX(d2.decided_at) FROM decisions d2 WHERE d2.repo=d.repo AND d2.issue=d.issue)
           GROUP BY d.repo, d.issue ORDER BY d.decided_at DESC LIMIT ?""", (limit,)))


def prs_for_issue(db, issue_repo: str, issue_num: int) -> list[tuple[str, int]]:
    """Open factory PRs raised for a ticket (the ones a review should cover)."""
    return [(r, n) for r, n in db.execute(
        "SELECT repo, number FROM prs WHERE issue_repo=? AND issue_num=? AND status != 'closed' ORDER BY number", (issue_repo, issue_num))]


# ---- pipeline steps: one per (ticket, step), derived from runs; optionally mirrored as GitHub sub-issues ----
STEP_ORDER = ("analyze", "design", "architect", "implement", "review", "ci-fix")
_STAGE_STEP = {"analyst": "analyze", "designer": "design", "architect": "architect", "reviewer": "review"}
_DONE = {"stage", "pr"}
_QUEUED = {"rate-limited", "interrupted"}                  # the run was requeued, nothing is running


def step_of(kind: str, stage: str | None) -> str | None:
    if kind == "build":
        return "implement"
    if kind == "fix":
        return "ci-fix"
    return _STAGE_STEP.get(stage or "")


def step_status(run_status: str) -> str:
    return "running" if run_status == "running" else "done" if run_status in _DONE else "queued" if run_status in _QUEUED else "failed"


def ensure_step_tables(db) -> None:
    db.execute(
        """CREATE TABLE IF NOT EXISTS step_issues (
            repo TEXT NOT NULL, issue INTEGER NOT NULL, step TEXT NOT NULL,
            sub_number INTEGER, sub_id INTEGER,
            last_state TEXT NOT NULL DEFAULT 'open',
            hands_off INTEGER NOT NULL DEFAULT 0,
            sync_error TEXT NOT NULL DEFAULT '', updated REAL NOT NULL,
            PRIMARY KEY (repo, issue, step))"""
    )
    db.execute("CREATE INDEX IF NOT EXISTS runs_ticket ON runs(repo, issue, id)")


def get_step_issue(db, repo: str, issue: int, step: str) -> dict | None:
    rows = _dicts(db.execute("SELECT * FROM step_issues WHERE repo=? AND issue=? AND step=?", (repo, issue, step)))
    return rows[0] if rows else None


def upsert_step_issue(db, repo: str, issue: int, step: str, **fields) -> None:
    allowed = {"sub_number", "sub_id", "last_state", "hands_off", "sync_error"}
    assert set(fields) <= allowed
    db.execute("INSERT OR IGNORE INTO step_issues (repo, issue, step, updated) VALUES (?,?,?,?)", (repo, issue, step, time.time()))
    if fields:
        sets = ", ".join(f"{k}=?" for k in fields) + ", updated=?"
        db.execute(f"UPDATE step_issues SET {sets} WHERE repo=? AND issue=? AND step=?", (*fields.values(), time.time(), repo, issue, step))
    db.commit()


def steps_for_ticket(db, repo: str, issue: int) -> list[dict]:
    """The ticket's pipeline, computed from its runs (so tickets worked before step tracking have it too).
    Retries, review runs and CI-fix rounds are grouped under their step; the latest run decides the status."""
    try:
        runs = _dicts(db.execute(
            "SELECT id, kind, stage, harness, model, effort, started, finished, status, pr_urls FROM runs r "
            "WHERE (r.repo=? AND r.issue=?) OR (r.kind='fix' AND EXISTS (SELECT 1 FROM prs p WHERE p.repo=r.repo AND p.issue_num=r.issue "
            "AND p.issue_repo=? AND p.issue_num=?)) ORDER BY r.id", (repo, issue, repo, issue)))
    except sqlite3.OperationalError:
        return []
    by: dict[str, list[dict]] = {}
    for r in runs:
        step = step_of(r["kind"], r["stage"])
        if step:
            by.setdefault(step, []).append(r)
    try:
        subs = {s["step"]: s for s in _dicts(db.execute("SELECT * FROM step_issues WHERE repo=? AND issue=?", (repo, issue)))}
    except sqlite3.OperationalError:
        subs = {}
    out = []
    for step in STEP_ORDER:
        rs = by.get(step)
        if not rs:
            continue
        last = rs[-1]
        urls = " ".join(dict.fromkeys(u for r in rs for u in (r["pr_urls"] or "").split()))
        sub = subs.get(step)
        out.append({"step": step, "status": step_status(last["status"]), "attempts": len(rs), "run_id": last["id"],
                    "run_ids": [r["id"] for r in rs], "role": last["stage"] or last["kind"], "harness": last["harness"],
                    "model": last["model"], "effort": last["effort"], "started": rs[0]["started"], "finished": last["finished"],
                    "pr_urls": urls, "sub_number": sub["sub_number"] if sub else None})
    return out


def step_counts(db, rows: list[dict]) -> dict[tuple[str, int], tuple[int, int]]:
    """(done, total) steps for each ticket row, for the Tickets list."""
    return {(t["repo"], t["issue"]): (sum(s["status"] == "done" for s in st), len(st))
            for t in rows for st in [steps_for_ticket(db, t["repo"], t["issue"])]}


DOC_STAGES = ("analyst", "designer", "architect")


def stage_doc(db, repo: str, issue: int, stage: str) -> dict | None:
    """The latest finished run of a stage that kept a document: id, started, output and whether it was cut at MAX_OUTPUT."""
    if stage not in DOC_STAGES:
        return None
    try:
        rows = _dicts(db.execute("SELECT id, started, output FROM runs WHERE repo=? AND issue=? AND stage=? AND output<>'' "
                                 "ORDER BY id DESC LIMIT 1", (repo, issue, stage)))
    except sqlite3.OperationalError:
        return None
    if not rows:
        return None
    r = rows[0]
    return {"id": r["id"], "started": r["started"], "output": r["output"], "truncated": len(r["output"]) >= MAX_OUTPUT}


def doc_stages(db, repo: str, issue: int) -> list[str]:
    """The stages that have a stored document for the ticket, in pipeline order."""
    try:
        have = {r[0] for r in db.execute("SELECT DISTINCT stage FROM runs WHERE repo=? AND issue=? AND output<>''", (repo, issue))}
    except sqlite3.OperationalError:
        return []
    return [s for s in DOC_STAGES if s in have]


# ---- ticket journey: the route a ticket took, in order (runs, routing decisions, CI rounds, a stop for a person) ----
# Floor station ids (ui/floor.py STATIONS). A CI fix run is drawn at Build, so a loop reads build, CI, build, CI.
_RUN_STATION = {"build": "build", "fix": "build", "review": "review", "conflicts": "conflicts"}
_CI_STATE = {"ci:passed": "done", "ci:failed": "failed", "ci:timed-out": "failed"}
_TICKET_RUNS = ("WHERE (r.repo=? AND r.issue=?) OR (r.kind='fix' AND EXISTS (SELECT 1 FROM prs p WHERE p.repo=r.repo "
                "AND p.issue_num=r.issue AND p.issue_repo=? AND p.issue_num=?)) ORDER BY r.id")


def run_station(kind: str, stage: str | None) -> str | None:
    if kind == "stage":
        return stage if stage in DOC_STAGES else "review" if stage == "reviewer" else None
    return _RUN_STATION.get(kind)


def run_tokens(r: dict) -> dict:
    """Tokens in (uncached input plus cache reads and writes) and out; None where the harness did not report them."""
    parts = [r.get(c) for c in ("tokens_in", "tokens_cache_read", "tokens_cache_write")]
    return {"in": sum(p for p in parts if p is not None) if any(p is not None for p in parts) else None,
            "out": r.get("tokens_out")}


def journey(db, repo: str, issue: int, now: float | None = None) -> dict:
    """The ticket's steps in the order they happened, numbered from 1, with totals. Each step: n, station, kind (run,
    decision, ci or person), state (done, running, failed, queued or waiting), started, finished, seconds and, for runs,
    run_id, stage, status, harness, model, effort and tokens {in, out}. Text fields are raw: the UI escapes them.
    Read-only; works on a database the orchestrator has not upgraded yet (no token counts)."""
    now = time.time() if now is None else now
    cols = "r.id, r.kind, r.stage, r.harness, r.model, r.effort, r.started, r.finished, r.status"
    args = (repo, issue, repo, issue)
    try:
        runs = _dicts(db.execute(f"SELECT {cols}, " + ", ".join(f"r.{c}" for c in TOKEN_COLS) + f" FROM runs r {_TICKET_RUNS}", args))
    except sqlite3.OperationalError:
        try:
            runs = _dicts(db.execute(f"SELECT {cols} FROM runs r {_TICKET_RUNS}", args))
        except sqlite3.OperationalError:
            runs = []
    try:
        events = _dicts(db.execute("SELECT id, ts, kind, message FROM events WHERE repo=? AND issue=? "
                                   "AND (kind='decision' OR kind LIKE 'ci:%') ORDER BY id", (repo, issue)))
    except sqlite3.OperationalError:
        events = []
    try:
        asked = db.execute("SELECT stage, pending, updated FROM open_questions WHERE repo=? AND issue=?", (repo, issue)).fetchone()
    except sqlite3.OperationalError:
        asked = None
    # An event and the run it led to can share a clock tick: the event (a decision, a CI result) comes first.
    merged = sorted([(e["ts"], 0, e["id"], e) for e in events] + [(r["started"], 1, r["id"], r) for r in runs], key=lambda x: x[:3])
    steps: list[dict] = []
    for _, is_run, _, x in merged:
        if is_run:
            state = step_status(x["status"])
            end = x["finished"]
            steps.append({"kind": "run", "station": run_station(x["kind"], x["stage"]), "state": state, "run_id": x["id"],
                          "run_kind": x["kind"], "stage": x["stage"], "status": x["status"], "harness": x["harness"],
                          "model": x["model"], "effort": x["effort"], "started": x["started"], "finished": end,
                          "seconds": (end if end is not None else now) - x["started"] if state == "running" or end is not None else None,
                          "tokens": run_tokens(x), "message": ""})
        elif x["kind"] == "decision":
            person = x["message"].startswith(("needs a person", "human:")) and not x["message"].endswith("[dry-run]")
            steps.append({"kind": "person" if person else "decision",
                          "station": "needs" if person else "trust" if x["message"].startswith("ignored") else "classify",
                          "state": "done", "started": x["ts"], "finished": x["ts"], "seconds": None, "message": x["message"]})
        elif x["kind"] in _CI_STATE:
            steps.append({"kind": "ci", "station": "ci", "state": _CI_STATE[x["kind"]], "started": x["ts"], "finished": x["ts"],
                          "seconds": None, "message": x["message"]})
    # Stopped for a person: the route ends at the step that asked (a stage's open questions, or the router's call).
    if asked and not any(s["kind"] == "run" and s["started"] > asked[2] for s in steps):
        steps.append({"kind": "person", "station": "needs", "state": "waiting", "stage": asked[0], "pending": asked[1],
                      "started": asked[2], "finished": None, "seconds": None, "message": f"{asked[1]} open question(s) from the {asked[0]}"})
    elif steps and steps[-1]["kind"] == "person":
        steps[-1].update(state="waiting", finished=None)
    for i, s in enumerate(steps, 1):
        s["n"] = i
    run_steps = [s for s in steps if s["kind"] == "run"]
    if not steps:
        status = "none"
    elif any(s["state"] == "running" for s in run_steps):
        status = "running"
    else:
        status = {"waiting": "waiting", "failed": "failed", "queued": "queued"}.get(steps[-1]["state"], "done")
    started = steps[0]["started"] if steps else None
    finished = max((s["finished"] for s in steps if s["finished"] is not None), default=None) if status in ("done", "failed") else None
    tok_in = [s["tokens"]["in"] for s in run_steps if s["tokens"]["in"] is not None]
    tok_out = [s["tokens"]["out"] for s in run_steps if s["tokens"]["out"] is not None]
    models: dict[str, int] = {}
    for s in run_steps:
        if s["model"]:
            models[s["model"]] = models.get(s["model"], 0) + 1
    return {"steps": steps, "status": status, "started": started, "finished": finished,
            "seconds": ((finished if finished is not None else now) - started) if started is not None else None,
            "tokens": {"in": sum(tok_in) if tok_in else None, "out": sum(tok_out) if tok_out else None},
            "models": models, "waiting": steps[-1] if status == "waiting" else None}
