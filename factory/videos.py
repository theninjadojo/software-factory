"""Review videos: a short walkthrough of a pull request with a spoken voice-over and a transcript, made for each build PR of a ticket
whose "Make a review video" switch is on. The switch lives only in the admin UI (a row here, never a label).

The UI writes rows (the switch, and one queued video per PR); the orchestrator makes them, one at a time on its own thread.
Nothing a ticket or a repository says decides what runs:
  - the narration is fixed wording built from the PR's file list (names cleaned to a small set of characters, counts as numbers);
    no model reads the diff, so text in it cannot steer anything. It is sanitised before it is stored or shown.
  - the pictures are the screenshots the build already took of its configured screens (each re-checked with png_ok), then each
    changed file's first lines of diff, escaped into a fixed template and turned into PNGs by the sealed render container.
  - speech (Piper) and encoding (ffmpeg) run in the sealed video container named in config ([videos] image): no network, read-only
    root, no capabilities, only /in (read-only) and /out mounted.
What comes back is untrusted: the MP4 is accepted only when mp4_ok walks its boxes and finds a real MP4 of one H.264 track and at most
one AAC track, within the configured length and size; it is stored under a name the factory picks, in a private folder of the
state directory (not in backups). A video that fails, times out or hits the daily limit is skipped with a fixed reason on the ticket;
it never touches the PR's checks or blocks anything."""
import dataclasses
import html
import json
import logging
import re
import secrets
import shutil
import sqlite3
import subprocess
import tempfile
import threading
from pathlib import Path

from . import render
from .sanitize import sanitize_markdown

log = logging.getLogger("factory.videos")
ACTIVE = ("queued", "making")
STATUSES = ("queued", "making", "ready", "skipped", "stale", "removed")
MAX_SCENES, MAX_SHOTS, HUNK_LINES, LINE_CHARS = 8, 3, 40, 110
CHECK_SECONDS = 600                     # a ready video's PR is re-read for new commits at most this often
CHECKS_PER_TICK = 5
MAX_MOOV = 8 * 1024 * 1024
BRANDS = (b"isom", b"mp42", b"avc1", b"iso2", b"mp41")
FILE = re.compile(r"(\d{1,12})\.mp4")
SPOKEN = re.compile(r"[^A-Za-z0-9 .,:;'()/_+-]")
_running = threading.Lock()


def reasons(vc) -> dict[str, str]:
    """Every reason a video is skipped, fixed wording with only config numbers in it: nothing from a ticket, a repo or a tool."""
    return {"daily_cap": f"Daily limit of {vc.max_per_day} videos reached. Try again tomorrow.",
            "tool_failed": "The video tool failed. The pull request is not affected.",
            "timeout": f"Took longer than {max(1, vc.timeout_seconds // 60)} minutes to make.",
            "too_long": f"The video was longer than {_minutes(vc.max_seconds)}.",
            "too_large": f"The video was larger than {vc.max_mb} MB.",
            "closed": "The pull request is no longer open.",
            "github": "The pull request could not be read from GitHub."}


def _minutes(s: int) -> str:
    return f"{s // 60} minutes" if s % 60 == 0 and s >= 120 else f"{s // 60} minute" if s == 60 else f"{s} seconds"


def ensure_tables(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS review_video_tickets (
        repo TEXT NOT NULL, issue INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
        updated REAL NOT NULL, updated_by TEXT NOT NULL DEFAULT '', PRIMARY KEY (repo, issue))""")
    db.execute("""CREATE TABLE IF NOT EXISTS review_videos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        issue_repo TEXT NOT NULL, issue_num INTEGER NOT NULL, pr_repo TEXT NOT NULL, pr_number INTEGER NOT NULL,
        pr_title TEXT NOT NULL DEFAULT '', head_sha TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'queued',      -- queued | making | ready | skipped | stale | removed
        reason TEXT NOT NULL DEFAULT '',            -- a key of reasons(), when skipped
        mode TEXT NOT NULL DEFAULT '',              -- ui (screenshots, then the diff) | diff
        created REAL NOT NULL, started REAL, finished REAL, checked REAL,
        seconds REAL NOT NULL DEFAULT 0, bytes INTEGER NOT NULL DEFAULT 0,
        transcript TEXT NOT NULL DEFAULT '')         -- JSON [[start seconds, text], ...], sanitised""")
    db.execute("CREATE INDEX IF NOT EXISTS review_videos_pr ON review_videos(pr_repo, pr_number)")
    db.execute("CREATE INDEX IF NOT EXISTS review_videos_issue ON review_videos(issue_repo, issue_num)")


def _dicts(cur) -> list[dict]:
    names = [c[0] for c in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def enabled(db, repo: str, issue: int) -> bool:
    try:
        row = db.execute("SELECT enabled FROM review_video_tickets WHERE repo=? AND issue=?", (repo, int(issue))).fetchone()
    except sqlite3.OperationalError:
        return False
    return bool(row and row[0])


def build_prs(db, repo: str, issue: int) -> list[tuple[str, int]]:
    """The ticket's open build PRs the factory recorded (design PRs left out)."""
    from . import db as dbm
    design = dbm.design_prs(db, repo, issue)
    return [(r["repo"], int(r["number"])) for r in dbm.conflicts_for_issue(db, repo, issue) if (r["repo"], int(r["number"])) not in design]


