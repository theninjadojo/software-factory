"""Credentials and the outbound checks the UI offers. Secrets are write-only: stored 0600, never rendered back, never logged.
Outbound calls go only to fixed hosts (api.github.com, api.telegram.org, slack.com, openrouter.ai, api.elevenlabs.io)."""
import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

from ..classifier import Classification, RuleClassifier
from ..config import Config
from ..jev import JevClassifier

SECRET_RE = re.compile(r"^\S{8,500}$")
CLAUDE_VARS = {"subscription": "CLAUDE_CODE_OAUTH_TOKEN", "apikey": "ANTHROPIC_API_KEY"}

# name -> (label, accessor of the file path in the config)
SECRETS = {
    "github": ("GitHub token", lambda c: c.token_file),
    "claude": ("Claude credential", lambda c: c.runner.claude_env_file),
    "openrouter": ("OpenRouter key (Jev)", lambda c: c.openrouter_key_file),
    "telegram": ("Telegram bot token", lambda c: c.telegram_token_file),
    "slack_bot": ("Slack bot token (xoxb-)", lambda c: c.slack_bot_token_file),
    "slack_app": ("Slack app-level token (xapp-)", lambda c: c.slack_app_token_file),
    "elevenlabs": ("ElevenLabs key (video voices)", lambda c: c.videos.elevenlabs_key_file),
}
SLACK_PREFIX = {"slack_bot": "xoxb-", "slack_app": "xapp-"}
# The Slack app the factory needs: a bot that may post and own /factory, interactivity for the buttons, Socket Mode (no public URL).
SLACK_MANIFEST = {
    "display_information": {"name": "Shikumi"},
    "features": {"bot_user": {"display_name": "Shikumi", "always_online": True},
                 "slash_commands": [{"command": "/factory", "description": "Control the factory", "usage_hint": "status | usage | pause | resume | help",
                                     "should_escape": False}]},
    "oauth_config": {"scopes": {"bot": ["chat:write", "commands"]}},
    "settings": {"interactivity": {"is_enabled": True}, "socket_mode_enabled": True, "org_deploy_enabled": False, "token_rotation_enabled": False},
}


def slack_create_url() -> str:
    """Slack's 'create an app from this manifest' link: the person picks a workspace and confirms; nothing is sent from here."""
    from urllib.parse import quote
    return "https://api.slack.com/apps?new_app=1&manifest_json=" + quote(json.dumps(SLACK_MANIFEST, separators=(",", ":")))


def secret_path(cfg: Config, name: str) -> Path | None:
    p = SECRETS[name][1](cfg)
    return Path(p) if p else None


def secret_status(cfg: Config, name: str) -> dict:
    """Whether a secret is set and when it changed. Never the value itself."""
    p = secret_path(cfg, name)
    if p is None:
        return {"configured": False, "set": False, "note": "no file is configured for this credential"}
    if not p.is_file() or p.stat().st_size == 0:
        return {"configured": True, "set": False, "path": str(p)}
    out = {"configured": True, "set": True, "path": str(p), "updated": p.stat().st_mtime}
    if name == "claude":                                   # which kind it is, from the variable NAME only
        out["kind"] = ", ".join(sorted({ln.split("=", 1)[0] for ln in p.read_text().splitlines() if "=" in ln}))
    return out


def write_secret(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def save_secret(cfg: Config, name: str, value: str, claude_kind: str = "subscription") -> None:
    if name not in SECRETS:
        raise ValueError("unknown credential")
    path = secret_path(cfg, name)
    if path is None:
        raise ValueError("no file is configured for this credential in config.toml")
    value = (value or "").strip()
    if not SECRET_RE.match(value):
        raise ValueError("that does not look like a token (8-500 characters, no spaces or line breaks)")
    if name in SLACK_PREFIX and not value.startswith(SLACK_PREFIX[name]):
        raise ValueError(f"that is not the right token: this one starts with {SLACK_PREFIX[name]}")
    if name == "claude":
        if claude_kind not in CLAUDE_VARS:
            raise ValueError("choose subscription token or API key")
        write_secret(path, f"{CLAUDE_VARS[claude_kind]}={value}\n")   # exactly one variable: an API key would silently win otherwise
    else:
        write_secret(path, value)


def read_secret(cfg: Config, name: str) -> str | None:
    p = secret_path(cfg, name)
    return p.read_text().strip() if p and p.is_file() and p.stat().st_size else None


# ---------------------------------------------------------------- outbound checks
def _scrub(text: str, *secrets: str) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "[token]")
    return text


