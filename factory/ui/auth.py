"""Authentication for the admin UI: one password (scrypt-hashed), in-memory sessions, CSRF tokens, login throttling."""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

N, R, P = 2 ** 14, 8, 1
MIN_PASSWORD = 10
SESSION_SECONDS = 12 * 3600


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def hash_password(password: str, salt: bytes | None = None) -> dict:
    salt = salt or os.urandom(16)
    h = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, dklen=32)
    return {"salt": _b64(salt), "hash": _b64(h), "n": N, "r": R, "p": P}


def verify_password(password: str, rec: dict) -> bool:
    try:
        h = hashlib.scrypt(password.encode(), salt=base64.b64decode(rec["salt"]), n=rec["n"], r=rec["r"], p=rec["p"], dklen=32)
        return hmac.compare_digest(h, base64.b64decode(rec["hash"]))
    except Exception:
        return False


class AuthStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def configured(self) -> bool:
        return self.path.exists()

    def set_password(self, password: str) -> None:
        if len(password) < MIN_PASSWORD:
            raise ValueError(f"password must be at least {MIN_PASSWORD} characters")
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(hash_password(password), f)
        os.replace(tmp, self.path)

    def check(self, password: str) -> bool:
        try:
            return verify_password(password, json.loads(self.path.read_text()))
        except Exception:
            return False


class Sessions:
    def __init__(self):
        self._s: dict[str, dict] = {}
        self._secret = secrets.token_bytes(32)

    def create(self) -> tuple[str, str]:
        sid = secrets.token_urlsafe(32)
        self._s[sid] = {"exp": time.time() + SESSION_SECONDS, "csrf": hmac.new(self._secret, sid.encode(), "sha256").hexdigest()}
        return sid, self._s[sid]["csrf"]

    def get(self, sid: str | None) -> dict | None:
        s = self._s.get(sid or "")
        if s and s["exp"] > time.time():
            return s
        self._s.pop(sid or "", None)
        return None

    def destroy(self, sid: str | None) -> None:
        self._s.pop(sid or "", None)


class Throttle:
    """At most `limit` failed logins per `window` seconds per client address."""

    def __init__(self, limit: int = 5, window: int = 300):
        self.limit, self.window, self._f = limit, window, {}

    def blocked(self, ip: str) -> bool:
        now = time.time()
        self._f[ip] = [t for t in self._f.get(ip, []) if now - t < self.window]
        return len(self._f[ip]) >= self.limit

    def fail(self, ip: str) -> None:
        self._f.setdefault(ip, []).append(time.time())

    def reset(self, ip: str) -> None:
        self._f.pop(ip, None)
