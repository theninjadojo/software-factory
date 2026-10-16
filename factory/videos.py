"""Review videos: a short narrated walkthrough of a pull request, rendered with Remotion, made for each build PR of a ticket whose
"Make a review video" switch is on. The switch, and the voice, live only in the admin UI (rows here, never a label).

The UI writes rows (the switch, and one queued video per PR); the orchestrator makes them, one at a time on its own thread.
Nothing a ticket or a repository says decides what runs:
  - the narration comes from the automated code review, which already reads the diff: the reviewer ends its document with a
    `factory-video` block (scenes of plain text). That block is untrusted model output, so it is only data: each scene is checked
    against fixed kinds and limits, a code scene may only name a file the PR changes (the lines shown are taken from the PR's own
    diff here, never from the block), a screen scene only a screenshot the build took, and every text is cleaned and cut to length.
    Without a usable block (no review, or review videos turned on after it) the narration is fixed wording built from the file list.
  - the pictures are the build's screenshots (each re-checked with png_ok) and the PR's diff lines; the Remotion composition shows
    them with fixed templates as React text, so nothing in them is ever HTML or code.
  - Piper speaks inside the video container; ElevenLabs (a ticket's choice, or the config default) is called from voices.py, and its
    WAVs are passed in. The container (config [videos] image) has no network, a read-only root, no capabilities, and only /in
    (read-only) and /out mounted.
What comes back is untrusted: the MP4 is accepted only when mp4_ok walks its boxes and finds a real MP4 of one H.264 track and at most
one AAC track, within the configured length and size; it is stored under a name the factory picks, in a private folder of the
state directory (not in backups). A video that fails, times out or hits the daily limit is skipped with a fixed reason on the ticket;
it never touches the PR's checks or blocks anything."""
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

from . import render, voices
from .sanitize import sanitize_markdown

log = logging.getLogger("factory.videos")
ACTIVE = ("queued", "making")
STATUSES = ("queued", "making", "ready", "skipped", "stale", "removed")
MAX_SCENES, MAX_SHOTS, HUNK_LINES, LINE_CHARS, MAX_POINTS = 12, 3, 40, 110, 6
CHECK_SECONDS = 600                     # a ready video's PR is re-read for new commits at most this often
CHECKS_PER_TICK = 5
REVIEW_WAIT = 3600                      # a new PR's video waits this long for the automated review's narration
MAX_MOOV = 8 * 1024 * 1024
BRANDS = (b"isom", b"mp42", b"avc1", b"iso2", b"mp41")
FILE = re.compile(r"(\d{1,12})\.mp4")
SPOKEN = re.compile(r"[^A-Za-z0-9 .,:;'()/_+-]")
PR_REF = re.compile(r"([A-Za-z0-9][A-Za-z0-9_.-]{0,99}/[A-Za-z0-9_.-]{1,100})#(\d{1,9})")
SCREEN = re.compile(r"[a-z0-9][a-z0-9-]{0,60}")
BLOCK = re.compile(r"^```factory-video[ \t]*\n(.*?)^```[ \t]*$", re.M | re.S)
KINDS = ("title", "shot", "code", "points")
_running = threading.Lock()

PROMPT = (
    "\n\nREVIEW VIDEO BLOCK (required). A short narrated video of each pull request is made for the people reviewing it. After your "
    "review, add exactly one fenced code block whose info string is `factory-video`, holding JSON like "
    '{"videos": [{"pr": "owner/repo#12", "scenes": [{"kind": "title", "title": "Short headline", "say": "One or two sentences."}, '
    '{"kind": "code", "file": "path/changed/in/the/pr.py", "hunk": 0, "say": "What this change does and why it matters."}, '
    '{"kind": "shot", "screen": "screen-name", "say": "What the person sees."}, '
    '{"kind": "points", "title": "What to check", "points": ["Short point"], "say": "Closing sentence."}]}]}. '
    "One entry per pull request you reviewed. 3 to 10 scenes each, in the order a reviewer should look at things: start with a title "
    "scene saying what the change is for, then the most important files (`file` must be a path the pull request changes; `hunk` is "
    "the 0-based index of the diff hunk to show), screens only by the names of the built screenshots you were given, and end with "
    "what a reviewer should check. `say` is what the narrator speaks: plain spoken English, at most 300 characters, no Markdown, "
    "no code, no URLs. The block is machine-read: valid JSON, plain text.")


