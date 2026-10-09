"""Design exports linked from a ticket (an external Claude Design page, as one self-contained HTML file).

A person puts a link in the issue body; the orchestrator (never the sandbox) fetches it, so the agents' network allowlist does not
change. The fetch is narrow: https on port 443 only, a host the admin listed in [design_links] hosts, no credentials sent, every
redirect re-checked against the same list, only public IP addresses (the connection goes to the address that was checked), a size
cap and a content-type check. The result is untrusted: it is stored for the UI (shown in a sandboxed tab, no scripts), rendered to
a PNG in the sealed container, and handed to the agents under factory-chosen names, as intent only. It is never committed to a repo
and never satisfies the mockup gate."""
import hashlib
import http.client
import ipaddress
import logging
import re
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit

from . import db as dbm
from . import render as render_mod

log = logging.getLogger("factory.designlinks")
URL_RE = re.compile(r"https://[^\s<>\"'`)\]]+")
HOST_RE = re.compile(r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+")
CTYPES = ("text/html", "text/plain")
MAX_REDIRECTS, TIMEOUT = 3, 20


def valid_host(h: str) -> bool:
    return isinstance(h, str) and h == h.lower() and bool(HOST_RE.fullmatch(h)) and len(h) <= 253


def check_url(url: str, hosts) -> str | None:
    """The URL without its fragment when it is https on port 443, has no credentials and its host is listed; else None."""
    if not isinstance(url, str) or len(url) > 2000 or re.search(r"[\x00-\x20\x7f-￿\\]", url):
        return None
    try:
        p = urlsplit(url)
        port = p.port
    except ValueError:
        return None
    if p.scheme != "https" or p.username is not None or p.password is not None or port not in (None, 443):
        return None
    host = (p.hostname or "")
    if not valid_host(host) or host not in {h.lower() for h in hosts}:
        return None
    return p._replace(fragment="").geturl()


def extract(body: str, hosts, max_links: int) -> list[str]:
    """At most max_links distinct acceptable URLs from an issue body, in order."""
    out: list[str] = []
    for m in URL_RE.finditer(body or ""):
        u = check_url(m.group(0).rstrip(".,;:!?"), hosts)
        if u and u not in out:
            out.append(u)
        if len(out) >= max_links:
            break
    return out


def public_ip(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%")[0])
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def resolve(host: str) -> str:
    """One address for host, only when every address it resolves to is public (else ValueError)."""
    addrs = [i[4][0] for i in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)]
    if not addrs or not all(public_ip(a) for a in addrs):
        raise ValueError("not a public address")
    return addrs[0]


class _Conn(http.client.HTTPSConnection):
    """Connects to the address that was checked (not a second lookup), and verifies the certificate for the host name."""
    def __init__(self, host: str, ip: str, timeout: float):
        super().__init__(host, 443, timeout=timeout, context=ssl.create_default_context())
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, 443), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def http_get(url: str, max_bytes: int, accept: str = "text/html"):
    """(status, headers, body) of one GET with no cookies or credentials; the body is read up to max_bytes + 1."""
    p = urlsplit(url)
    conn = _Conn(p.hostname, resolve(p.hostname), TIMEOUT)
    try:
        conn.request("GET", (p.path or "/") + (f"?{p.query}" if p.query else ""),
                     headers={"Host": p.hostname, "Accept": accept, "User-Agent": "software-factory", "Accept-Encoding": "identity"})
        r = conn.getresponse()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read(max_bytes + 1)
    finally:
        conn.close()


def fetch(url: str, hosts, max_bytes: int, get=http_get) -> tuple[str, str, bytes | None]:
    """(status, fixed detail, html bytes). status: ok | refused | failed | too-large. Nothing from the remote side is put in detail."""
    seen = 0
    while True:
        if check_url(url, hosts) is None:
            return "refused", "the link is not on an allowed host", None
        try:
            status, headers, body = get(url, max_bytes)
        except ValueError:
            return "refused", "the host does not resolve to a public address", None
        except (OSError, http.client.HTTPException):
            return "failed", "the link could not be fetched", None
        if status in (301, 302, 303, 307, 308):
            seen += 1
            loc = headers.get("location", "")
            if seen > MAX_REDIRECTS or not loc:
                return "failed", "too many redirects", None
            url = urljoin(url, loc)
            continue
        if status != 200:
            return "failed", f"the link answered HTTP {int(status)}", None
        if len(body) > max_bytes:
            return "too-large", "the file is larger than the limit", None
        if headers.get("content-type", "").split(";")[0].strip().lower() not in CTYPES:
            return "refused", "the link is not an HTML file", None
        if b"\x00" in body:
            return "refused", "the file is not text", None
        try:
            body.decode("utf-8")
        except UnicodeDecodeError:
            return "refused", "the file is not UTF-8 text", None
        return "ok", "", body


def refresh(db, cfg, rn, repo: str, num: int, body: str, get=http_get, render=render_mod.render, now=time.time) -> list[dict]:
    """Fetch (or reuse, while younger than ttl_hours) the ticket's links; returns the usable ones as {id, html, png}."""
    dl = cfg.design_links
    out = []
    for url in extract(body, dl.hosts, dl.max_links):
        h = hashlib.sha256(url.encode()).hexdigest()
        row = dbm.design_import_row(db, repo, num, h)
        if not row or (row["status"] == "ok" and not row["html"]) or now() - row["fetched"] > dl.ttl_hours * 3600:
            status, detail, html = fetch(url, dl.hosts, dl.max_bytes, get)
            png = None
            if html is not None:
                try:
                    png = render(rn, {f"import-{h[:12]}": html.decode()}).get(f"import-{h[:12]}")
                except Exception:
                    log.exception("could not render a linked design")
            dbm.add_design_import(db, repo, num, url, h, status, detail, html, png, hashlib.sha256(html or b"").hexdigest(), now())
            row = dbm.design_import_row(db, repo, num, h)
        if row and row["status"] == "ok":
            out.append({"id": row["id"], "html": row["html"], "png": row["png"]})
    return out
