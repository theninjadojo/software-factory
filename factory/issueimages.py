"""Pictures in a GitHub issue (a screenshot pasted into the body or a comment), copied in for the agents.

The sandbox cannot reach GitHub, so the orchestrator fetches the pictures. It reads the issue as GitHub renders it (body_html:
in a private repository each picture carries a short-lived signed URL) and takes only <img src> addresses on GitHub's own image
hosts (ASSET_HOSTS, fixed in code). The fetch is the narrow one of design links: https on port 443 only, no credentials sent
(the signed URL is the credential), every redirect re-checked (at most 3), only public IP addresses, a size cap, and the file
must start with the signature of a png, jpg or gif. A picture that fails any check is skipped; it never fails a run or an
import. The pictures are untrusted: agents get them under factory-chosen names (issue-<n>.<ext>) in /task/attachments/."""
import hashlib
import http.client
import logging
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from . import designlinks, tracker

log = logging.getLogger("factory.issueimages")
# Where a picture in an issue may point, and the extra host GitHub may redirect it to. github.com only for uploaded assets.
ASSET_HOSTS = ("github.com", "private-user-images.githubusercontent.com", "user-images.githubusercontent.com")
REDIRECT_HOSTS = ASSET_HOSTS + ("github-production-user-asset-6210df.s3.amazonaws.com",)
ASSET_PATH = re.compile(r"/user-attachments/assets/[A-Za-z0-9-]+")
CTYPES = ("image/png", "image/jpeg", "image/gif", "application/octet-stream", "binary/octet-stream")
KINDS = ("png", "jpg", "gif")
MAX_REDIRECTS, MAX_URL = 3, 4000


def check_url(url: str, redirect: bool = False) -> str | None:
    """The URL without its fragment when it is https on port 443 with no credentials, on an image host (the S3 host only as a
    redirect target) and, on github.com, an uploaded asset; else None."""
    if not isinstance(url, str) or len(url) > MAX_URL or re.search(r"[\x00-\x20\x7f-\uffff\\]", url):
        return None
    try:
        p = urlsplit(url)
        port = p.port
    except ValueError:
        return None
    if p.scheme != "https" or p.username is not None or p.password is not None or port not in (None, 443):
        return None
    host = p.hostname or ""
    if host not in (REDIRECT_HOSTS if redirect else ASSET_HOSTS) or "/." in p.path:
        return None
    if host == "github.com" and not ASSET_PATH.fullmatch(p.path):
        return None
    return p._replace(fragment="").geturl()


class _Images(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.srcs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            self.srcs += [v.strip() for k, v in attrs if k == "src" and v]


def extract(html: str, max_images: int) -> list[str]:
    """At most max_images distinct acceptable picture URLs from rendered issue HTML, in order."""
    if not isinstance(html, str) or not html:
        return []
    p = _Images()
    p.feed(html)
    p.close()
    out: list[str] = []
    for src in p.srcs:
        u = check_url(src)
        if u and u not in out:
            out.append(u)
        if len(out) >= max_images:
            break
    return out


def kind(data: bytes) -> str | None:
    """png, jpg or gif from the file's own signature, else None."""
    return next((k for k in KINDS if data.startswith(tracker.ATTACH_MAGIC[k])), None)


def _get(url: str, max_bytes: int):
    return designlinks.http_get(url, max_bytes, accept="image/png, image/jpeg, image/gif")


def fetch(url: str, max_bytes: int, get=_get) -> tuple[str, str, bytes | None]:
    """(status, fixed detail, picture bytes). status: ok | refused | failed | too-large. Nothing from the remote side is put in detail."""
    seen, redirect = 0, False
    while True:
        checked = check_url(url, redirect)
        if checked is None:
            return "refused", "the picture is not on a GitHub image host", None
        url = checked
        try:
            status, headers, body = get(url, max_bytes)
        except ValueError:
            return "refused", "the host does not resolve to a public address", None
        except (OSError, http.client.HTTPException):
            return "failed", "the picture could not be fetched", None
        if status in (301, 302, 303, 307, 308):
            seen += 1
            loc = headers.get("location", "")
            if seen > MAX_REDIRECTS or not loc:
                return "failed", "too many redirects", None
            url, redirect = urljoin(url, loc), True
            continue
        if status != 200:
            return "failed", f"the picture answered HTTP {int(status)}", None
        if len(body) > max_bytes:
            return "too-large", "the picture is larger than the limit", None
        if headers.get("content-type", "").split(";")[0].strip().lower() not in CTYPES:
            return "refused", "the link is not a picture", None
        if kind(body) is None:
            return "refused", "the file is not a png, jpg or gif picture", None
        return "ok", "", body


def read(gh, repo: str, num: int, max_images: int) -> list[str]:
    """The picture URLs of a GitHub issue, body first, then its comments. GitHub errors are raised."""
    urls: list[str] = []
    for html in [gh.issue_html(repo, num)] + [h for _, h in gh.comments_html(repo, num)]:
        urls += [u for u in extract(html, max_images) if u not in urls]
    return urls[:max_images]


def download(urls: list[str], max_bytes: int, get=_get) -> tuple[list[tuple[str, bytes]], int]:
    """([(issue-<n>.<ext>, bytes)], how many could not be read). A picture seen twice is kept once."""
    items: list[tuple[str, bytes]] = []
    seen, missed = set(), 0
    for url in urls:
        status, detail, data = fetch(url, max_bytes, get)
        if data is None:
            log.info("an issue picture was skipped: %s", detail)
            missed += 1
            continue
        h = hashlib.sha256(data).hexdigest()
        if h in seen:
            continue
        name = f"issue-{len(items) + 1}.{kind(data)}"
        try:
            tracker.vet_attachment(name, data, max_bytes)
        except ValueError:
            missed += 1
            continue
        seen.add(h)
        items.append((name, data))
    return items, missed


def collect(cfg, gh, repo: str, num: int, get=_get) -> tuple[list[tuple[str, bytes]], int]:
    """The pictures of GitHub issue repo#num and how many could not be read; ([], 0) when off or when GitHub cannot be read."""
    ic = cfg.issue_images
    if not ic.enabled:
        return [], 0
    try:
        urls = read(gh, repo, num, ic.max_images)
    except Exception as e:
        log.warning("could not read the pictures of %s#%s (%s)", repo, num, type(e).__name__)
        return [], 0
    if not urls:
        return [], 0
    items, missed = download(urls, ic.max_mb * 1024 * 1024, get)
    log.info("issue pictures of %s#%s: %d copied, %d could not be read", repo, num, len(items), missed)
    return items, missed