def prompt() -> str:
    return PROMPT


def reasons(vc) -> dict[str, str]:
    """Every reason a video is skipped, fixed wording with only config numbers in it: nothing from a ticket, a repo or a tool."""
    return {"daily_cap": f"Daily limit of {vc.max_per_day} videos reached. Try again tomorrow.",
            "tool_failed": "The video tool failed. The pull request is not affected.",
            "voice": "The voice could not be made (check the ElevenLabs key, voice and quota on the Credentials page).",
            "timeout": f"Took longer than {max(1, vc.timeout_seconds // 60)} minutes to make.",
            "too_long": f"The video was longer than {_minutes(vc.max_seconds)}.",
            "too_large": f"The video was larger than {vc.max_mb} MB.",
            "closed": "The pull request is no longer open.",
            "github": "The pull request could not be read from GitHub."}


def _minutes(s: int) -> str:
    return f"{s // 60} minutes" if s % 60 == 0 and s >= 120 else f"{s // 60} minute" if s == 60 else f"{s} seconds"


def _column(db, table: str, name: str, decl: str) -> None:
    if name not in {r[1] for r in db.execute(f"PRAGMA table_info({table})")}:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def ensure_tables(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS review_video_tickets (
        repo TEXT NOT NULL, issue INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 0,
        updated REAL NOT NULL, updated_by TEXT NOT NULL DEFAULT '', PRIMARY KEY (repo, issue))""")
    _column(db, "review_video_tickets", "voice", "TEXT NOT NULL DEFAULT ''")          # '' = the config's voice
    _column(db, "review_video_tickets", "voice_id", "TEXT NOT NULL DEFAULT ''")       # an ElevenLabs voice id; '' = the config's
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
    _column(db, "review_videos", "narration", "TEXT NOT NULL DEFAULT ''")             # review (the reviewer's scenes) | fixed
    _column(db, "review_videos", "voice", "TEXT NOT NULL DEFAULT ''")                 # piper | elevenlabs, as made
    _column(db, "review_videos", "wait_review", "INTEGER NOT NULL DEFAULT 0")         # queued as the PR opened: its review is coming
    db.execute("CREATE INDEX IF NOT EXISTS review_videos_pr ON review_videos(pr_repo, pr_number)")
    db.execute("CREATE INDEX IF NOT EXISTS review_videos_issue ON review_videos(issue_repo, issue_num)")
    db.execute("""CREATE TABLE IF NOT EXISTS review_video_notes (
        pr_repo TEXT NOT NULL, pr_number INTEGER NOT NULL, created REAL NOT NULL,
        scenes TEXT NOT NULL,                       -- JSON, checked by parse_block (files and screens are checked again when made)
        PRIMARY KEY (pr_repo, pr_number))""")


def _dicts(cur) -> list[dict]:
    names = [c[0] for c in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def enabled(db, repo: str, issue: int) -> bool:
    try:
        row = db.execute("SELECT enabled FROM review_video_tickets WHERE repo=? AND issue=?", (repo, int(issue))).fetchone()
    except sqlite3.OperationalError:
        return False
    return bool(row and row[0])


def ticket_voice(db, repo: str, issue: int) -> tuple[str, str]:
    """(engine, ElevenLabs voice id) a ticket chose; '' for either means the config's."""
    try:
        row = db.execute("SELECT voice, voice_id FROM review_video_tickets WHERE repo=? AND issue=?", (repo, int(issue))).fetchone()
    except sqlite3.OperationalError:
        return "", ""
    return (row[0] or "", row[1] or "") if row else ("", "")


def voice_for(vc, chosen: tuple[str, str]) -> tuple[str, str]:
    """The engine and voice id a video is spoken with: the ticket's choice over the config's."""
    engine = chosen[0] if chosen[0] in voices.ENGINES else vc.voice
    return engine, (chosen[1] or vc.elevenlabs_voice_id) if engine == "elevenlabs" else ""


def build_prs(db, repo: str, issue: int) -> list[tuple[str, int]]:
    """The ticket's open build PRs the factory recorded (design PRs left out)."""
    from . import db as dbm
    design = dbm.design_prs(db, repo, issue)
    return [(r["repo"], int(r["number"])) for r in dbm.conflicts_for_issue(db, repo, issue) if (r["repo"], int(r["number"])) not in design]


def queue(db, issue_repo: str, issue_num: int, pr_repo: str, pr_number: int, now: float, again: bool = False,
          wait_review: bool = False) -> int | None:
    """Queue one video of a PR. Without `again`, only when the PR never had one (turning the switch off and on queues nothing new);
    with it, when none is queued or being made. The new row's id, else None."""
    ensure_tables(db)
    have = {r[0] for r in db.execute("SELECT status FROM review_videos WHERE pr_repo=? AND pr_number=?", (pr_repo, int(pr_number)))}
    if have & set(ACTIVE) or (have and not again):
        return None
    cur = db.execute("INSERT INTO review_videos (issue_repo, issue_num, pr_repo, pr_number, created, wait_review) VALUES (?,?,?,?,?,?)",
                     (issue_repo, int(issue_num), pr_repo, int(pr_number), now, int(wait_review)))
    db.commit()
    return cur.lastrowid


def set_enabled(db, repo: str, issue: int, on: bool, who: str, now: float, voice: str = "", voice_id: str = "") -> int:
    """Save the switch and the voice. Turned on, every open build PR of the ticket without a video gets one queued. Returns how
    many were. `voice` is '' (the config's), piper or elevenlabs; `voice_id` '' or an ElevenLabs voice id (checked here)."""
    if voice not in ("", *voices.ENGINES) or voice_id and not voices.VOICE_ID.fullmatch(voice_id):
        raise ValueError("not a voice")
    ensure_tables(db)
    db.execute("INSERT INTO review_video_tickets (repo, issue, enabled, updated, updated_by, voice, voice_id) VALUES (?,?,?,?,?,?,?) "
               "ON CONFLICT(repo, issue) DO UPDATE SET enabled=excluded.enabled, updated=excluded.updated, updated_by=excluded.updated_by, "
               "voice=excluded.voice, voice_id=excluded.voice_id",
               (repo, int(issue), int(bool(on)), now, who, voice, voice_id))
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
    return queue(db, issue_repo, issue_num, pr_repo, pr_number, now, wait_review=True) if enabled(db, issue_repo, issue_num) else None


def wanted(db, prs: list) -> bool:
    """Whether any of these PRs belongs to a ticket with the switch on (the reviewer is then asked for the video block)."""
    try:
        return any(db.execute("SELECT 1 FROM review_videos v JOIN review_video_tickets t ON t.repo=v.issue_repo AND t.issue=v.issue_num "
                              "WHERE t.enabled=1 AND v.pr_repo=? AND v.pr_number=? LIMIT 1", (r, int(n))).fetchone() for r, n in prs)
    except sqlite3.OperationalError:
        return False


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


# ---- the reviewer's narration ----

def clean(text, n: int) -> str:
    """Model or repository text made safe to speak and show: printable characters, single spaces, no Markdown or tags, at most n."""
    s = "".join(c if c.isprintable() else " " for c in str(text or ""))
    s = re.sub(r"[`*_<>\[\]{}|#\\]", " ", s)
    s = re.sub(r"https?://\S+", "", s)
    return sanitize_markdown(" ".join(s.split())[:n].strip(), n)


def speakable(text, n: int) -> str:
    """Names (files, screens, titles) cut to a small set of characters, single spaces, at most n characters."""
    return " ".join(SPOKEN.sub(" ", str(text or "")).split())[:n].strip()


def _scene(d) -> dict | None:
    if not isinstance(d, dict) or d.get("kind") not in KINDS:
        return None
    s = {"kind": d["kind"], "say": clean(d.get("say"), 300)}
    if d["kind"] in ("title", "points"):
        s["title"] = clean(d.get("title"), 120)
        if not s["title"]:
            return None
    if d["kind"] == "code":
        f, h = d.get("file"), d.get("hunk", 0)
        if not isinstance(f, str) or not 0 < len(f) <= 300 or not isinstance(h, int) or isinstance(h, bool) or not 0 <= h < 200:
            return None
        s.update(file=f, hunk=h)
    if d["kind"] == "shot":
        if not isinstance(d.get("screen"), str) or not SCREEN.fullmatch(d["screen"]):
            return None
        s["screen"] = d["screen"]
    if d["kind"] == "points":
        pts = [clean(p, 140) for p in d.get("points") or [] if isinstance(p, str)] if isinstance(d.get("points"), list) else []
        s["points"] = [p for p in pts if p][:MAX_POINTS]
        if not s["points"]:
            return None
    return s


def parse_block(text: str, prs: list) -> dict[tuple[str, int], list[dict]]:
    """{(repo, number): scenes} from the last `factory-video` block, for the reviewed PRs only, each scene checked; {} without one."""
    blocks = BLOCK.findall(text or "")
    try:
        data = json.loads(blocks[-1]) if blocks else None
    except ValueError:
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("videos"), list):
        return {}
    mine, out = {(r, int(n)) for r, n in prs}, {}
    for v in data["videos"][:20]:
        m = PR_REF.fullmatch(v.get("pr", "")) if isinstance(v, dict) and isinstance(v.get("pr"), str) else None
        key = (m[1], int(m[2])) if m else None
        if key not in mine or key in out or not isinstance(v.get("scenes"), list):
            continue
        scenes = [s for s in map(_scene, v["scenes"][:MAX_SCENES * 2]) if s][:MAX_SCENES]
        if scenes:
            out[key] = scenes
    return out


