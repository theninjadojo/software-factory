"""Back up and restore the factory from the admin UI. See docs/operations.md ("Backup and restore") and SECURITY.md.

A backup is a zip: manifest.json, a sanitised copy of the database, and the two config files. It never holds secrets
(files under secrets/, the UI password), run output, logs, agent patches or queued work. A restore is untrusted input: the
upload is never put in place, its rows are copied into a fresh database built by `db.connect`, and the swap happens at the
orchestrator's next start, before it opens the database."""
import email.parser
import hashlib
import json
import mmap
import os
import re
import shutil
import sqlite3
import tempfile
import time
import tomllib
import zipfile
from pathlib import Path

from . import db as dbm
from . import updates, version
from .config import deep_merge, overrides_path, parse

FORMAT = 1
MAX_RESTORE = 512 * 1024 * 1024                  # the upload
MAX_UNPACKED = 2 * 1024 ** 3                     # all members together, uncompressed
MAX_CONFIG = 1024 * 1024
CHUNK = 64 * 1024
MEMBERS = ("manifest.json", "factory.db", "config.toml", "config.overrides.toml")
TMP_BACKUP, TMP_RESTORE, STAGED, MARKER, LAST = ".backup-tmp", ".restore-tmp", "restore", "RESTORE.json", "last_backup.json"

BLANK = {"runs": ("output", "log_tail"), "verify_jobs": ("patch", "log", "progress", "tests"),
         "design_imports": ("html", "png"), "verify_reports": ("html",)}          # run output, logs, test reports and agent-written diffs
SKIP = ("approvals", "schedule_requests")                                          # queued actions: restoring them would start work
SKIP_WHERE = {"verify_jobs": "status IN ('queued','claimed')"}                      # work a worker has not finished (its patch is blanked)


class BackupError(Exception):
    """A message safe to show a person."""


