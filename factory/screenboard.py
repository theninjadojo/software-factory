"""The Screens board: what each configured screen looks like now, as built on the default branch and as designed.

On a timer ([screens] board_every) or when a person asks on the board, the orchestrator clones each repo with screens (depth 1, the
default branch), renders every page at every viewport with the same sealed shoot as a build (screens.capture), and renders each
page's design canvas ([[screens.pages]] design) with the same sealed renderer as the designer's previews, after the same checks.
Nothing here runs page code outside those containers, and every PNG that comes back is checked again before it is stored.

Shots are kept per commit, so a note a person left on one keeps its picture: older shots are dropped only when nothing open points
at them. An image is named by a key, screen:<repo>:<page>:<view>:<sha>, where view is a viewport name or "design"."""
import json
import logging
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

from . import designfiles, jobs, screens
from .config import Config, ScreenPage
from .render import png_ok
from .render import render as render_canvas

log = logging.getLogger("factory.screenboard")
DESIGN = "design"
KEY = re.compile(r"screen:([\w.-]+/[\w.-]+):([a-z0-9][a-z0-9-]{0,60}):([a-z0-9][a-z0-9-]{0,60}):([0-9a-f]{40})")
STATUS = "screens_board"                 # status row: {"at": when the last refresh finished, "repos": {repo: {"sha", "problem"}}}
_running = threading.Lock()


def ensure_tables(db) -> None:
    db.execute(
        """CREATE TABLE IF NOT EXISTS screen_shots (
            repo TEXT NOT NULL, page TEXT NOT NULL, view TEXT NOT NULL, sha TEXT NOT NULL, png BLOB NOT NULL, created REAL NOT NULL,
            PRIMARY KEY (repo, page, view, sha))""")
    db.execute(                                          # which test of a Playwright run took a shot (jobs.validate_result checked it)
        """CREATE TABLE IF NOT EXISTS screen_shot_tests (
            repo TEXT NOT NULL, page TEXT NOT NULL, view TEXT NOT NULL, sha TEXT NOT NULL, test TEXT NOT NULL,
            PRIMARY KEY (repo, page, view, sha, test))""")
    db.execute("CREATE INDEX IF NOT EXISTS screen_shot_tests_t ON screen_shot_tests(repo, test)")
    db.execute("CREATE TABLE IF NOT EXISTS screen_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, created REAL NOT NULL)")   # "Refresh now"


def key(repo: str, page: str, view: str, sha: str) -> str:
    return f"screen:{repo}:{page}:{view}:{sha}"


def src(k: str) -> str:
    return "/screenimg?" + urlencode({"key": k})


def store(db, repo: str, page: str, view: str, sha: str, png: bytes, now: float | None = None) -> bool:
    if not (png_ok(png) and KEY.fullmatch(key(repo, page, view, sha))):
        return False
    db.execute("INSERT OR REPLACE INTO screen_shots (repo, page, view, sha, png, created) VALUES (?,?,?,?,?,?)",
               (repo, page, view, sha, png, now or time.time()))
    return True


def image(db, k: str) -> bytes | None:
    """The stored PNG a key names, re-checked; None when it is gone, unusable or the key is not one."""
    m = KEY.fullmatch(k or "")
    if not m:
        return None
    try:
        row = db.execute("SELECT png FROM screen_shots WHERE repo=? AND page=? AND view=? AND sha=?", m.groups()).fetchone()
    except sqlite3.OperationalError:
        return None
    return bytes(row[0]) if row and png_ok(bytes(row[0])) else None


def latest(db) -> dict[tuple[str, str, str], dict]:
    """(repo, page, view) -> {key, sha, created} of the newest shot ({} before the table exists)."""
    try:
        rows = db.execute("SELECT repo, page, view, sha, MAX(created) FROM screen_shots GROUP BY repo, page, view").fetchall()
    except sqlite3.OperationalError:
        return {}
    return {(r, p, v): {"key": key(r, p, v, s), "sha": s, "created": c} for r, p, v, s, c in rows}


CAPTURE_VIEWS = ("desktop", "tablet", "phone", "mobile")      # shown first, in this order, when a Playwright run's screens are grouped by page
RUN_VIEW = "run"                                              # the view of a screenshot whose name does not end in a known viewport