def strip(text: str) -> str:
    """The review without its video block, so the PR comment never echoes the JSON."""
    return BLOCK.sub("", text or "").rstrip()


def store_notes(db, found: dict, now: float) -> None:
    ensure_tables(db)
    for (r, n), scenes in found.items():
        db.execute("INSERT INTO review_video_notes (pr_repo, pr_number, created, scenes) VALUES (?,?,?,?) ON CONFLICT(pr_repo, pr_number) "
                   "DO UPDATE SET created=excluded.created, scenes=excluded.scenes", (r, int(n), now, json.dumps(scenes)))
    db.commit()


def notes(db, pr_repo: str, pr_number: int) -> list[dict] | None:
    try:
        row = db.execute("SELECT scenes FROM review_video_notes WHERE pr_repo=? AND pr_number=?", (pr_repo, int(pr_number))).fetchone()
        return [s for s in map(_scene, json.loads(row[0])) if s] if row else None
    except (sqlite3.OperationalError, ValueError, TypeError):
        return None


def reviewed(cfg, gh, conn, prs: list, output: str, now: float) -> str:
    """After an automated review: keep its narration for the PRs' videos and requeue a video already made from fixed wording.
    Returns the review without the block. Never fails a review."""
    if conn is None:
        return strip(output)
    try:
        found = parse_block(output, prs)
        store_notes(conn, found, now)
        for r, n in found:
            last = newest_for_pr(conn, r, n)
            if last and last["status"] in ("ready", "stale", "skipped") and last.get("narration") != "review":
                queue(conn, last["issue_repo"], last["issue_num"], r, n, now, again=True)
    except Exception:
        log.exception("videos: could not keep the review's narration")
    return strip(output)