def queue(db, issue_repo: str, issue_num: int, pr_repo: str, pr_number: int, now: float, again: bool = False) -> int | None:
    """Queue one video of a PR. Without `again`, only when the PR never had one (turning the switch off and on queues nothing new);
    with it, when none is queued or being made. The new row's id, else None."""
    ensure_tables(db)
    have = {r[0] for r in db.execute("SELECT status FROM review_videos WHERE pr_repo=? AND pr_number=?", (pr_repo, int(pr_number)))}
    if have & set(ACTIVE) or (have and not again):
        return None
    cur = db.execute("INSERT INTO review_videos (issue_repo, issue_num, pr_repo, pr_number, created) VALUES (?,?,?,?,?)",
                     (issue_repo, int(issue_num), pr_repo, int(pr_number), now))
    db.commit()
    return cur.lastrowid


def set_enabled(db, repo: str, issue: int, on: bool, who: str, now: float) -> int:
    """Save the switch. Turned on, every open build PR of the ticket without a video gets one queued. Returns how many were."""
    ensure_tables(db)
    db.execute("INSERT INTO review_video_tickets (repo, issue, enabled, updated, updated_by) VALUES (?,?,?,?,?) "
               "ON CONFLICT(repo, issue) DO UPDATE SET enabled=excluded.enabled, updated=excluded.updated, updated_by=excluded.updated_by",
               (repo, int(issue), int(bool(on)), now, who))
    db.commit()
    if not on:
        return 0
    try:
        prs = build_prs(db, repo, issue)
    except sqlite3.OperationalError:
        prs = []
    return sum(1 for r, n in prs if queue(db, repo, issue, r, n, now) is not None)


def pr_opened(db, issue_repo: str, issue_num: int, pr_repo: str, pr_number: int, now: float) -> int | None:
    """A build PR was opened: queue its video when the ticket's switch is on."""
    return queue(db, issue_repo, issue_num, pr_repo, pr_number, now) if enabled(db, issue_repo, issue_num) else None


def get(db, vid) -> dict | None:
    try:
        rows = _dicts(db.execute("SELECT * FROM review_videos WHERE id=?", (int(vid),)))
    except (sqlite3.OperationalError, ValueError, TypeError):
        return None
    return rows[0] if rows else None


def for_ticket(db, repo: str, issue: int) -> list[dict]:
    """The newest video of each PR of the ticket."""
    try:
        return _dicts(db.execute("SELECT * FROM review_videos WHERE id IN (SELECT MAX(id) FROM review_videos WHERE issue_repo=? AND issue_num=? "
                                 "GROUP BY pr_repo, pr_number) ORDER BY pr_repo, pr_number", (repo, int(issue))))
    except sqlite3.OperationalError:
        return []


def newest_for_pr(db, pr_repo: str, pr_number: int) -> dict | None:
    rows = _dicts(db.execute("SELECT * FROM review_videos WHERE pr_repo=? AND pr_number=? ORDER BY id DESC LIMIT 1", (pr_repo, int(pr_number))))
    return rows[0] if rows else None


def used_today(db, now: float) -> int:
    """Videos started in the last 24 hours, whatever became of them."""
    try:
        return db.execute("SELECT COUNT(*) FROM review_videos WHERE started IS NOT NULL AND started > ?", (now - 86400,)).fetchone()[0]
    except sqlite3.OperationalError:
        return 0