def _tables(db) -> list[str]:
    return [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def _cols(db, table: str, schema: str = "main") -> list[str]:
    return [r[1] for r in db.execute(f'PRAGMA {schema}.table_info("{table}")')]


def sweep(state: Path) -> None:
    """Remove temporary files a crashed request left behind (at UI start, when no request can be using them)."""
    for name in (TMP_BACKUP, TMP_RESTORE):
        shutil.rmtree(Path(state) / name, ignore_errors=True)


def tmpdir(state: Path, kind: str) -> Path:
    """A private work directory in the state volume (not /tmp: containers run with a RAM-backed one)."""
    base = Path(state) / kind
    base.mkdir(mode=0o700, exist_ok=True)
    return Path(tempfile.mkdtemp(dir=base))


# ---------------------------------------------------------------- backup
def snapshot(db_path: str, out: Path) -> Path:
    """A consistent copy of the live database taken with SQLite's online backup (safe while the orchestrator writes),
    then cleaned of everything a backup must not carry."""
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
    dst = sqlite3.connect(out)
    try:
        src.backup(dst)
    finally:
        src.close()
    try:
        have = set(_tables(dst))
        for t in SKIP:
            if t in have:
                dst.execute(f"DELETE FROM {t}")
        for t, cols in BLANK.items():
            if t in have:
                if t in SKIP_WHERE:
                    if "verify_artifacts" in have:
                        dst.execute(f"DELETE FROM verify_artifacts WHERE job_id IN (SELECT id FROM {t} WHERE {SKIP_WHERE[t]})")
                    dst.execute(f"DELETE FROM {t} WHERE {SKIP_WHERE[t]}")
                dst.execute(f"UPDATE {t} SET " + ", ".join(f"{c}=''" for c in cols))
        dst.commit()
        dst.execute("VACUUM")                      # blanked text must not linger in free pages
        dst.execute("PRAGMA journal_mode=DELETE")   # one file, no -wal
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise BackupError("The database copy failed its integrity check.")
    finally:
        dst.close()
    return out


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def build_bundle(cfg_path: str, db_path: str, work: Path) -> Path:
    """work/backup.zip for the factory whose config is cfg_path. Raises BackupError."""
    snap = snapshot(db_path, work / "factory.db")
    files = {"factory.db": snap}
    for name, p in (("config.toml", Path(cfg_path)), ("config.overrides.toml", overrides_path(cfg_path))):
        if p.is_file():
            files[name] = p
    db = sqlite3.connect(snap)
    try:
        counts = {t: db.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in _tables(db)}
    finally:
        db.close()
    manifest = {"format": FORMAT, "factory_version": version.current(), "created": time.time(), "tables": counts,
                "sha256": {n: _sha(p) for n, p in files.items()},
                "excluded": ["secrets", "UI password", "run output and logs", "agent patches", "queued approvals, schedule requests and verification jobs"]}
    out = work / "backup.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=1))
        for n, p in files.items():
            z.write(p, n)
    return out


def record_backup(state: Path, nbytes: int) -> None:
    (Path(state) / LAST).write_text(json.dumps({"at": time.time(), "bytes": nbytes}))


def last_backup(state: Path) -> dict | None:
    try:
        d = json.loads((Path(state) / LAST).read_text())
        return {"at": float(d["at"]), "bytes": int(d["bytes"])}
    except (OSError, ValueError, KeyError, TypeError):
        return None


# ---------------------------------------------------------------- reading an upload
def read_bundle(zip_path: Path, work: Path) -> tuple[dict, dict[str, Path]]:
    """Unpack only the known members into work, with size and checksum checks. Returns (manifest, {name: path})."""
    try:
        z = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError):
        raise BackupError("That is not a backup file (not a zip).")
    with z:
        names = [i.filename for i in z.infolist()]
        if len(set(names)) != len(names) or any(n not in MEMBERS for n in names):
            raise BackupError("That is not a backup file (unexpected contents).")
        if "manifest.json" not in names or "factory.db" not in names:
            raise BackupError("That is not a backup file (manifest or database missing).")
        total, out = 0, {}
        for info in z.infolist():
            dest = work / info.filename
            cap, size = (MAX_UNPACKED if info.filename == "factory.db" else MAX_CONFIG), 0
            with z.open(info) as src, open(dest, "wb") as dst:
                while chunk := src.read(CHUNK):
                    total += len(chunk)
                    size += len(chunk)
                    if total > MAX_UNPACKED or size > cap:
                        raise BackupError("The backup is too large when unpacked.")
                    dst.write(chunk)
            out[info.filename] = dest
    try:
        manifest = json.loads(out["manifest.json"].read_text())
        shas = manifest["sha256"]
        if manifest["format"] != FORMAT:
            raise BackupError(f"This backup has format {manifest['format']}; this factory reads format {FORMAT}.")
        theirs, mine = str(manifest.get("factory_version", "")), version.current()
        if updates.newer(theirs, mine):
            raise BackupError(f"This backup is from factory {theirs}, newer than this one ({mine}). Update the factory first.")
        for name, p in out.items():
            if name != "manifest.json" and shas.get(name) != _sha(p):
                raise BackupError(f"{name} does not match its checksum: the backup is damaged or was changed.")
    except (ValueError, KeyError, TypeError, AttributeError):
        raise BackupError("That is not a backup file (bad manifest).")
    return manifest, out