# ---- the script ----

WHAT = {"added": "new file", "removed": "deleted", "renamed": "renamed", "copied": "copied"}


def hunks(patch: str) -> list[list[str]]:
    """A file's diff split into hunks (each starting at its @@ line)."""
    out: list[list[str]] = []
    for line in str(patch or "").splitlines():
        if line.startswith("@@") or not out:
            out.append([])
        out[-1].append(line)
    return out


def code_lines(f: dict, hunk: int = 0) -> list[dict]:
    """The lines a code scene shows, taken from the PR's own diff (never from model text), cut to fixed limits."""
    hs = hunks(f.get("patch"))
    pick = hs[hunk] if 0 <= hunk < len(hs) else (hs[0] if hs else [])
    out = []
    for line in pick[:HUNK_LINES]:
        t = "@" if line.startswith("@@") else line[:1] if line[:1] in "+-" else " "
        body = line if t == "@" else line[1:] if line[:1] in "+- " else line
        out.append({"t": t, "text": "".join(c if c.isprintable() else " " for c in body)[:LINE_CHARS]})
    return out


def _file_scene(f: dict, say: str, hunk: int = 0) -> dict:
    a, d = int(f.get("additions") or 0), int(f.get("deletions") or 0)
    name = str(f.get("filename"))[:200]
    return {"kind": "code", "title": "".join(c if c.isprintable() else " " for c in name),
            "subtitle": f"{WHAT.get(f.get('status'), 'changed')} · +{a} −{d}", "lines": code_lines(f, hunk), "say": say}