def transcript(row: dict) -> list[tuple[float, str]]:
    try:
        lines = json.loads(row.get("transcript") or "[]")
        return [(float(t), str(s)) for t, s in lines]
    except (ValueError, TypeError):
        return []


def clock(seconds: float) -> str:
    s = int(seconds)
    return f"{s // 60}:{s % 60:02d}"


def transcript_text(row: dict) -> str:
    return "".join(f"{clock(t)} {s}\n" for t, s in transcript(row))


def video_dir(cfg) -> Path:
    return Path(cfg.db_path).parent / "videos"


def video_path(cfg, row: dict) -> Path:
    return video_dir(cfg) / f"{int(row['id'])}.mp4"


def byte_range(header: str | None, size: int):
    """A Range header against a file of `size` bytes: None for the whole file, (first, last) inclusive for one satisfiable range,
    False for anything else (several ranges, other units, out of bounds): the caller answers 416."""
    if not header:
        return None
    m = re.fullmatch(r"bytes=(\d{0,15})-(\d{0,15})", header.strip())
    if not m or size <= 0 or not (m[1] or m[2]):
        return False
    if not m[1]:
        n = int(m[2])
        return (max(0, size - n), size - 1) if n else False
    first, last = int(m[1]), int(m[2]) if m[2] else size - 1
    if first >= size or last < first:
        return False
    return first, min(last, size - 1)


# ---- the script ----

def speakable(text, n: int) -> str:
    """Text safe to read aloud and to show: a small set of characters, single spaces, at most n characters."""
    return " ".join(SPOKEN.sub(" ", str(text or "")).split())[:n].strip()


WHAT = {"added": "new file", "removed": "deleted", "renamed": "renamed", "copied": "copied"}


def script(pr_number: int, title: str, files: list[dict], shots: list[str]) -> list[dict]:
    """The scenes of a video, in order: what the PR is, the screens as built (when the build took any), each changed file, and how many
    were left out. Each scene: {text (spoken and the transcript), kind, file or shot}. Fixed wording; only cleaned names and numbers."""
    files = [f for f in files if isinstance(f, dict) and speakable(f.get("filename"), 160)]
    plus = sum(int(f.get("additions") or 0) for f in files)
    minus = sum(int(f.get("deletions") or 0) for f in files)
    head = f"Review of pull request {int(pr_number)}" + (f": {t}." if (t := speakable(title, 80)) else ".")
    scenes = [{"kind": "intro", "text": f"{head} It changes {len(files)} file{'' if len(files) == 1 else 's'}, "
                                        f"with {plus} line{'' if plus == 1 else 's'} added and {minus} removed."}]
    for name in shots[:MAX_SHOTS]:
        scenes.append({"kind": "ui", "shot": name, "text": f"The {speakable(name.replace('-', ' '), 60)} screen, as built."})
    room = MAX_SCENES - len(scenes) - (1 if len(files) > MAX_SCENES - len(scenes) else 0)
    for f in files[:room]:
        a, d = int(f.get("additions") or 0), int(f.get("deletions") or 0)
        scenes.append({"kind": "file", "file": f, "text": f"{speakable(f['filename'], 160)}: {WHAT.get(f.get('status'), 'changed')}, "
                                                         f"{a} line{'' if a == 1 else 's'} added and {d} removed."})
    if len(files) > room:
        rest = len(files) - room
        scenes.append({"kind": "more", "text": f"{rest} more file{'' if rest == 1 else 's'} are in the pull request but not in this video."})
    for s in scenes:
        s["text"] = sanitize_markdown(s["text"], 400)
    return scenes


SLIDE_CSS = ("html,body{margin:0;background:#12161a;color:#e8edf0;font:22px/1.4 'DejaVu Sans',sans-serif}"
             ".s{width:1280px;height:720px;box-sizing:border-box;padding:44px 56px;overflow:hidden;border:1px solid #2d363d}"
             "h1{font-size:32px;margin:0 0 10px;word-break:break-all}p{margin:0 0 18px;color:#a9b5bd}"
             "pre{margin:0;font:17px/1.35 'DejaVu Sans Mono',monospace;white-space:pre;overflow:hidden}"
             ".a{color:#52c7a1}.d{color:#ff6b5e}.h{color:#8fa8ff}")