def _http(url: str, token: str | None = None, data: dict | None = None, timeout: int = 15, bearer: bool = True) -> tuple[int, dict]:
    headers = {"User-Agent": "shikumi-ui", "Accept": "application/json"}
    if token and bearer:
        headers["Authorization"] = f"Bearer {token}"
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read() or b"{}")
        except ValueError:
            body = {}
        return e.code, body
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 0, {"error": _scrub(str(getattr(e, "reason", e)), token or "")}


def check_github(token: str, repos: list[str]) -> dict:
    status, user = _http("https://api.github.com/user", token)
    if status != 200:
        return {"ok": False, "message": f"GitHub rejected the token (HTTP {status})."}
    access = {}
    for r in repos[:30]:
        s, body = _http(f"https://api.github.com/repos/{r}", token)
        access[r] = ("push" if body.get("permissions", {}).get("push") else "read") if s == 200 else "no access"
    return {"ok": True, "message": f"Signed in as {user.get('login')}.", "access": access}


def repo_access(token: str | None, repos: list[str]) -> tuple[dict, list[str]]:
    """Can the factory's GitHub token use these repositories? ({repo: why not}, notes about what could not be settled).
    Only a clear no from GitHub refuses a repository; no token, no network or a rate limit leaves it unchecked and says so."""
    refused, notes = {}, []
    if not repos:
        return refused, notes
    if not token:
        return refused, ["No GitHub token is stored yet (Credentials), so access to the new repositories was not checked."]
    unchecked, readonly, rejected = [], [], False
    for r in repos[:30]:
        s, body = _http(f"https://api.github.com/repos/{r}", token)
        limited = s in (403, 429) and "rate limit" in str(body.get("message", "")).lower()
        if s == 200:
            if not body.get("permissions", {}).get("push"):
                readonly.append(r)
        elif s in (403, 404) and not limited:
            refused[r] = "The factory's GitHub token cannot see this repository. Check the name, or give the token access to it."
        else:
            unchecked.append(r)
            rejected = rejected or s == 401
    unchecked += repos[30:]
    if readonly:
        notes.append(f"The GitHub token can read {', '.join(readonly)} but not write to it, so the factory cannot open pull requests there.")
    if unchecked:
        notes.append(f"Access to {', '.join(unchecked)} could not be checked" + (" (GitHub rejected the token)." if rejected else "."))
    return refused, notes


def telegram_me(token: str) -> dict:
    s, body = _http(f"https://api.telegram.org/bot{token}/getMe", None)
    if s == 200 and body.get("ok"):
        return {"ok": True, "username": body["result"].get("username", "")}
    return {"ok": False, "message": _scrub(str(body.get("description", body.get("error", f"HTTP {s}"))), token)}


def telegram_senders(token: str) -> list[dict]:
    """Who has messaged the bot: id, first name, chat type. Never the message text."""
    s, body = _http(f"https://api.telegram.org/bot{token}/getUpdates?timeout=0", None)
    seen: dict[int, dict] = {}
    if s == 200:
        for u in body.get("result", []):
            m = u.get("message") or {}
            f = m.get("from") or {}
            if f.get("id"):
                seen[f["id"]] = {"id": f["id"], "name": str(f.get("first_name", ""))[:40], "chat": (m.get("chat") or {}).get("type", "")}
    return list(seen.values())


def telegram_send(token: str, chat_id: int, text: str) -> dict:
    s, body = _http(f"https://api.telegram.org/bot{token}/sendMessage", None, {"chat_id": chat_id, "text": text})
    return {"ok": s == 200 and body.get("ok", False), "message": "Sent." if s == 200 else _scrub(str(body.get("description", f"HTTP {s}")), token)}


def slack_check(bot_token: str) -> dict:
    s, body = _http("https://slack.com/api/auth.test", bot_token, {})
    if s == 200 and body.get("ok"):
        return {"ok": True, "message": f"The bot token works (workspace {str(body.get('team', ''))[:60]}, bot {str(body.get('user', ''))[:40]})."}
    return {"ok": False, "message": _scrub(f"Slack rejected the token: {str(body.get('error', f'HTTP {s}'))[:80]}", bot_token)}