def fixed_script(pr_number: int, title: str, files: list[dict], shots: list[str]) -> list[dict]:
    """The narration when there is no review block: fixed wording with only cleaned names and numbers."""
    files = [f for f in files if isinstance(f, dict) and speakable(f.get("filename"), 160)]
    plus = sum(int(f.get("additions") or 0) for f in files)
    minus = sum(int(f.get("deletions") or 0) for f in files)
    t = speakable(title, 80)
    scenes = [{"kind": "title", "title": f"Pull request {int(pr_number)}", "subtitle": t,
               "say": f"Review of pull request {int(pr_number)}{': ' + t if t else ''}. It changes {len(files)} file{'' if len(files) == 1 else 's'}, "
                      f"with {plus} line{'' if plus == 1 else 's'} added and {minus} removed."}]
    for name in shots[:MAX_SHOTS]:
        scenes.append({"kind": "shot", "screen": name, "title": f"{speakable(name.replace('-', ' '), 60)} screen",
                       "say": f"The {speakable(name.replace('-', ' '), 60)} screen, as built."})
    room = MAX_SCENES - len(scenes) - (1 if len(files) > MAX_SCENES - len(scenes) else 0)
    for f in files[:room]:
        a, d = int(f.get("additions") or 0), int(f.get("deletions") or 0)
        scenes.append(_file_scene(f, f"{speakable(f['filename'], 160)}: {WHAT.get(f.get('status'), 'changed')}, "
                                     f"{a} line{'' if a == 1 else 's'} added and {d} removed."))
    if len(files) > room:
        rest = files[room:]
        scenes.append({"kind": "points", "title": "Also changed", "points": [speakable(f["filename"], 140) for f in rest[:MAX_POINTS]],
                       "say": f"{len(rest)} more file{'' if len(rest) == 1 else 's'} are in the pull request but not in this video."})
    return scenes


def review_script(found: list[dict], files: list[dict], shots: list[str]) -> list[dict]:
    """The reviewer's scenes made concrete: a code scene gets its lines from the named file's diff (a file the PR does not change
    drops the scene), a screen scene the named screenshot (one the build did not take drops it)."""
    by_name = {f.get("filename"): f for f in files if isinstance(f, dict)}
    out = []
    for s in found:
        if s["kind"] == "code":
            f = by_name.get(s["file"])
            if f is None:
                continue
            out.append(_file_scene(f, s["say"], s.get("hunk", 0)))
        elif s["kind"] == "shot":
            if s["screen"] not in shots:
                continue
            out.append({"kind": "shot", "screen": s["screen"], "title": f"{speakable(s['screen'].replace('-', ' '), 60)} screen", "say": s["say"]})
        else:
            out.append({k: v for k, v in s.items()})
    return out[:MAX_SCENES]


