"""Plan usage for the Claude subscription the agents share: how much of the 5-hour session and the 7-day week is used.
The orchestrator polls Anthropic's OAuth usage endpoint (api.anthropic.com only, with the subscription token from claude.env)
at most every REFRESH_SECONDS and stores the result in the status table as JSON under STATUS_KEY. The UI and Telegram only
read that row, so they never see the token. The endpoint is not a documented API: any failure is stored as an error and shown
as "unavailable", never raised. An API key (ANTHROPIC_API_KEY) has no plan limits, so nothing is fetched for one."""
import hashlib
import json
import logging
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from . import db as dbm

log = logging.getLogger("factory.usage")
STATUS_KEY = "claude_usage"
URL = "https://api.anthropic.com/api/oauth/usage"
REFRESH_SECONDS = 300
REFUSED_RETRY = 3600                # after a 401/403: this token cannot read the usage, so ask again hourly (or once the token changes)
LIMITED_RETRY = 900                 # after a 429 without a usable Retry-After
LOW_PERCENT = 90                    # warn when a window is this used
WINDOWS = (("five_hour", "session"), ("seven_day", "week"))


def read_oauth_token(env_file: str) -> str | None:
    try:
        for line in Path(env_file).read_text().splitlines():
            k, _, v = line.partition("=")
            if k.strip() == "CLAUDE_CODE_OAUTH_TOKEN" and v.strip():
                return v.strip()
    except OSError:
        pass
    return None


def parse(body: dict, now: float) -> dict:
    """{"fetched": ts, "session": {"used": pct, "resets": ts|None}, "week": {...}}; a window the response lacks is left out."""
    out: dict = {"fetched": now}
    for key, name in WINDOWS:
        w = body.get(key)
        if not isinstance(w, dict) or type(w.get("utilization")) not in (int, float):
            continue
        resets = None
        if isinstance(w.get("resets_at"), str):
            try:
                resets = datetime.fromisoformat(w["resets_at"].replace("Z", "+00:00")).timestamp()
            except ValueError:
                pass
        out[name] = {"used": max(0.0, min(100.0, float(w["utilization"]))), "resets": resets}
    return out


def fetch(token: str, now: float) -> dict:
    req = urllib.request.Request(URL, headers={"Authorization": f"Bearer {token}", "anthropic-beta": "oauth-2025-04-20",
                                               "Accept": "application/json", "User-Agent": "software-factory"})
    with urllib.request.urlopen(req, timeout=15) as r:
        body = json.loads(r.read(100_000))
    if not isinstance(body, dict):
        raise ValueError("unexpected response")
    return parse(body, now)


def _token_id(token: str) -> str:
    """A short fingerprint of the token, so a new token is tried at once after the old one was refused (never the token itself)."""
    return hashlib.sha256(token.encode()).hexdigest()[:12]


def _line(text, limit: int = 200) -> str:
    """Anthropic's error text as one short printable line for the log and the status (it is shown in the UI and Telegram)."""
    text = "".join(c for c in " ".join(str(text).split()) if c.isprintable())     # line breaks become spaces, then controls go
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _anthropic_says(e: urllib.error.HTTPError) -> str:
    """'permission_error: OAuth token does not meet scope requirement user:profile' from an error body, or ""."""
    try:
        body = json.loads(e.read(4000) or b"{}")
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            kind, msg = err.get("type"), err.get("message")
            return _line(": ".join(str(x) for x in (kind, msg) if isinstance(x, str) and x))
    except Exception:                                       # no body, not JSON: the status code says enough
        pass
    return ""


def _retry_after(e: urllib.error.HTTPError) -> int:
    try:
        return max(60, min(3600, int(e.headers.get("Retry-After", ""))))
    except (TypeError, ValueError):
        return LIMITED_RETRY


