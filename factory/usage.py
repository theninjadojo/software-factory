"""Plan usage for the Claude subscription the agents share: how much of the 5-hour session and the 7-day week is used.
The orchestrator polls Anthropic's OAuth usage endpoint (api.anthropic.com only, with the subscription token from claude.env)
at most every REFRESH_SECONDS and stores the result in the status table as JSON under STATUS_KEY. The UI and Telegram only
read that row, so they never see the token. The endpoint is not a documented API: any failure is stored as an error and shown
as "unavailable", never raised. An API key (ANTHROPIC_API_KEY) has no plan limits, so nothing is fetched for one."""
import json
import logging
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from . import db as dbm

log = logging.getLogger("factory.usage")
STATUS_KEY = "claude_usage"
URL = "https://api.anthropic.com/api/oauth/usage"
REFRESH_SECONDS = 300
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


def refresh(conn, env_file: str, now: float | None = None, force: bool = False) -> dict | None:
    """Fetch and store the usage unless it was fetched recently. Returns the stored dict (with "error" on failure)."""
    now = now or time.time()
    prev = load(conn)
    if prev and not force and now - prev.get("fetched", 0) < REFRESH_SECONDS:
        return prev
    token = read_oauth_token(env_file)
    if not token:
        return None
    try:
        cur = fetch(token, now)
        if len(cur) == 1:
            cur["error"] = "no usage windows in the response"
    except Exception as e:                                  # network, 401/403 (token lacks the scope), format change
        log.warning("usage fetch failed: %s", type(e).__name__)
        cur = {"fetched": now, "error": type(e).__name__ + (f" {e.code}" if hasattr(e, "code") else "")}
        for _, name in WINDOWS:                             # keep the last known numbers, marked stale by "error"
            if prev and name in prev:
                cur[name] = prev[name]
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
        lines.append(f"(could not refresh: {u['error']})" if lines else f"Claude usage unavailable: {u['error']}")
    return "\n".join(lines) or "Claude usage: no data"


def over_limit(u: dict | None) -> list[str]:
    """Names of the windows at or past LOW_PERCENT."""
    return [label for _, label in WINDOWS if u and (w := u.get(label)) and w["used"] >= LOW_PERCENT]