def slide_html(scene: dict) -> str:
    """A fixed template; every piece of text from the PR is escaped. The render container has no network and runs no script."""
    e = html.escape
    if scene["kind"] == "file":
        f = scene["file"]
        lines = []
        for line in str(f.get("patch") or "").splitlines()[:HUNK_LINES]:
            cls = "h" if line.startswith("@@") else "a" if line.startswith("+") else "d" if line.startswith("-") else ""
            text = e(line[:LINE_CHARS])
            lines.append(f'<span class="{cls}">{text}</span>' if cls else text)
        body = f"<pre>{chr(10).join(lines)}</pre>" if lines else "<p>No text diff to show (a binary or very large file).</p>"
        top = f"<h1>{e(str(f.get('filename'))[:200])}</h1><p>{e(scene['text'])}</p>"
    else:
        top, body = f"<h1>{e(scene['text'])}</h1>", ""
    return f'<!doctype html><html><head><meta charset="utf-8"><style>{SLIDE_CSS}</style></head><body><div class="s">{top}{body}</div></body></html>'


def built_shots(db, repo: str, issue: int) -> dict[str, bytes]:
    """The screenshots of the ticket's newest build run that took any ({name: PNG}, each re-checked), at most MAX_SHOTS."""
    try:
        rows = db.execute("SELECT i.name, i.png FROM run_images i WHERE i.kind='built' AND i.run_id=(SELECT MAX(r.id) FROM runs r "
                          "JOIN run_images j ON j.run_id=r.id AND j.kind='built' WHERE r.repo=? AND r.issue=?) ORDER BY i.name LIMIT ?",
                          (repo, int(issue), MAX_SHOTS)).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {n: bytes(p) for n, p in rows if render.png_ok(bytes(p))}


# ---- checking what comes back ----

def _head(head: bytes, left: int) -> tuple[bytes, int, int]:
    """(type, header length, box length) of a box whose first bytes are `head`, with `left` bytes to its container's end."""
    if len(head) < 8 or left < 8:
        raise ValueError("short box")
    n, kind, h = int.from_bytes(head[:4], "big"), head[4:8], 8
    if n == 1:
        if len(head) < 16 or left < 16:
            raise ValueError("short box")
        n, h = int.from_bytes(head[8:16], "big"), 16
    elif n == 0:
        n = left
    if n < h or n > left:
        raise ValueError("bad box size")
    return kind, h, n


def _box(data: bytes, i: int, end: int) -> tuple[bytes, int, int]:
    """(type, payload start, box end) of the box at i; ValueError when it does not fit."""
    kind, h, n = _head(data[i:i + 16], end - i)
    return kind, i + h, i + n


def _children(data: bytes, a: int, b: int):
    i = a
    while i < b:
        kind, p, end = _box(data, i, b)
        yield kind, p, end
        i = end


def _find(data: bytes, a: int, b: int, path: tuple) -> tuple[int, int] | None:
    for kind, p, end in _children(data, a, b):
        if kind == path[0]:
            return (p, end) if len(path) == 1 else _find(data, p, end, path[1:])
    return None


def _moov(moov: bytes, max_seconds: float) -> float | None:
    seconds, codecs = None, []
    for kind, p, end in _children(moov, 0, len(moov)):
        if kind == b"mvhd":
            if end - p < 32:
                return None
            if moov[p] == 0:
                scale, dur = int.from_bytes(moov[p + 12:p + 16], "big"), int.from_bytes(moov[p + 16:p + 20], "big")
            else:
                scale, dur = int.from_bytes(moov[p + 20:p + 24], "big"), int.from_bytes(moov[p + 24:p + 32], "big")
            if not scale:
                return None
            seconds = dur / scale
        elif kind == b"trak":
            stsd = _find(moov, p, end, (b"mdia", b"minf", b"stbl", b"stsd"))
            if stsd is None or stsd[1] - stsd[0] < 16:
                return None
            codecs.append(moov[stsd[0] + 12:stsd[0] + 16])          # version/flags, entry count, then the first entry's size and type
    if seconds is None or not 0 < seconds <= max_seconds + 2:
        return None
    if codecs.count(b"avc1") != 1 or codecs.count(b"mp4a") > 1 or len(codecs) != codecs.count(b"avc1") + codecs.count(b"mp4a"):
        return None
    return seconds


