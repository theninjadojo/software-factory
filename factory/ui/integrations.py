"""Credentials and the outbound checks the UI offers. Secrets are write-only: stored 0600, never rendered back, never logged.
Outbound calls go only to fixed hosts (api.github.com, api.telegram.org, openrouter.ai)."""
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
}


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


def check_openrouter(key: str, model: str) -> dict:
    from ..jev import QUESTIONS, URL
    s, body = _http(URL, key, {"model": model, "state": {"title": "connection test", "body": "hello", "labels": []},
                               "questions": {"kind": QUESTIONS["kind"]}})
    if s == 200:
        return {"ok": True, "message": f"Jev answered (cost ${body.get('usage', {}).get('cost', 0):.6f})."}
    return {"ok": False, "message": f"OpenRouter said HTTP {s}: {_scrub(str(body.get('error', body))[:160], key)}"}


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