def view_order(view: str) -> tuple[int, str]:
    return (CAPTURE_VIEWS.index(view) if view in CAPTURE_VIEWS else len(CAPTURE_VIEWS), view)


def capture_sha(db, repo: str) -> str:
    """The commit of the newest Playwright run of `repo` that has screens on the board ('' if none)."""
    try:
        for sha, params in db.execute("SELECT base_sha, params FROM verify_jobs WHERE repo=? AND params != '' AND status IN ('passed','failed') "
                                      "ORDER BY id DESC LIMIT 50", (repo,)).fetchall():
            if jobs.is_screens({"params": params}) and db.execute("SELECT 1 FROM screen_shots WHERE repo=? AND sha=? LIMIT 1", (repo, sha)).fetchone():
                return sha
    except sqlite3.OperationalError:
        pass
    return ""


def capture_shots(db, sc) -> dict[tuple[str, str], dict[str, dict]]:
    """(repo, page) -> {view: {key, sha, created}} for the screens of each Playwright repo's newest run (pages that are not configured
    as board pages; those keep their own cards)."""
    have = latest(db)
    configured = {(p.repo, p.name) for p in sc.pages}
    out: dict[tuple[str, str], dict[str, dict]] = {}
    for c in sc.captures:
        sha = capture_sha(db, c.repo)
        for (r, p, v), x in have.items():
            if r == c.repo and x["sha"] == sha and (r, p) not in configured:
                out.setdefault((r, p), {})[v] = x
    return out


def prune(db, keep: set[str]) -> int:
    """Drop every shot that is not the newest of its (repo, page, view) and that no key in `keep` names."""
    newest = {x["key"] for x in latest(db).values()}
    gone = 0
    for r, p, v, s in db.execute("SELECT repo, page, view, sha FROM screen_shots").fetchall():
        if (k := key(r, p, v, s)) not in newest and k not in keep:
            gone += db.execute("DELETE FROM screen_shots WHERE repo=? AND page=? AND view=? AND sha=?", (r, p, v, s)).rowcount
            db.execute("DELETE FROM screen_shot_tests WHERE repo=? AND page=? AND view=? AND sha=?", (r, p, v, s))
    return gone


def link_tests(db, repo: str, sha: str, links: dict[tuple[str, str], set[str]]) -> None:
    """Record which tests took each (page, view) shot of a run at `sha`: this run's links replace any earlier ones of those shots."""
    for (page, view), tests in links.items():
        db.execute("DELETE FROM screen_shot_tests WHERE repo=? AND page=? AND view=? AND sha=?", (repo, page, view, sha))
        db.executemany("INSERT OR IGNORE INTO screen_shot_tests (repo, page, view, sha, test) VALUES (?,?,?,?,?)",
                       [(repo, page, view, sha, t) for t in sorted(tests)])