def script(pr_number: int, title: str, files: list[dict], shots: list[str], found: list[dict] | None = None) -> tuple[list[dict], str]:
    """(scenes, narration): the reviewer's scenes when at least two survive the checks, else the fixed wording."""
    if found:
        scenes = review_script(found, files, shots)
        if len(scenes) >= 2:
            return scenes, "review"
    return fixed_script(pr_number, title, files, shots), "fixed"


def built_shots(db, repo: str, issue: int) -> dict[str, bytes]:
    """The screenshots of the ticket's newest build run that took any ({name: PNG}, each re-checked), at most MAX_SHOTS."""
    try:
        rows = db.execute("SELECT i.name, i.png FROM run_images i WHERE i.kind='built' AND i.run_id=(SELECT MAX(r.id) FROM runs r "
                          "JOIN run_images j ON j.run_id=r.id AND j.kind='built' WHERE r.repo=? AND r.issue=?) ORDER BY i.name LIMIT ?",
                          (repo, int(issue), MAX_SHOTS)).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {n: bytes(p) for n, p in rows if SCREEN.fullmatch(str(n)) and render.png_ok(bytes(p))}


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


def spec(vc, scenes: list[dict], voice: str) -> dict:
    """script.json for the container (sandbox/video/render.mjs checks it again): only the fields its templates use."""
    keep = {"title": ("title", "subtitle"), "shot": ("title",), "code": ("title", "subtitle", "lines"), "points": ("title", "points")}
    out = []
    for i, s in enumerate(scenes, 1):
        c = {"kind": s["kind"], "say": s.get("say", "")}
        c.update({k: s[k] for k in keep[s["kind"]] if s.get(k)})
        if s["kind"] == "shot":
            c["image"] = f"s{i:02d}.png"
        if voice == "files" and c["say"]:
            c["audio"] = f"s{i:02d}.wav"
        out.append(c)
    return {"width": 1280, "height": 720, "fps": 30, "max_seconds": vc.max_seconds, "voice": voice, "scenes": out}


def speech(vc, engine: str, voice_id: str, scenes: list[dict], post=None) -> dict[str, bytes]:
    """{sNN.wav: WAV} of each spoken scene, made with ElevenLabs; {} for Piper (the container speaks). Raises Skip('voice')."""
    if engine != "elevenlabs":
        return {}
    key = voices.read_key(vc.elevenlabs_key_file)
    out = {}
    try:
        for i, s in enumerate(scenes, 1):
            if s.get("say"):
                out[f"s{i:02d}.wav"] = voices.elevenlabs(key, voice_id, vc.elevenlabs_model, s["say"], **({"post": post} if post else {}))
    except voices.VoiceError as e:
        log.warning("videos: %s", e)
        raise Skip("voice") from None
    return out


def _compose(cfg, script_json: dict, images: dict[str, bytes], audio: dict[str, bytes], run) -> tuple[Path, Path, Path]:
    """Run the video container. (work dir, review.mp4, timings.json); the caller removes the work dir."""
    rn, vc = cfg.runner, cfg.videos
    Path(rn.work_dir).mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="video-", dir=rn.work_dir))
    for d in ("in/img", "in/audio", "out"):
        (work / d).mkdir(parents=True)
    for name, data in images.items():
        (work / "in" / "img" / name).write_bytes(data)
    for name, data in audio.items():
        (work / "in" / "audio" / name).write_bytes(data)
    (work / "in" / "script.json").write_text(json.dumps(script_json))
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
    code = getattr(res, "returncode", 0)
    if code != 0:
        log.warning("videos: the video container exited %s: %s", code, (getattr(res, "stderr", "") or "")[-300:])
        shutil.rmtree(work, ignore_errors=True)
        raise Skip("too_long" if code == 3 else "tool_failed")
    return work, work / "out" / "review.mp4", work / "out" / "timings.json"