def mp4_ok(path: Path, max_seconds: float, max_bytes: int) -> float | None:
    """The video's length in seconds when `path` is a regular file holding a real MP4 (ftyp first with a known brand, moov before
    mdat for streaming), exactly one H.264 track and at most one AAC track, no longer than max_seconds and no larger than max_bytes.
    None otherwise. Only the box headers, ftyp and moov are read."""
    try:
        if path.is_symlink() or not path.is_file():
            return None
        size = path.stat().st_size
        if not 32 <= size <= max_bytes:
            return None
        top = []
        with open(path, "rb") as f:
            i = 0
            while i < size:
                if len(top) >= 64:
                    return None
                f.seek(i)
                kind, h, n = _head(f.read(16), size - i)
                top.append((kind, i + h, i + n))
                i += n
            kinds = [k for k, _, _ in top]
            if kinds[0] != b"ftyp" or b"moov" not in kinds or b"mdat" not in kinds or kinds.index(b"moov") > kinds.index(b"mdat"):
                return None
            _, a, _ = top[0]
            f.seek(a)
            if f.read(4) not in BRANDS:
                return None
            _, a, b = top[kinds.index(b"moov")]
            if b - a > MAX_MOOV:
                return None
            f.seek(a)
            moov = f.read(b - a)
        return _moov(moov, max_seconds)
    except (OSError, ValueError):
        return None


def timings(path: Path, n: int, max_seconds: float) -> list[float] | None:
    """timings.json from the container: {"scenes": [seconds of each scene]}, exactly n positive numbers that fit in the video."""
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 10_000:
            return None
        got = json.loads(path.read_text())["scenes"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if (not isinstance(got, list) or len(got) != n
            or any(isinstance(x, bool) or not isinstance(x, (int, float)) or not 0 < x <= max_seconds for x in got) or sum(got) > max_seconds + 2):
        return None
    return [float(x) for x in got]


# ---- making one ----

def compose_cmd(rn, vc, name: str, work: Path) -> list[str]:
    user = ["--userns=keep-id:uid=1000,gid=1000"] if rn.engine == "podman" else ["--user", "1000:1000"]
    return [rn.engine, "run", "--rm", "--name", name, "--network=none", *user, "--read-only", "--cap-drop=all",
            "--security-opt=no-new-privileges", "--pids-limit=256", "--memory=2g", "--cpus=2",
            "--tmpfs", "/tmp:rw,size=1g,mode=1777", "-v", f"{work / 'in'}:/in:ro", "-v", f"{work / 'out'}:/out:rw", vc.image]


class Skip(Exception):
    """A video that will not be made; args[0] is a key of reasons()."""


def _compose(cfg, scenes: list[dict], slides: dict[str, bytes], run) -> tuple[Path, Path, Path]:
    """Run the video container on the scenes and their slides. (work dir, review.mp4, timings.json); the caller removes the work dir."""
    rn, vc = cfg.runner, cfg.videos
    Path(rn.work_dir).mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="video-", dir=rn.work_dir))
    (work / "in" / "slides").mkdir(parents=True)
    (work / "out").mkdir()
    for name, png in slides.items():
        (work / "in" / "slides" / f"{name}.png").write_bytes(png)
    spec = {"max_seconds": vc.max_seconds, "scenes": [{"slide": f"{s['slide']}.png", "text": s["text"]} for s in scenes]}
    (work / "in" / "script.json").write_text(json.dumps(spec))
    run(["chmod", "-R", "a+rX", str(work / "in")], check=True)
    run(["chmod", "a+rwX", str(work / "out")], check=True)
    name = f"factory-video-{secrets.token_hex(4)}"            # factory-*: the deploy guard and recovery see it like a run
    try:
        res = run(compose_cmd(rn, vc, name, work), capture_output=True, text=True, timeout=vc.timeout_seconds, check=False)
    except subprocess.TimeoutExpired:
        run([rn.engine, "rm", "-f", name], capture_output=True, check=False)
        shutil.rmtree(work, ignore_errors=True)
        raise Skip("timeout")
    except OSError:
        shutil.rmtree(work, ignore_errors=True)
        log.warning("videos: could not start the video container (is %s installed?)", rn.engine)
        raise Skip("tool_failed")
    if getattr(res, "returncode", 0) != 0:
        log.warning("videos: the video container exited %s: %s", res.returncode, (getattr(res, "stderr", "") or "")[-300:])
        shutil.rmtree(work, ignore_errors=True)
        raise Skip("tool_failed")
    return work, work / "out" / "review.mp4", work / "out" / "timings.json"