def rebuild_db(untrusted: Path, out: Path) -> dict:
    """A fresh database (this factory's schema) holding the rows of the uploaded one. Nothing else comes across: no triggers,
    views, indexes or unknown tables, and the columns a backup excludes are blank whatever the upload says."""
    new = dbm.connect(str(out))
    try:
        up = sqlite3.connect(f"file:{untrusted}?mode=ro", uri=True)
        try:
            up.execute("PRAGMA trusted_schema=OFF")
            if up.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise BackupError("The database in the backup is damaged.")
            theirs = {r[0] for r in up.execute("SELECT name FROM sqlite_master WHERE type='table'")}   # tables only: a view may not stand in for one
        except sqlite3.DatabaseError:
            raise BackupError("The database in the backup cannot be read.")
        finally:
            up.close()
        new.execute("ATTACH DATABASE ? AS up", (str(untrusted),))      # only ever read from
        new.execute("PRAGMA trusted_schema=OFF")
        counts = {}
        for t in _tables(new):
            if t in SKIP or t not in theirs:
                continue
            common = [c for c in _cols(new, t) if c in _cols(new, t, "up")]
            if not common:
                continue
            sel = ", ".join(f"''" if c in BLANK.get(t, ()) else f'"{c}"' for c in common)
            where = f" WHERE NOT ({SKIP_WHERE[t]})" if t in SKIP_WHERE else ""
            new.execute(f'INSERT OR REPLACE INTO main."{t}" ({", ".join(chr(34) + c + chr(34) for c in common)}) SELECT {sel} FROM up."{t}"{where}')
            counts[t] = new.execute(f'SELECT COUNT(*) FROM main."{t}"').fetchone()[0]
        new.commit()
        new.execute("DETACH DATABASE up")
        new.execute("PRAGMA journal_mode=DELETE")
    except sqlite3.DatabaseError as e:
        raise BackupError(f"The database in the backup could not be restored: {e}")
    finally:
        new.close()
    return counts


