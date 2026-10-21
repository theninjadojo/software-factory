"""Google Sheets client for syncing a project's tickets and milestone plan with a spreadsheet (ticket L-71).

This is the client only: it signs in as a Google service account and reads and writes cell values. The sync itself, its tables and
the settings card come later. Nothing here runs unless a sheet is configured.

The factory signs the service account's RS256 token with the `openssl` program (the standard library cannot), and hands it the
private key through a pipe as /dev/fd/N, so the key never touches disk or a command line. Requests go only to the two Google hosts
below, never follow a redirect, and are size-capped. The key file's own `token_uri` is ignored, so a crafted key file cannot send
the signed assertion elsewhere. Error messages are fixed texts: no key material, no openssl output, no response bodies. Every
value is written with valueInputOption=RAW, so nothing the factory writes is ever evaluated as a formula. See SECURITY.md."""
import base64
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://sheets.googleapis.com/v4/spreadsheets/"
HOSTS = frozenset({"oauth2.googleapis.com", "sheets.googleapis.com"})
SCOPE = "https://www.googleapis.com/auth/spreadsheets"
MAX_BYTES = 8_000_000
KEY_MAX = 20_000                                 # a service-account key file is about 2.3 KB
PEM_MAX = 8_000                                  # well under the smallest pipe buffer (16 KB on macOS), so the write never blocks
SHEET_ID = re.compile(r"[A-Za-z0-9_-]{20,100}")
LINK = re.compile(r"https://docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]{20,100})(?:[/?#].*)?")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}")
PEM_HEAD, PEM_TAIL = "-----BEGIN PRIVATE KEY-----", "-----END PRIVATE KEY-----"
NO_OPENSSL = "The openssl program is missing on the server."

_tokens: dict[str, tuple[str, float]] = {}       # key file path -> (access token, expiry); in memory only, never stored