def shots_for_test(db, repo: str, test: str) -> list[str]:
    """Keys of the shots the test `test` took in `repo`'s newest Playwright run, in page and view order ([] when none)."""
    sha = capture_sha(db, repo)
    if not test or not sha:
        return []
    try:
        rows = db.execute("SELECT page, view FROM screen_shot_tests WHERE repo=? AND sha=? AND test=?", (repo, sha, test)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [key(repo, p, v, sha) for p, v in sorted(rows, key=lambda r: (r[0], view_order(r[1])))]


def tests_for_shot(db, k: str) -> list[str]:
    """Ids of the tests that took the shot `k` ([] when none is known)."""
    m = KEY.fullmatch(k or "")
    if not m:
        return []
    try:
        return [r[0] for r in db.execute("SELECT test FROM screen_shot_tests WHERE repo=? AND page=? AND view=? AND sha=? ORDER BY test",
                                         m.groups()).fetchall()]
    except sqlite3.OperationalError:
        return []


def status(db) -> dict:
    try:
        row = db.execute("SELECT value FROM status WHERE key=?", (STATUS,)).fetchone()
        return json.loads(row[0]) if row else {}
    except (sqlite3.OperationalError, ValueError):
        return {}


def requested(db) -> bool:
    try:
        return db.execute("SELECT 1 FROM screen_requests LIMIT 1").fetchone() is not None
    except sqlite3.OperationalError:
        return False


def request(db) -> None:
    ensure_tables(db)
    db.execute("INSERT INTO screen_requests (created) VALUES (?)", (time.time(),))
    db.commit()


def due(cfg: Config, db, now: float) -> bool:
    if not cfg.screens.pages:
        return False
    if requested(db):
        return True
    from .schedules import parse_every
    every = parse_every(cfg.screens.board_every) if cfg.screens.board_every else None
    return bool(every) and now - float(status(db).get("at") or 0) >= every


def _design_text(src_dir: Path, rel: str) -> str | None:
    """The canvas at `rel` in the checkout, if it is a regular file inside it that passes the designer's checks."""
    p = src_dir / rel
    try:
        real = p.resolve()
        if p.is_symlink() or not p.is_file() or not real.is_relative_to(src_dir.resolve()) or p.stat().st_size > designfiles.MAX_BYTES:
            return None
        text = p.read_text(errors="strict")
        designfiles.validate_html(text)
        return text
    except (OSError, ValueError, designfiles.DesignFileRejected):
        return None


def shoot_repo(cfg: Config, repo: str, src_dir: Path, sha: str, db, run=subprocess.run) -> str:
    """Render one repo's pages and canvases from the checkout at `src_dir` and store them under `sha`. Returns what went wrong, or ''."""
    sc, problems = cfg.screens, []
    pages = [p for p in sc.pages if p.repo == repo]
    by_id = {s["id"]: s for s in screens.shots_for(sc, repo)}
    view_of = {}                                                   # shot id -> (page, viewport)
    for p in pages:
        for v in (p.viewports or tuple(x.name for x in sc.viewports)):
            view_of[f"{p.name}-{v}"] = (p.name, v)
    got, problem = screens.capture(cfg.runner, sc, repo, src_dir, run)
    if problem.startswith("rendering "):                         # the container's own output: logged, not shown
        log.warning("screens board: %s: %s", repo, problem)
        problem = "the screenshots could not be taken (the orchestrator log has the renderer's output)"
    if problem:
        problems.append(problem)
    for sid, png in got.items():
        if sid in by_id and sid in view_of:
            store(db, repo, *view_of[sid], sha, png)
    docs = {p.name: t for p in pages if p.design and (t := _design_text(src_dir, p.design)) is not None}
    missing = [p.design for p in pages if p.design and p.name not in docs]
    if missing:
        problems.append("design canvas missing or refused: " + ", ".join(missing))
    if docs:
        try:
            rendered = render_canvas(cfg.runner, docs, run)
        except ValueError:
            rendered = {}
        for name, png in rendered.items():
            store(db, repo, name, DESIGN, sha, png)
        if len(rendered) < len(docs):
            problems.append("design canvas did not render: " + ", ".join(sorted(set(docs) - set(rendered))))
    db.commit()
    return "; ".join(problems)


def refresh(cfg: Config, token: str | None, db, keep: set[str], run=subprocess.run, clone=None) -> dict:
    """Shoot every repo with screens on its default branch. Returns the status written for the board."""
    from .runner import git, git_env
    env = git_env(token or "")
    repos = sorted({p.repo for p in cfg.screens.pages})
    db.execute("DELETE FROM screen_requests")                     # taken now: a request made while this runs asks for another pass
    db.commit()
    out = {"at": 0, "repos": {}}
    Path(cfg.runner.work_dir).mkdir(parents=True, exist_ok=True)
    for repo in repos:
        work = Path(tempfile.mkdtemp(prefix="board-", dir=cfg.runner.work_dir))
        try:
            dest = work / "repo"
            if clone:
                clone(repo, dest)
            else:
                git(["clone", "--quiet", "--depth", "1", f"https://github.com/{repo}.git", str(dest)], None, env, 300)
            sha = git(["rev-parse", "HEAD"], dest, env).stdout.strip()
            if not re.fullmatch(r"[0-9a-f]{40}", sha):
                raise ValueError("no commit")
            out["repos"][repo] = {"sha": sha, "problem": shoot_repo(cfg, repo, dest, sha, db, run)}
        except (subprocess.SubprocessError, OSError, ValueError) as e:
            log.warning("screens board: %s failed: %s", repo, type(e).__name__)
            out["repos"][repo] = {"sha": "", "problem": "could not check out the default branch"}
        finally:
            shutil.rmtree(work, ignore_errors=True)
    prune(db, keep)
    out["at"] = time.time()
    db.execute("INSERT INTO status (key, value, updated) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
               "updated=excluded.updated", (STATUS, json.dumps(out), out["at"]))
    db.commit()
    return out


def kept_keys(db) -> set[str]:
    """Screen images an open review note is on: they outlive newer shots."""
    try:
        return {r[0] for r in db.execute("SELECT image FROM review_notes WHERE sent IS NULL AND image LIKE 'screen:%'")}
    except sqlite3.OperationalError:
        return set()


def tick(cfg: Config, conn, now: float, token: str | None, connect, emit=None, threaded: bool = True) -> bool:
    """Start a refresh when one is due and none is running. It runs on its own thread with its own connection (`connect()`), so a
    slow render never holds up the poll. Returns True when one was started."""
    if not due(cfg, conn, now) or not _running.acquire(blocking=False):
        return False

    def job():
        db = connect()
        try:
            st = refresh(cfg, token, db, kept_keys(db))
            bad = [r for r, x in st["repos"].items() if x["problem"]]
            if emit:
                emit("screens", "screens board refreshed" + (f"; problems in {', '.join(bad)}" if bad else ""))
        except Exception:
            log.exception("screens board refresh failed")
        finally:
            db.close()
            _running.release()
    if threaded:
        threading.Thread(target=job, name="screens-board", daemon=True).start()
    else:
        job()
    return True


def running() -> bool:
    return _running.locked()


def journeys(pages: tuple[ScreenPage, ...]) -> list[tuple[str, list[ScreenPage]]]:
    """[(journey, pages in step order)], named journeys first by name, then the pages with none under ''."""
    groups: dict[str, list[ScreenPage]] = {}
    for p in pages:
        groups.setdefault(p.journey, []).append(p)
    order = sorted(j for j in groups if j) + ([""] if "" in groups else [])
    return [(j, sorted(groups[j], key=lambda p: (p.step, p.name, p.repo))) for j in order]


def board_images(db, sc, noted: list[str] = ()) -> list[dict]:
    """Every screen image a person can mark up on the board: per page in journey order, its canvas then each viewport's newest shot,
    then any older shot of it that an open note is on. [{key, label, src, screen: (repo, page)}]."""
    have = latest(db)
    older: dict[tuple[str, str], list[str]] = {}
    for k in dict.fromkeys(noted):
        m = KEY.fullmatch(k)
        if m and k not in {x["key"] for x in have.values()} and image(db, k) is not None:
            older.setdefault((m.group(1), m.group(2)), []).append(k)
    names = [v.name for v in sc.viewports]
    out = []
    for (repo, page), views_ in sorted(capture_shots(db, sc).items()):
        for v in sorted(views_, key=view_order):
            out.append({"key": views_[v]["key"], "label": f"{page} · {v}", "src": src(views_[v]["key"]), "screen": (repo, page)})
        for k in older.get((repo, page), []):
            v, sha = KEY.fullmatch(k).group(3, 4)
            out.append({"key": k, "label": f"{page} · {v} · {sha[:7]}", "src": src(k), "screen": (repo, page)})
    for _, pages in journeys(sc.pages):
        for p in pages:
            for v in [DESIGN] + list(p.viewports or names):
                if (x := have.get((p.repo, p.name, v))):
                    out.append({"key": x["key"], "label": f"{p.name} · {'designed' if v == DESIGN else v}", "src": src(x["key"]),
                                "screen": (p.repo, p.name)})
            for k in older.get((p.repo, p.name), []):
                v, sha = KEY.fullmatch(k).group(3, 4)
                out.append({"key": k, "label": f"{p.name} · {'designed' if v == DESIGN else v} · {sha[:7]}", "src": src(k),
                            "screen": (p.repo, p.name)})
    return out
