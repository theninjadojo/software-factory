"""The files of a published release that a verification worker needs to update itself, fetched with the factory's own GitHub token.

A worker has no credentials for a private repository and must not be given any: it asks the worker API, which reads the release's assets
here and hands them over. Only the four worker files of the release the factory itself runs are served, each checked (the VERSION file must
name the tag, the archive must be a gzip tarball) and cached on disk, so ten workers cost one download."""
import json
import logging
import re
import urllib.error
import urllib.request
from pathlib import Path

log = logging.getLogger("factory.releasefiles")
FILES = ("VERSION", "shikumi-worker.tar.gz", "setup-worker.sh", "update-worker.sh")
TAG = re.compile(r"v\d{1,4}\.\d{1,4}\.\d{1,4}")
MAX_BYTES = 30_000_000


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _get(url: str, headers: dict, opener=None) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "shikumi", **headers})
    with (opener or urllib.request.build_opener()).open(req, timeout=60) as r:
        return r.read(MAX_BYTES + 1)


def download(repo: str, tag: str, name: str, token: str, opener=None) -> bytes:
    """One asset of release `tag`. GitHub answers an asset request with a redirect to a signed storage URL, which must be fetched WITHOUT the
    token (the storage service refuses a request that carries one), so the redirect is followed by hand."""
    auth = {"Authorization": f"Bearer {token}"} if token else {}
    api = f"https://api.github.com/repos/{repo}"
    rel = json.loads(_get(f"{api}/releases/tags/{tag}", {"Accept": "application/vnd.github+json", **auth}, opener))
    asset = next((a for a in rel.get("assets", []) if a.get("name") == name), None)
    if not asset or not isinstance(asset.get("id"), int):
        raise FileNotFoundError(f"release {tag} has no {name}")
    try:
        return _get(f"{api}/releases/assets/{asset['id']}", {"Accept": "application/octet-stream", **auth}, urllib.request.build_opener(_NoRedirect))
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location") if e.code in (301, 302, 303, 307, 308) else None
        if not loc or not loc.startswith("https://"):
            raise
        return _get(loc, {}, opener)


def get(state_dir, repo: str, tag: str, name: str, token: str, fetch=download) -> bytes | None:
    """The bytes of a worker file of release `tag`, from the cache or GitHub; None when it cannot be had or does not check out."""
    if not TAG.fullmatch(tag or "") or name not in FILES:
        return None
    path = Path(state_dir) / "worker-release" / tag / name
    try:
        if path.is_file():
            return path.read_bytes()
        data = fetch(repo, tag, name, token)
    except (OSError, ValueError, urllib.error.URLError):
        log.warning("could not fetch %s of %s", name, tag)
        return None
    if not data or len(data) > MAX_BYTES:
        return None
    if name == "VERSION" and data.decode(errors="replace").strip() != tag[1:]:
        return None
    if name == "shikumi-worker.tar.gz" and not data.startswith(b"\x1f\x8b"):
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    except OSError:
        pass                                        # served anyway; the next worker fetches it again
    return data