def describe(e: Exception) -> tuple[str, str, int]:
    """(short error, what it means for a person, seconds until the next try) for a failed fetch."""
    if isinstance(e, urllib.error.HTTPError):
        code = f"HTTP {e.code}"
        if e.code == 401:
            return code, ("The subscription token was refused (401): it may have expired. Make a new one with \"claude setup-token\" "
                          "and save it under Credentials."), REFUSED_RETRY
        if e.code == 403:
            return code, ("This subscription token cannot read plan usage (403). Tokens made with \"claude setup-token\" usually lack "
                          "that permission; agent runs are not affected."), REFUSED_RETRY
        if e.code == 429:
            return code, "Anthropic is limiting how often plan usage can be read (429). The factory tries again later.", _retry_after(e)
        if e.code >= 500:
            return code, f"Anthropic's usage service failed ({e.code}). The factory tries again in a few minutes.", REFRESH_SECONDS
        return code, f"Anthropic answered {e.code} to the usage request.", REFRESH_SECONDS
    if isinstance(e, (urllib.error.URLError, OSError)):
        return "unreachable", "api.anthropic.com could not be reached for plan usage. The factory tries again in a few minutes.", REFRESH_SECONDS
    return "bad response", "The usage response was not what the factory expects (the endpoint may have changed).", REFRESH_SECONDS


def refresh(conn, env_file: str, now: float | None = None, force: bool = False) -> dict | None:
    """Fetch and store the usage unless it was fetched recently. Returns the stored dict (with "error" and "reason" on failure).
    A refusal (401/403) or a rate limit (429) waits longer before the next try, unless the token in claude.env changes. The log
    says what went wrong once, when it starts or changes, and once when it works again; not on every try."""
    now = now or time.time()
    prev = load(conn)
    token = read_oauth_token(env_file)
    if not token:
        return None
    tid = _token_id(token)
    if prev and not force and prev.get("token") == tid:
        wait = prev.get("retry_at") or (prev.get("fetched", 0) + REFRESH_SECONDS)
        if now < wait:
            return prev
    try:
        cur = fetch(token, now)
        if len(cur) == 1:
            raise ValueError("no usage windows in the response")
        if prev and prev.get("error"):
            log.info("usage fetch works again")
    except Exception as e:                                  # network, 401/403 (token lacks the scope), 429, format change
        err, reason, wait = describe(e)
        said = _anthropic_says(e) if isinstance(e, urllib.error.HTTPError) else ""
        cur = {"fetched": now, "error": err, "reason": reason, "retry_at": now + wait, **({"detail": said} if said else {})}
        if not prev or (prev.get("error"), prev.get("detail"), prev.get("token")) != (err, said or None, tid):
            log.warning("usage fetch failed: %s%s; next try in %d min", err, f" {said}" if said else "", wait // 60)
        for _, name in WINDOWS:                             # keep the last known numbers, marked stale by "error"
            if prev and name in prev:
                cur[name] = prev[name]
    cur["token"] = tid
    dbm.set_status(conn, STATUS_KEY, json.dumps(cur))
    return cur


def load(conn) -> dict | None:
    try:
        row = dbm.get_status(conn).get(STATUS_KEY)
        return json.loads(row["value"]) if row else None
    except (ValueError, TypeError, KeyError):
        return None


def _left(resets, now: float) -> str:
    if not resets:
        return ""
    s = max(0, int(resets - now))
    d, h, m = s // 86400, s % 86400 // 3600, s % 3600 // 60
    return f" (resets in {d}d {h}h)" if d else f" (resets in {h}h {m}m)" if h else f" (resets in {m}m)"


def summary(u: dict | None, now: float | None = None) -> str:
    """One line per window: 'Session: 37% used, 63% left (resets in 2h 10m)'."""
    now = now or time.time()
    if not u:
        return "Claude usage: not available (needs a subscription token)"
    lines = [f"{label.capitalize()}: {w['used']:.0f}% used, {100 - w['used']:.0f}% left{_left(w.get('resets'), now)}"
             for _, label in WINDOWS if (w := u.get(label))]
    if u.get("error"):
        why = u.get("reason") or u["error"]
        lines.append(f"(could not refresh: {why})" if lines else f"Claude usage unavailable: {why}")
    return "\n".join(lines) or "Claude usage: no data"


def over_limit(u: dict | None) -> list[str]:
    """Names of the windows at or past LOW_PERCENT."""
    return [label for _, label in WINDOWS if u and (w := u.get(label)) and w["used"] >= LOW_PERCENT]