def slack_app_check(app_token: str) -> dict:
    """The app-level token can open a Socket Mode connection (only the address is asked for; no connection is made)."""
    s, body = _http("https://slack.com/api/apps.connections.open", app_token, {})
    if s == 200 and body.get("ok"):
        return {"ok": True, "message": "The app-level token works and Socket Mode is on."}
    err = str(body.get("error", f"HTTP {s}"))[:80]
    hint = " Turn on Socket Mode in the app's settings." if err in ("not_allowed_token_type", "socket_mode_not_enabled") else ""
    return {"ok": False, "message": _scrub(f"Slack rejected the token: {err}.", app_token) + hint}


def slack_send(bot_token: str, channel: str, text: str) -> dict:
    s, body = _http("https://slack.com/api/chat.postMessage", bot_token, {"channel": channel, "text": text, "mrkdwn": False})
    if s == 200 and body.get("ok"):
        return {"ok": True, "message": "Sent."}
    err = str(body.get("error", f"HTTP {s}"))[:80]
    hint = " Invite the bot to the channel (/invite @name), or use the id of a direct message." if err == "not_in_channel" else ""
    return {"ok": False, "message": _scrub(err, bot_token) + "." + hint}


def check_openrouter(key: str, model: str) -> dict:
    from ..jev import QUESTIONS, URL
    s, body = _http(URL, key, {"model": model, "state": {"title": "connection test", "body": "hello", "labels": []},
                               "questions": {"kind": QUESTIONS["kind"]}})
    if s == 200:
        return {"ok": True, "message": f"Jev answered (cost ${body.get('usage', {}).get('cost', 0):.6f})."}
    return {"ok": False, "message": f"OpenRouter said HTTP {s}: {_scrub(str(body.get('error', body))[:160], key)}"}


def check_elevenlabs(key: str, voice_id: str) -> dict:
    """Reads the configured voice (or, without one, the account) with the key; says nothing about the key itself."""
    url = f"https://api.elevenlabs.io/v1/voices/{voice_id}" if voice_id else "https://api.elevenlabs.io/v1/user"
    req = urllib.request.Request(url, headers={"xi-api-key": key, "Accept": "application/json", "User-Agent": "shikumi-ui"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        why = {401: "the key was refused", 404: "there is no voice with the configured id"}.get(e.code, f"HTTP {e.code}")
        return {"ok": False, "message": f"ElevenLabs: {why}."}
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        return {"ok": False, "message": f"Could not reach ElevenLabs: {_scrub(str(getattr(e, 'reason', e)), key)[:160]}"}
    if voice_id:
        name = str(body.get("name") or "")[:60] if isinstance(body, dict) else ""
        return {"ok": True, "message": f"The key works and the voice {name or voice_id} is available."}
    return {"ok": True, "message": "The key works. Set the ElevenLabs voice id on Settings → Review videos."}


def classify_sample(cfg: Config, title: str, body: str, labels: list[str]) -> tuple[Classification, str]:
    """Run the CONFIGURED classifier on pasted text so rule changes can be tried before relying on them."""
    key = read_secret(cfg, "openrouter")
    if cfg.classifier_backend == "jev" and key:
        c = JevClassifier(key, cfg.jev_model, kind_aliases=cfg.kind_aliases).classify(title[:300], body[:15000], labels)
        return c, ("Jev (" + c.source + ")" if c.source != "labels" else "Jev was unreachable: fell back to labels")
    return RuleClassifier(cfg.kind_aliases).classify(title, body, labels), "labels only"


def save_harness_credential(cfg: Config, name: str, value: str) -> None:
    """Writes VAR=value into the harness's credential file. claude-code is managed on the Credentials page."""
    from ..config import harness_for
    h = harness_for(cfg, name)
    if h is None or not h.env_var or not re.fullmatch(r"[A-Z][A-Z0-9_]*", h.env_var):
        raise ValueError("this harness has no credential variable to set here")
    value = (value or "").strip()
    if not SECRET_RE.match(value):
        raise ValueError("that does not look like a key (8-500 characters, no spaces or line breaks)")
    write_secret(Path(h.env_file), f"{h.env_var}={value}\n")


def harness_credential_status(cfg: Config, name: str) -> dict:
    from ..config import harness_for
    h = harness_for(cfg, name)
    p = Path(h.env_file) if h else None
    if not p or not p.is_file() or p.stat().st_size == 0:
        return {"set": False}
    return {"set": True, "updated": p.stat().st_mtime, "vars": ", ".join(sorted({ln.split("=", 1)[0] for ln in p.read_text().splitlines() if "=" in ln}))}
