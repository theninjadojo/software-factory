"""Is a newer release available? One cached, unauthenticated read of the latest GitHub release of the project (no token, nothing
sent but the request). It only informs: installing is `scripts/update.sh`, run by a person on the host."""
import json
import logging
import re
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("factory.updates")
TAG = re.compile(r"^v?(\d{1,4})\.(\d{1,4})\.(\d{1,4})$")
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
CACHE_SECONDS = 6 * 3600


def token_for(cfg) -> str:
    """The token used to read a private repository's releases: [updates] token_file, else the [github] token ('' if neither is readable)."""
    tf = cfg.updates.token_file or cfg.token_file
    try:
        return Path(tf).read_text().strip() if tf and Path(tf).is_file() else ""
    except OSError:
        return ""


def parse(v: str) -> tuple[int, int, int] | None:
    m = TAG.match((v or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def newer(latest: str, have: str) -> bool:
    """True when `latest` is a higher release than `have`. A development build ('dev') never nags."""
    a, b = parse(latest), parse(have)
    return bool(a and b and a > b)


def fetch_latest(repo: str, opener=urllib.request.urlopen, token: str = "") -> dict | None:
    """{tag, url} of the latest published release, or None."""
    if not REPO.match(repo or ""):
        return None
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/latest",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "shikumi"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with opener(req, timeout=10) as r:
            data = json.loads(r.read(200_000))
    except Exception:
        log.info("could not check for a new release")
        return None
    tag, url = str(data.get("tag_name", "")), str(data.get("html_url", ""))
    return {"tag": tag, "url": url} if parse(tag) and url.startswith("https://github.com/") else None


def _read(state_dir: Path) -> dict:
    try:
        return json.loads((Path(state_dir) / "update_check.json").read_text())
    except (OSError, ValueError):
        return {}


def due(state_dir: Path, now=time.time) -> bool:
    return now() - float(_read(state_dir).get("checked", 0) or 0) > CACHE_SECONDS


def refresh(state_dir: Path, repo: str, now=time.time, fetch=fetch_latest, token: str = "") -> None:
    """Ask GitHub and remember the answer (a failed check is remembered too, so it is not retried on every page view)."""
    try:
        (Path(state_dir) / "update_check.json").write_text(json.dumps({"checked": now(), "latest": fetch(repo, token=token) if token else fetch(repo)}))
    except OSError:
        pass


def available(state_dir: Path, have: str) -> dict | None:
    """The cached latest release when it is newer than `have` (reads the cache only: no network)."""
    latest = _read(state_dir).get("latest")
    return latest if isinstance(latest, dict) and newer(str(latest.get("tag", "")), have) else None
