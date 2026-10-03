"""The worker API: how verification workers pull jobs and return results (docs/workers.md).

A separate process from the orchestrator and the admin UI; it shares only the SQLite file. Workers authenticate with a bearer token
(`name token` lines in workers.tokens_file); a worker can only touch jobs it claimed. Everything in a request body is untrusted and
is validated in jobs.py. Standard library only. Loopback by default: put TLS or a VPN / SSH tunnel in front of it for a remote worker."""
import argparse
import hmac
import json
import logging
import os
import re
import secrets
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import db as dbm
from . import jobs
from .config import WORKER_NAME, Config, load

log = logging.getLogger("factory.workerapi")
MAX_BODY = 64 * 1024
MAX_RESULT = 16 * 1024 * 1024
PROTOCOL = 1
JOB_PATH = re.compile(r"/v1/jobs/(\d{1,9})/(heartbeat|result)")
PLATFORM = re.compile(r"[a-z0-9][a-z0-9-]{0,40}")


def read_tokens(path: str) -> dict[str, str]:
    """{name: token} from `name token` lines. Bad lines are skipped; a missing file means nobody can authenticate."""
    out: dict[str, str] = {}
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        parts = line.split()
        if len(parts) == 2 and not line.lstrip().startswith("#") and WORKER_NAME.fullmatch(parts[0]) and len(parts[1]) >= 24:
            out[parts[0]] = parts[1]
    return out


def add_token(path: str, name: str) -> str:
    """Create a token for worker `name`: appended to `path` (mode 0600), returned once. Raises ValueError for a bad or taken name; never overwrites."""
    if not WORKER_NAME.fullmatch(name):
        raise ValueError("a worker name is lowercase letters, digits and dashes (at most 41 characters)")
    if name in read_tokens(path):
        raise ValueError(f"a worker called {name} already exists; remove its line from {path} to replace it")
    token = secrets.token_urlsafe(32)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a") as f:
        f.write(f"{name} {token}\n")
    return token


def authenticate(header: str | None, tokens: dict[str, str]) -> str | None:
    """The worker name for a `Bearer <token>` header, comparing every token in constant time. None if it matches no worker."""
    if not header or not header.startswith("Bearer "):
        return None
    given, found = header[7:].strip().encode(), None
    for name, tok in tokens.items():
        if hmac.compare_digest(given, tok.encode()):
            found = name
    return found


class Api:
    def __init__(self, cfg_loader, clock=time.time):
        self.cfg_loader, self.clock = cfg_loader, clock

    def handle(self, method: str, path: str, auth: str | None, body: bytes) -> tuple[int, dict | None]:
        """(status, JSON reply) for one request. No sockets here, so it is tested directly."""
        cfg: Config = self.cfg_loader()
        w = cfg.workers
        if not w.enabled:
            return 404, {"error": "workers are not enabled"}
        worker = authenticate(auth, read_tokens(w.tokens_file))
        if worker is None:
            return 401, {"error": "unauthorized"}
        if method != "POST":
            return 405, {"error": "POST only"}
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            return 400, {"error": "invalid JSON"}
        if not isinstance(data, dict):
            return 400, {"error": "the body must be a JSON object"}
        db, now = dbm.local(cfg.db_path), self.clock()
        if path == "/v1/ping":                          # lets a worker's `--check` prove the URL and token without claiming anything
            return 200, {"ok": True, "protocol": PROTOCOL, "worker": worker}
        if path == "/v1/claim":
            platform, recipes = data.get("platform"), data.get("recipes")
            if (not isinstance(platform, str) or not PLATFORM.fullmatch(platform) or not isinstance(recipes, list)
                    or len(recipes) > 50 or not all(isinstance(r, str) and jobs.NAME.fullmatch(r) for r in recipes)):
                return 400, {"error": "platform and recipes are required"}
            if data.get("version", PROTOCOL) != PROTOCOL:
                return 400, {"error": f"this server speaks protocol {PROTOCOL}"}
            job = jobs.claim(db, worker, platform, recipes, now, w.lease_seconds, w.claim_wait_seconds, w.max_attempts)
            if not job:
                return 204, None
            return 200, {"id": job["id"], "repo": job["repo"], "base_sha": job["base_sha"], "patch": job["patch"],
                         "recipe": job["recipe"], "lease_seconds": w.lease_seconds}
        m = JOB_PATH.fullmatch(path)
        if not m:
            return 404, {"error": "not found"}
        jid, what = int(m.group(1)), m.group(2)
        if what == "heartbeat":
            state = jobs.heartbeat(db, jid, worker, now)
            return (200, {"cancel": state == "cancel"}) if state != "gone" else (404, {"error": "not your job"})
        why = jobs.complete(db, jid, worker, data, now)
        if why:
            log.warning("worker %s sent a refused result for job %s: %s", worker, jid, why)
            return 400, {"error": why}
        return 200, {"ok": True}


def make_handler(api: Api):
    class Handler(BaseHTTPRequestHandler):
        server_version, sys_version, timeout = "factory-workers", "", 30

        def log_message(self, fmt, *args):
            log.info("%s %s", self.address_string(), fmt % args)

        def _go(self):
            try:
                n = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                n = -1
            limit = MAX_RESULT if self.path.endswith("/result") else MAX_BODY
            if not 0 <= n <= limit:
                self._reply(413, {"error": "body too large"})
                return
            try:
                status, out = api.handle(self.command, self.path, self.headers.get("Authorization"), self.rfile.read(n) if n else b"")
            except Exception:
                log.exception("worker API error")
                status, out = 500, {"error": "internal error"}
            self._reply(status, out)

        def _reply(self, status: int, out: dict | None):
            data = json.dumps(out).encode() if out is not None else b""
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        do_GET = do_POST = do_PUT = do_DELETE = _go

    return Handler


def main() -> None:
    ap = argparse.ArgumentParser(description="software-factory worker API")
    ap.add_argument("--config", required=True)
    ap.add_argument("--listen", help="override workers.listen, e.g. 127.0.0.1:8788")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    cfg = load(args.config)
    if not cfg.workers.enabled:
        sys.exit("workers are not enabled: set [workers] enabled = true")
    host, _, port = (args.listen or cfg.workers.listen).rpartition(":")
    dbm.connect(cfg.db_path).close()            # make sure the tables exist
    srv = ThreadingHTTPServer((host, int(port)), make_handler(Api(lambda: load(args.config))))
    log.info("worker API on %s:%s", host, port)
    srv.serve_forever()


if __name__ == "__main__":
    main()