class SheetsError(RuntimeError):
    """A failure with a fixed, safe message (shown on the settings card and recorded with the sync)."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def parse_link(link: str) -> str | None:
    """The spreadsheet id from a sheet link (or a bare id); None if it is neither."""
    s = str(link or "").strip()
    if SHEET_ID.fullmatch(s):
        return s
    m = LINK.fullmatch(s)
    return m[1] if m else None


def load_key(path: str) -> dict:
    """The service-account key file, checked. Returns only client_email and private_key."""
    try:
        p = Path(path)
        if p.stat().st_size > KEY_MAX:
            raise SheetsError("The key file is too large to be a service-account key.")
        raw = json.loads(p.read_text())
    except SheetsError:
        raise
    except FileNotFoundError:
        raise SheetsError("The key file was not found.") from None
    except (OSError, UnicodeDecodeError):
        raise SheetsError("The key file could not be read.") from None
    except ValueError:
        raise SheetsError("The key file is not valid JSON.") from None
    if not isinstance(raw, dict) or raw.get("type") != "service_account":
        raise SheetsError("The key file is not a Google service-account key.")
    email, pem = raw.get("client_email"), raw.get("private_key")
    if not isinstance(email, str) or not EMAIL.fullmatch(email):
        raise SheetsError("The key file has no valid client_email.")
    if not isinstance(pem, str) or len(pem) > PEM_MAX or not pem.strip().startswith(PEM_HEAD) or not pem.strip().endswith(PEM_TAIL):
        raise SheetsError("The key file has no valid private_key.")
    return {"client_email": email, "private_key": pem}


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def signing_input(email: str, now: float) -> bytes:
    """header.claims of the JWT the token endpoint expects. `aud` is always TOKEN_URL, whatever the key file says."""
    header = {"alg": "RS256", "typ": "JWT"}
    iat = int(now)
    claims = {"iss": email, "scope": SCOPE, "aud": TOKEN_URL, "iat": iat, "exp": iat + 3600}
    enc = lambda d: _b64(json.dumps(d, separators=(",", ":")).encode())
    return f"{enc(header)}.{enc(claims)}".encode()


def sign(pem: str, data: bytes, run=subprocess.run, which=shutil.which) -> bytes:
    """RS256 signature of `data` by openssl. The key goes through a pipe; only its file-descriptor path is on the command line."""
    if not which("openssl"):
        raise SheetsError(NO_OPENSSL)
    r, w = os.pipe()
    try:
        try:
            os.write(w, pem.encode())
        finally:
            os.close(w)                          # closed before openssl starts, so it reads to the end and never waits on us
        try:
            out = run(["openssl", "dgst", "-sha256", "-sign", f"/dev/fd/{r}"], input=data, pass_fds=(r,),
                      capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            raise SheetsError("openssl could not be run.") from None
    finally:
        os.close(r)
    if out.returncode != 0 or not out.stdout:    # stderr can quote the key; it is never recorded
        raise SheetsError(f"openssl could not sign (exit {out.returncode}).")
    return out.stdout


def assertion(key: dict, now: float, run=subprocess.run, which=shutil.which) -> str:
    data = signing_input(key["client_email"], now)
    return data.decode() + "." + _b64(sign(key["private_key"], data, run, which))


class Client:
    """One service account. `opener` and `run` are seams for tests; the default opener refuses redirects."""

    def __init__(self, key_file: str, opener=None, run=subprocess.run, which=shutil.which, clock=time.time):
        self.key_file, self.run, self.which, self.clock = str(key_file), run, which, clock
        self.opener = opener or urllib.request.build_opener(_NoRedirect)

    def _call(self, method: str, url: str, body: dict | None = None, form: dict | None = None, auth: bool = True):
        u = urllib.parse.urlsplit(url)
        if u.scheme != "https" or u.hostname not in HOSTS or u.port not in (None, 443) or u.username or u.password:
            raise SheetsError("Refused a request to a host other than Google's.")
        headers = {"Accept": "application/json", "User-Agent": "software-factory"}
        data = None
        if form is not None:
            data, headers["Content-Type"] = urllib.parse.urlencode(form).encode(), "application/x-www-form-urlencoded"
        elif body is not None:
            data, headers["Content-Type"] = json.dumps(body).encode(), "application/json"
        if auth:
            headers["Authorization"] = "Bearer " + self.token()
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with self.opener.open(req, timeout=30) as r:
                raw = r.read(MAX_BYTES + 1)
        except urllib.error.HTTPError as e:
            raise SheetsError(_http_message(e.code, u.hostname)) from None
        except (urllib.error.URLError, OSError):
            raise SheetsError(f"Could not reach {u.hostname}.") from None
        if len(raw) > MAX_BYTES:
            raise SheetsError("Google's answer was too large.")
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            raise SheetsError("Google's answer was not valid JSON.") from None

    def token(self) -> str:
        """An access token for the spreadsheets scope, cached in memory until a minute before it expires."""
        now = self.clock()
        hit = _tokens.get(self.key_file)
        if hit and now < hit[1]:
            return hit[0]
        key = load_key(self.key_file)
        jwt = assertion(key, now, self.run, self.which)
        out = self._call("POST", TOKEN_URL, form={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": jwt},
                         auth=False)
        tok, life = out.get("access_token"), out.get("expires_in", 3600)
        if not isinstance(tok, str) or not tok or not isinstance(life, int):
            raise SheetsError("Google did not return an access token.")
        _tokens[self.key_file] = (tok, now + max(0, life - 60))
        return tok

    def _url(self, sid: str, tail: str = "", **q) -> str:
        if not SHEET_ID.fullmatch(sid or ""):
            raise SheetsError("That is not a Google Sheets id.")
        query = urllib.parse.urlencode(q, doseq=True)
        return API + sid + tail + ("?" + query if query else "")

    def metadata(self, sid: str) -> dict:
        return self._call("GET", self._url(sid, fields="properties.title,sheets.properties.title"))

    def batch_get(self, sid: str, ranges: list[str]) -> list[list[list]]:
        """The values of each range, in order (a range with no values gives [])."""
        out = self._call("GET", self._url(sid, "/values:batchGet", ranges=list(ranges)))
        got = out.get("valueRanges") or []
        return [(vr.get("values") or []) if isinstance(vr, dict) else [] for vr in got]

    def batch_update(self, sid: str, data: list[dict]) -> dict:
        """Write each {range, values}. RAW: a value that looks like a formula is stored as text."""
        return self._call("POST", self._url(sid, "/values:batchUpdate"),
                          body={"valueInputOption": "RAW", "data": [{"range": d["range"], "values": d["values"]} for d in data]})

    def append(self, sid: str, rng: str, rows: list[list]) -> dict:
        return self._call("POST", self._url(sid, "/values/" + urllib.parse.quote(rng, safe="") + ":append",
                                            valueInputOption="RAW", insertDataOption="INSERT_ROWS"), body={"values": rows})


def _http_message(code: int, host: str | None) -> str:
    if host == "oauth2.googleapis.com" and code in (400, 401):
        return "Google refused the service-account key (it may have been deleted or disabled)."
    if code == 403:
        return "The service account has no access to this sheet. Share the sheet with its address."
    if code == 404:
        return "The sheet was not found. Check the link."
    if code == 429:
        return "Google's rate limit was reached; the sync will try again later."
    if 300 <= code < 400:
        return "Google answered with a redirect, which is refused."
    return f"Google answered HTTP {code}."


def check_access(link: str, key_file: str, client: Client | None = None) -> dict:
    """The link-time check the settings card runs: the key signs in and the sheet is readable. Returns the sheet's title,
    its tab names and the service-account address (to show whom to share with). Raises SheetsError with a fixed message."""
    sid = parse_link(link)
    if not sid:
        raise SheetsError("That is not a Google Sheets link.")
    email = load_key(key_file)["client_email"]
    meta = (client or Client(key_file)).metadata(sid)
    props = meta.get("properties") if isinstance(meta.get("properties"), dict) else {}
    tabs = [s["properties"]["title"] for s in meta.get("sheets") or []
            if isinstance(s, dict) and isinstance(s.get("properties"), dict) and isinstance(s["properties"].get("title"), str)]
    return {"id": sid, "title": str(props.get("title") or "")[:200], "tabs": [t[:100] for t in tabs[:50]], "email": email}
