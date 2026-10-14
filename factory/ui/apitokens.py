"""Tokens for the JSON API (api.py), which the command-line client uses. Kept in state/api-tokens.json (mode 0600); only each
token's SHA-256 is stored, the token itself is shown once when it is made. A token has 256 random bits, so a fast hash is enough:
nothing could be guessed from it. The file is read again when it changes, so `ctl tokens add|revoke` work without a restart.
Both the UI and ctl write it: every change takes a lock on api-tokens.lock and re-reads the file first."""
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from contextlib import contextmanager
from pathlib import Path

NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,40}")
SCOPES = ("read", "write")
PREFIX = "shk_"
TOUCH_EVERY = 3600          # last_used is written back at most this often per token
MAX_TOKENS = 50


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._seen: tuple | None = None          # (inode, mtime_ns, size) of the file last read: every write replaces it
        self._tokens: list[dict] = []

    # ------------------------------------------------------------------ reading
    def _read(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return []
        rows = data.get("tokens") if isinstance(data, dict) else None
        return [t for t in rows if isinstance(t, dict) and isinstance(t.get("hash"), str)] if isinstance(rows, list) else []

    def tokens(self) -> list[dict]:
        """The stored records, re-read when the file has changed."""
        try:
            st = self.path.stat()
            seen = (st.st_ino, st.st_mtime_ns, st.st_size)
        except OSError:
            seen = None
        with self._lock:
            if seen != self._seen:
                self._tokens, self._seen = (self._read() if seen else []), seen
            return list(self._tokens)

    def list(self) -> list[dict]:
        """name, id, scope, created, last_used of each token: never its hash."""
        return [{k: t.get(k) for k in ("name", "id", "scope", "created", "last_used")} for t in self.tokens()]

    # ------------------------------------------------------------------ writing
    @contextmanager
    def _change(self):
        """Lock, re-read, let the caller change the list, write it back atomically (mode 0600)."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = os.open(self.path.with_suffix(".lock"), os.O_WRONLY | os.O_CREAT, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            rows = self._read()
            yield rows
            tmp = self.path.with_suffix(f".tmp{os.getpid()}")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump({"version": 1, "tokens": rows}, f, indent=1)
            os.replace(tmp, self.path)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            os.close(lock)
        with self._lock:
            self._seen = None                    # read again on the next request

    def add(self, name: str, scope: str = "write", now: float | None = None) -> str:
        """Make a token and return it (the only time it is seen). Raises ValueError for a bad or taken name."""
        if not NAME.fullmatch(name or ""):
            raise ValueError("A token name is lowercase letters, digits and dashes (at most 41 characters).")
        if scope not in SCOPES:
            raise ValueError("A token is read or write.")
        with self._change() as rows:
            if any(t.get("name") == name for t in rows):
                raise ValueError(f"A token called {name} already exists. Revoke it first to replace it.")
            if len(rows) >= MAX_TOKENS:
                raise ValueError(f"There are {MAX_TOKENS} tokens already. Revoke one first.")
            tid = "".join(secrets.choice("abcdefghijkmnpqrstuvwxyz23456789") for _ in range(6))
            token = f"{PREFIX}{tid}_{secrets.token_urlsafe(32)}"
            rows.append({"name": name, "id": tid, "scope": scope, "hash": _digest(token),
                         "created": int(now if now is not None else time.time()), "last_used": None})
        return token

    def revoke(self, name_or_id: str) -> bool:
        """Remove a token by its name or id. False when there is none."""
        gone = False
        with self._change() as rows:
            keep = [t for t in rows if name_or_id not in (t.get("name"), t.get("id"))]
            gone = len(keep) != len(rows)
            rows[:] = keep
        return gone

    # ------------------------------------------------------------------ checking
    def authenticate(self, header: str | None) -> dict | None:
        """The token record for an `Authorization: Bearer <token>` header, or None. Every stored hash is compared, in constant time."""
        if not header or not header.startswith("Bearer "):
            return None
        given = header[7:].strip()
        if not given.startswith(PREFIX) or len(given) > 200:
            return None
        digest, found = _digest(given).encode(), None
        for t in self.tokens():
            if hmac.compare_digest(digest, t["hash"].encode()):
                found = t
        return {k: found.get(k) for k in ("name", "id", "scope", "created", "last_used")} if found else None

    def touch(self, token: dict, now: float | None = None) -> None:
        """Note that a token was used; written at most once an hour per token, and never fatal."""
        now = now if now is not None else time.time()
        if token.get("last_used") and now - token["last_used"] < TOUCH_EVERY:
            return
        try:
            with self._change() as rows:
                for t in rows:
                    if t.get("id") == token.get("id"):
                        t["last_used"] = int(now)
        except OSError:
            pass