def make(cfg, gh, db, row: dict, run=subprocess.run) -> dict:
    """Make one video (its row is already 'making'). Stores the file and returns the finished fields; raises Skip with a reason."""
    vc = cfg.videos
    try:
        pr = gh.get_pr(row["pr_repo"], row["pr_number"])
        if pr.get("state") != "open":
            raise Skip("closed")
        files = gh.pr_files(row["pr_repo"], row["pr_number"])
    except Skip:
        raise
    except Exception:
        log.exception("videos: could not read %s#%s", row["pr_repo"], row["pr_number"])
        raise Skip("github")
    shots = built_shots(db, row["issue_repo"], row["issue_num"])
    scenes = script(row["pr_number"], pr.get("title") or "", files, sorted(shots))
    slides, docs = {}, {}
    for i, s in enumerate(scenes, 1):
        s["slide"] = f"s{i:02d}"
        if s["kind"] == "ui":
            slides[s["slide"]] = shots[s["shot"]]
        else:
            docs[s["slide"]] = slide_html(s)
    slides.update(render.render(dataclasses.replace(cfg.runner, render_previews=True), docs, run=run))
    if any(s["slide"] not in slides for s in scenes):
        log.warning("videos: some slides of %s#%s did not render", row["pr_repo"], row["pr_number"])
        raise Skip("tool_failed")
    work, mp4, tim = _compose(cfg, scenes, slides, run)
    try:
        if mp4.is_file() and not mp4.is_symlink() and mp4.stat().st_size > vc.max_mb * 1024 * 1024:
            raise Skip("too_large")
        seconds = mp4_ok(mp4, vc.max_seconds, vc.max_mb * 1024 * 1024)
        if seconds is None:
            raise Skip("too_long" if mp4_ok(mp4, 10 ** 6, vc.max_mb * 1024 * 1024) else "tool_failed")
        times = timings(tim, len(scenes), vc.max_seconds)
        if times is None:
            raise Skip("tool_failed")
        start, lines = 0.0, []
        for s, t in zip(scenes, times):
            lines.append([round(start, 1), s["text"]])
            start += t
        out = video_dir(cfg)
        out.mkdir(mode=0o700, parents=True, exist_ok=True)
        dest = video_path(cfg, row)
        part = dest.with_name(dest.name + ".part")
        shutil.copyfile(mp4, part)
        part.chmod(0o600)
        part.replace(dest)
        return {"head_sha": str((pr.get("head") or {}).get("sha") or "")[:64], "pr_title": speakable(pr.get("title"), 120),
                "mode": "ui" if shots else "diff", "seconds": seconds, "bytes": dest.stat().st_size, "transcript": json.dumps(lines)}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def links(cfg, row: dict) -> str:
    base = cfg.videos.ui_url or cfg.telegram_ui_url
    if not base:
        return "A review video of this pull request is ready on the ticket's page in the factory's admin UI."
    return f"Review video: {base}/ticket/video?id={int(row['id'])} (transcript: {base}/ticket/video/transcript?id={int(row['id'])})"


def _say(cfg, gh, repo: str, number: int, body: str) -> None:
    if cfg.dry_run:
        return
    try:
        gh.comment(repo, number, body)
    except Exception:
        log.exception("videos: could not comment on %s#%s", repo, number)


def _skip(cfg, gh, db, row: dict, reason: str, now: float) -> None:
    db.execute("UPDATE review_videos SET status='skipped', reason=?, finished=? WHERE id=?", (reason, now, row["id"]))
    db.commit()
    text = reasons(cfg.videos).get(reason, reasons(cfg.videos)["tool_failed"])
    _say(cfg, gh, row["issue_repo"], row["issue_num"], f"No review video for {row['pr_repo']}#{int(row['pr_number'])}: {text}")
    _event(db, f"review video for {row['pr_repo']}#{int(row['pr_number'])} skipped: {reason}", row)


def _event(db, message: str, row: dict) -> None:
    from . import db as dbm
    try:
        dbm.add_event(db, "video", message, row["issue_repo"], row["issue_num"])
    except sqlite3.Error:
        pass