# ---------------------------------------------------------------- multipart upload
def read_parts(path: Path, content_type: str, max_field: int = 256, max_parts: int = 64,
               truncate: bool = False) -> tuple[dict, list[tuple[str, str, tuple[int, int]]]]:
    """The text fields and the file parts ((field name, file name, byte span)) of a multipart/form-data body saved at path
    (read through mmap, so a large upload is never held in memory). A text field longer than max_field is dropped, or with
    truncate cut to max_field + 1 bytes so the caller's own length check refuses it. More than max_parts parts is refused."""
    m = re.search(r'boundary="?([^";\s]{1,200})"?', content_type or "")
    if not m or not (content_type or "").lower().startswith("multipart/form-data"):
        raise BackupError("Expected a multipart upload.")
    delim = b"--" + m.group(1).encode()
    fields, files, count = {}, [], 0
    if path.stat().st_size == 0:
        raise BackupError("Empty upload.")
    with open(path, "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as buf:
        pos = buf.find(delim)
        while pos != -1:
            start = pos + len(delim)
            if buf[start:start + 2] == b"--":
                break
            count += 1
            if count > max_parts:
                raise BackupError("The upload has too many parts.")
            head_end = buf.find(b"\r\n\r\n", start)
            nxt = buf.find(b"\r\n" + delim, head_end) if head_end != -1 else -1
            if head_end == -1 or nxt == -1 or head_end - start > 4096:
                raise BackupError("The upload is malformed.")
            msg = email.parser.BytesParser().parsebytes(buf[start + 2:head_end] + b"\r\n\r\n", headersonly=True)
            name = msg.get_param("name", header="content-disposition")
            fname = msg.get_param("filename", header="content-disposition")
            if fname is not None:
                files.append((name or "", str(fname), (head_end + 4, nxt)))
            elif name and (nxt - (head_end + 4) <= max_field or truncate):
                fields[name] = buf[head_end + 4:min(nxt, head_end + 4 + max_field + 1)].decode("utf-8", "replace")
            pos = nxt + 2
    return fields, files


def read_multipart(path: Path, content_type: str, max_field: int = 256) -> tuple[dict, tuple[int, int] | None]:
    """The text fields and the byte span of the (first) file part named `file` of a multipart/form-data body saved at path."""
    fields, files = read_parts(path, content_type, max_field)
    return fields, next((span for name, _, span in files if name == "file"), None)


def read_span(src: Path, span: tuple[int, int]) -> bytes:
    with open(src, "rb") as f:
        f.seek(span[0])
        return f.read(span[1] - span[0])


def copy_span(src: Path, span: tuple[int, int], dst: Path) -> None:
    with open(src, "rb") as f, open(dst, "wb") as out:
        f.seek(span[0])
        left = span[1] - span[0]
        while left > 0:
            chunk = f.read(min(CHUNK, left))
            if not chunk:
                break
            out.write(chunk)
            left -= len(chunk)


# ---------------------------------------------------------------- restore
def _replace_file(p: Path, data: bytes) -> None:
    """Write p (atomically, keeping its mode), keeping the previous contents as p.bak."""
    if p.exists():
        shutil.copy2(p, p.with_name(p.name + ".bak"))
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    if p.exists():
        shutil.copymode(p, tmp)
    os.replace(tmp, p)


def stage_restore(cfg_path: str, state: Path, bundle: Path) -> dict:
    """Validate the uploaded bundle and stage it: the database waits in state/restore for the orchestrator's next start, the
    config files are replaced (the old ones kept as .bak), and the factory is paused. Raises BackupError before changing anything."""
    state, cfg_path = Path(state), str(cfg_path)
    work = tmpdir(state, TMP_RESTORE)
    try:
        manifest, files = read_bundle(bundle, work)
        configs = {}
        if "config.toml" not in files:
            raise BackupError("This backup has no config.toml.")
        try:
            base = tomllib.loads(files["config.toml"].read_text())
            ov = tomllib.loads(files["config.overrides.toml"].read_text()) if "config.overrides.toml" in files else {}
            new_cfg = parse(deep_merge(base, ov))
            cur_db = parse(deep_merge(tomllib.loads(Path(cfg_path).read_text()), _current_overrides(cfg_path))).db_path
        except (ValueError, KeyError, TypeError, OSError) as e:
            raise BackupError(f"The configuration in the backup is not valid: {e}")
        if os.path.abspath(new_cfg.db_path) != os.path.abspath(cur_db):
            raise BackupError("The backup's configuration puts the database somewhere else than this install does (general.db_path). "
                              "Restore it on an install with the same layout.")
        for name in ("config.toml", "config.overrides.toml"):
            if name in files:
                configs[name] = files[name].read_bytes()
        rebuilt = work / "rebuilt.db"
        counts = rebuild_db(files["factory.db"], rebuilt)
        staged = state / STAGED
        shutil.rmtree(staged, ignore_errors=True)
        staged.mkdir(mode=0o700)
        os.replace(rebuilt, staged / "factory.db")
        (state / MARKER).write_text(json.dumps({"staged": time.time(), "created": manifest.get("created"), "factory_version": manifest.get("factory_version"), "tables": counts}))
        _replace_file(Path(cfg_path), configs["config.toml"])
        ov_path = overrides_path(cfg_path)
        if "config.overrides.toml" in configs:
            _replace_file(ov_path, configs["config.overrides.toml"])
        elif ov_path.exists():
            os.replace(ov_path, ov_path.with_name(ov_path.name + ".bak"))
        (state / "PAUSED").write_text("")
        (state / "RESTART").write_text("")
        return manifest
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _current_overrides(cfg_path: str) -> dict:
    p = overrides_path(cfg_path)
    return tomllib.loads(p.read_text()) if p.exists() else {}


def apply_pending(state: Path, db_path: str) -> dict | None:
    """Orchestrator start, before the database is opened: swap in a staged restore. The previous database (and its -wal and
    -shm) is renamed factory.db.pre-restore-<time>, never deleted. Returns the marker, or None when nothing was staged."""
    state = Path(state)
    staged, marker = state / STAGED / "factory.db", state / MARKER
    if not staged.is_file() or not marker.is_file():
        return None
    try:
        info = json.loads(marker.read_text())
    except ValueError:
        info = {}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for suffix in ("", "-wal", "-shm"):
        old = Path(db_path + suffix)
        if old.exists():
            os.replace(old, Path(f"{db_path}.pre-restore-{stamp}{suffix}"))
    os.replace(staged, db_path)
    shutil.rmtree(state / STAGED, ignore_errors=True)
    marker.unlink(missing_ok=True)
    info["previous"] = f"{Path(db_path).name}.pre-restore-{stamp}"
    return info
