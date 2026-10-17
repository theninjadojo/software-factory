"""Is a newer release available? One cached, unauthenticated read of the latest GitHub release of the project (no token, nothing
sent but the request). It only informs: installing is `scripts/update.sh`, run by a person on the host.

Versions are X.Y.Z, or X.Y.Z-beta.N for a beta (a GitHub prerelease), which sorts below X.Y.Z. An install sees betas only on the beta
channel (state/UPDATE_CHANNEL says "beta", set on the Updates page; the update scripts read the same file)."""
import json
import logging
import re
import time
import urllib.request
from pathlib import Path

log = logging.getLogger("factory.updates")
TAG = re.compile(r"^v?(\d{1,4})\.(\d{1,4})\.(\d{1,4})(?:-beta\.(\d{1,4}))?$")
CHANNEL_FILE = "UPDATE_CHANNEL"
CHANNELS = ("stable", "beta")
REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
CACHE_SECONDS = 6 * 3600


def token_for(cfg) -> str:
    """The token used to read a private repository's releases: [updates] token_file, else the [github] token ('' if neither is readable)."""
    tf = cfg.updates.token_file or cfg.token_file
    try:
        return Path(tf).read_text().strip() if tf and Path(tf).is_file() else ""
    except OSError:
        return ""


def parse(v: str) -> tuple[int, int, int, int, int] | None:
    """A sort key: (X, Y, Z, 1, 0) for a release, (X, Y, Z, 0, N) for its beta N, so every beta of X.Y.Z sorts below X.Y.Z."""
    m = TAG.match((v or "").strip())
    if not m:
        return None
    a, b, c, n = m.groups()
    return (int(a), int(b), int(c), 0, int(n)) if n is not None else (int(a), int(b), int(c), 1, 0)


def is_beta(v: str) -> bool:
    k = parse(v)
    return bool(k and k[3] == 0)


def newer(latest: str, have: str) -> bool:
    """True when `latest` is a higher release than `have`. A development build ('dev') never nags."""
    a, b = parse(latest), parse(have)
    return bool(a and b and a > b)


def channel(state_dir) -> str:
    """'beta' when this install takes betas, else 'stable'."""
    try:
        return "beta" if (Path(state_dir) / CHANNEL_FILE).read_text().strip() == "beta" else "stable"
    except OSError:
        return "stable"


def set_channel(state_dir, ch: str) -> bool:
    """Switch the channel and forget the cached check (it was for the other channel). False for an unknown channel."""
    if ch not in CHANNELS:
        return False
    p = Path(state_dir) / CHANNEL_FILE
    if ch == "beta":
        p.write_text("beta\n")
    else:
        p.unlink(missing_ok=True)
    (Path(state_dir) / "update_check.json").unlink(missing_ok=True)
    return True


def _release(data) -> dict | None:
    tag, url = str(data.get("tag_name", "")), str(data.get("html_url", ""))
    return {"tag": tag, "url": url} if parse(tag) and url.startswith("https://github.com/") else None


def fetch_latest(repo: str, opener=urllib.request.urlopen, token: str = "", channel: str = "stable") -> dict | None:
    """{tag, url} of the latest published release, or None. On the beta channel: the highest release or beta, whichever is newer
    (GitHub's "latest" never is a prerelease, so that reads the list instead)."""
    if not REPO.match(repo or ""):
        return None
    path = "releases?per_page=30" if channel == "beta" else "releases/latest"
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/{path}",
                                 headers={"Accept": "application/vnd.github+json", "User-Agent": "shikumi"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with opener(req, timeout=10) as r:
            data = json.loads(r.read(2_000_000 if channel == "beta" else 200_000))
    except Exception:
        log.info("could not check for a new release")
        return None
    if channel != "beta":
        got = _release(data) if isinstance(data, dict) else None
        return None if got and is_beta(got["tag"]) else got
    found = [r for r in (_release(d) for d in data if isinstance(d, dict) and not d.get("draft")) if r] if isinstance(data, list) else []
    return max(found, key=lambda r: parse(r["tag"]), default=None)


def _read(state_dir: Path) -> dict:
    try:
        return json.loads((Path(state_dir) / "update_check.json").read_text())
    except (OSError, ValueError):
        return {}


def due(state_dir: Path, now=time.time) -> bool:
    return now() - float(_read(state_dir).get("checked", 0) or 0) > CACHE_SECONDS


def refresh(state_dir: Path, repo: str, now=time.time, fetch=fetch_latest, token: str = "") -> None:
    """Ask GitHub and remember the answer (a failed check is remembered too, so it is not retried on every page view)."""
    kw = {**({"token": token} if token else {}), **({"channel": "beta"} if channel(state_dir) == "beta" else {})}
    try:
        (Path(state_dir) / "update_check.json").write_text(json.dumps({"checked": now(), "latest": fetch(repo, **kw)}))
    except OSError:
        pass


def available(state_dir: Path, have: str) -> dict | None:
    """The cached latest release when it is newer than `have` (reads the cache only: no network)."""
    latest = _read(state_dir).get("latest")
    return latest if isinstance(latest, dict) and newer(str(latest.get("tag", "")), have) else None