def work_one(cfg, gh, db, row: dict, now_fn, run=subprocess.run) -> None:
    try:
        done = make(cfg, gh, db, row, run)
    except Skip as e:
        return _skip(cfg, gh, db, row, e.args[0], now_fn())
    except Exception:
        log.exception("videos: making the video of %s#%s failed", row["pr_repo"], row["pr_number"])
        return _skip(cfg, gh, db, row, "tool_failed", now_fn())
    db.execute("UPDATE review_videos SET status='ready', reason='', finished=?, checked=?, head_sha=?, pr_title=?, mode=?, seconds=?, bytes=?, "
               "transcript=? WHERE id=?", (now_fn(), now_fn(), done["head_sha"], done["pr_title"], done["mode"], done["seconds"], done["bytes"],
                                           done["transcript"], row["id"]))
    db.commit()
    row = get(db, row["id"])
    _say(cfg, gh, row["pr_repo"], row["pr_number"], links(cfg, row))
    _say(cfg, gh, row["issue_repo"], row["issue_num"], f"{row['pr_repo']}#{int(row['pr_number'])}: " + links(cfg, row))
    _event(db, f"review video for {row['pr_repo']}#{int(row['pr_number'])} made ({clock(row['seconds'])}, {row['mode']})", row)


# ---- the poll ----

def mark_stale(gh, db, now: float) -> int:
    """Ready videos whose PR has new commits become 'stale' (a person may Make again). Each PR is read at most every CHECK_SECONDS."""
    n = 0
    rows = _dicts(db.execute("SELECT * FROM review_videos WHERE status='ready' AND (checked IS NULL OR checked < ?) ORDER BY checked LIMIT ?",
                             (now - CHECK_SECONDS, CHECKS_PER_TICK)))
    for r in rows:
        try:
            pr = gh.get_pr(r["pr_repo"], r["pr_number"])
        except Exception:
            log.warning("videos: could not read %s#%s", r["pr_repo"], r["pr_number"])
            continue
        sha = str((pr.get("head") or {}).get("sha") or "")
        stale = bool(sha) and bool(r["head_sha"]) and sha != r["head_sha"]
        db.execute("UPDATE review_videos SET checked=?, status=? WHERE id=?", (now, "stale" if stale else "ready", r["id"]))
        n += stale
    db.commit()
    return n


def expire(cfg, db, now: float) -> int:
    """Remove video files older than retention_days, and files no row keeps; a kept row whose file is gone (say after a restore,
    which never carries videos) is marked removed. Returns how many files were deleted."""
    folder = video_dir(cfg)
    gone = 0
    cut = now - cfg.videos.retention_days * 86400
    keep = {}
    for r in _dicts(db.execute("SELECT id, finished FROM review_videos WHERE status IN ('ready','stale')")):
        p = folder / f"{int(r['id'])}.mp4"
        if (r["finished"] or 0) < cut or not p.is_file():
            db.execute("UPDATE review_videos SET status='removed' WHERE id=?", (r["id"],))
        else:
            keep[p.name] = r
    db.commit()
    if folder.is_dir():
        for p in folder.iterdir():
            if FILE.fullmatch(p.name) and p.name not in keep and (p.is_file() or p.is_symlink()):
                p.unlink()
                gone += 1
    return gone


def tick(cfg, gh, db, now: float, connect, now_fn, threaded: bool = True, run=subprocess.run) -> bool:
    """Expire old videos, mark out-of-date ones, and start the oldest queued video when none is being made and the daily limit
    allows (else it is skipped with that reason). True when one was started."""
    ensure_tables(db)
    expire(cfg, db, now)
    if not cfg.videos.enabled:
        return False                                    # anything queued waits until videos are on again
    try:
        mark_stale(gh, db, now)
    except Exception:
        log.exception("videos: checking for new commits failed")
    if not _running.acquire(blocking=False):
        return False
    db.execute("UPDATE review_videos SET status='skipped', reason='tool_failed', finished=? WHERE status='making'", (now,))   # left by a restart
    db.commit()
    rows = _dicts(db.execute("SELECT * FROM review_videos WHERE status='queued' ORDER BY id LIMIT 1"))
    if not rows:
        _running.release()
        return False
    row = rows[0]
    if used_today(db, now) >= cfg.videos.max_per_day:
        _running.release()
        _skip(cfg, gh, db, row, "daily_cap", now)
        return False
    db.execute("UPDATE review_videos SET status='making', started=? WHERE id=?", (now, row["id"]))
    db.commit()

    def job():
        conn = connect()
        try:
            work_one(cfg, gh, conn, row, now_fn, run)
        except Exception:
            log.exception("videos: the video job failed")
        finally:
            conn.close()
            _running.release()
    if threaded:
        threading.Thread(target=job, name="review-video", daemon=True).start()
    else:
        job()
    return True