def make(cfg, gh, db, row: dict, run=subprocess.run, post=None) -> dict:
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
    scenes, narration = script(row["pr_number"], pr.get("title") or "", files, sorted(shots), notes(db, row["pr_repo"], row["pr_number"]))
    engine, voice_id = voice_for(vc, ticket_voice(db, row["issue_repo"], row["issue_num"]))
    audio = speech(vc, engine, voice_id, scenes, post)
    images = {f"s{i:02d}.png": shots[s["screen"]] for i, s in enumerate(scenes, 1) if s["kind"] == "shot"}
    work, mp4, tim = _compose(cfg, spec(vc, scenes, "files" if engine == "elevenlabs" else "piper"), images, audio, run)
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
            if s.get("say"):
                lines.append([round(start, 1), s["say"]])
            start += t
        out = video_dir(cfg)
        out.mkdir(mode=0o700, parents=True, exist_ok=True)
        dest = video_path(cfg, row)
        part = dest.with_name(dest.name + ".part")
        shutil.copyfile(mp4, part)
        part.chmod(0o600)
        part.replace(dest)
        return {"head_sha": str((pr.get("head") or {}).get("sha") or "")[:64], "pr_title": speakable(pr.get("title"), 120),
                "mode": "ui" if images else "diff", "seconds": seconds, "bytes": dest.stat().st_size, "transcript": json.dumps(lines),
                "narration": narration, "voice": engine}
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


def work_one(cfg, gh, db, row: dict, now_fn, run=subprocess.run, post=None) -> None:
    try:
        done = make(cfg, gh, db, row, run, post)
    except Skip as e:
        return _skip(cfg, gh, db, row, e.args[0], now_fn())
    except Exception:
        log.exception("videos: making the video of %s#%s failed", row["pr_repo"], row["pr_number"])
        return _skip(cfg, gh, db, row, "tool_failed", now_fn())
    db.execute("UPDATE review_videos SET status='ready', reason='', finished=?, checked=?, head_sha=?, pr_title=?, mode=?, seconds=?, bytes=?, "
               "transcript=?, narration=?, voice=? WHERE id=?",
               (now_fn(), now_fn(), done["head_sha"], done["pr_title"], done["mode"], done["seconds"], done["bytes"], done["transcript"],
                done["narration"], done["voice"], row["id"]))
    db.commit()
    row = get(db, row["id"])
    _say(cfg, gh, row["pr_repo"], row["pr_number"], links(cfg, row))
    _say(cfg, gh, row["issue_repo"], row["issue_num"], f"{row['pr_repo']}#{int(row['pr_number'])}: " + links(cfg, row))
    _event(db, f"review video for {row['pr_repo']}#{int(row['pr_number'])} made ({clock(row['seconds'])}, {row['mode']}, "
               f"{row['narration']} narration, {row['voice']})", row)


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


def waiting_for_review(cfg, db, row: dict, now: float) -> bool:
    """A new PR's video waits (up to REVIEW_WAIT) for the automated review's narration, when a review will run on its own."""
    return (bool(row.get("wait_review")) and cfg.review.enabled and cfg.review.auto and now - row["created"] < REVIEW_WAIT
            and notes(db, row["pr_repo"], row["pr_number"]) is None)


def tick(cfg, gh, db, now: float, connect, now_fn, threaded: bool = True, run=subprocess.run) -> bool:
    """Expire old videos, mark out-of-date ones, and start the oldest queued video that is not waiting for its review, when none is
    being made and the daily limit allows (else it is skipped with that reason). True when one was started."""
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
    row = next((r for r in _dicts(db.execute("SELECT * FROM review_videos WHERE status='queued' ORDER BY id LIMIT 50"))
                if not waiting_for_review(cfg, db, r, now)), None)
    if row is None:
        _running.release()
        return False
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
