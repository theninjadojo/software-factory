"""Slack: alerts out, a few fixed commands and Run/Skip/answer buttons in, over Socket Mode (no public URL).
Trust model, as for Telegram: only the configured Slack user is obeyed (buttons must also come from the configured channel);
everything else is dropped, and only the sender's id, display name and channel are remembered so the UI can offer 'use this'.
The connection runs as soon as both tokens are saved, before anyone is configured, so the person setting it up can type /factory
and pick themselves on the UI's Slack page. Until then nobody is obeyed and no alert is sent.
Free text is never executed or forwarded to an agent (the app subscribes to no message events). Messages go out as plain text."""
import json
import logging
import socket
import sqlite3
import threading
import time
import urllib.request
from pathlib import Path

from . import db as dbm
from . import pause, usage, wsclient
from .telegram import parse_callback

log = logging.getLogger("factory.slack")
HELP = "Commands: /factory status | usage | pause | resume | help"
NOT_LINKED = "Shikumi is not linked to anyone yet. Open the Slack page in the factory's UI and press 'Use this' next to your name."
API = "https://slack.com/api/"


def authorized(user_id: str | None, channel_id: str | None, want_user: str, want_channel: str | None = None) -> bool:
    """The configured user, and (for buttons) the configured channel. A missing id never matches."""
    return bool(user_id) and user_id == want_user and (want_channel is None or channel_id == want_channel)


def blocks(text: str, buttons: list | None) -> list:
    """Block Kit for a message with buttons: the text, then a row of buttons per row given. Data 'url:https://...' is a link."""
    out = [{"type": "section", "text": {"type": "plain_text", "text": text[:3000], "emoji": False}}]
    rows = (buttons if isinstance(buttons[0], list) else [buttons]) if buttons else []
    for r, row in enumerate(rows):
        els = []
        for c, (label, data) in enumerate(row[:25]):
            el = {"type": "button", "text": {"type": "plain_text", "text": label[:75], "emoji": False}, "action_id": f"b{r}_{c}"}
            if data.startswith("url:"):
                el["url"] = data[4:]
            else:
                el["value"] = data[:2000]
            els.append(el)
        if els:
            out.append({"type": "actions", "elements": els})
    return out


class Slack:
    def __init__(self, bot_token: str, app_token: str, channel: str | None, user_id: str | None, state_dir: str, db_path: str,
                 allowed_repos: list[str]):
        self.bot_token, self.app_token, self.channel, self.user_id = bot_token, app_token, channel, user_id
        self.state_dir, self.db_path, self.allowed = Path(state_dir), db_path, set(allowed_repos)

    def _api(self, method: str, token: str, **params) -> dict:
        req = urllib.request.Request(API + method, data=json.dumps(params).encode(), headers={
            "Content-Type": "application/json; charset=utf-8", "Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req, timeout=30) as r:
            out = json.load(r)
        if not out.get("ok"):
            raise RuntimeError(f"slack {method}: {str(out.get('error'))[:80]}")      # the error code only, never the request
        return out

    def send(self, text: str, buttons: list | None = None) -> None:
        if not self.channel:
            return
        params = {"channel": self.channel, "text": text[:3000], "mrkdwn": False, "unfurl_links": False, "unfurl_media": False}
        if buttons:
            params["blocks"] = blocks(text, buttons)
        try:
            self._api("chat.postMessage", self.bot_token, **params)
        except Exception:
            log.exception("slack send failed")      # alerts must never break the orchestrator

    def _status(self) -> str:
        c = sqlite3.connect(self.db_path)
        rows = c.execute("select repo,issue,outcome,datetime(decided_at,'unixepoch','localtime') "
                         "from decisions order by decided_at desc limit 5").fetchall()
        c.close()
        return f"paused: {pause.paused(self.state_dir) or 'no'}\n" + "\n".join(f"{t} {r}#{i} {o}" for r, i, o, t in rows)

    def _note_unknown(self, user_id: str | None, name: str, channel: str | None = None) -> None:
        """Remember (id, name, channel) of someone else using the app, never what they typed, so the UI can offer 'use this'."""
        try:
            if not user_id:
                return
            db = dbm.connect(self.db_path)
            known = json.loads((dbm.get_status(db).get("slack_unknown_senders") or {}).get("value", "[]"))
            known = [k for k in known if k["id"] != user_id] + [{"id": str(user_id)[:30], "name": str(name)[:40], "channel": str(channel or "")[:30],
                                                                 "ts": time.time()}]
            dbm.set_status(db, "slack_unknown_senders", json.dumps(known[-5:]))
            db.close()
        except Exception:
            log.exception("could not record unknown sender")

    def _note_connection(self, error: str | None) -> None:
        """Whether the Socket Mode connection is up, for the UI's Slack page: the error code only, never a token."""
        try:
            db = dbm.connect(self.db_path)
            dbm.set_status(db, "slack_connection", json.dumps({"ok": error is None, "error": (error or "")[:120]}))
            db.close()
        except Exception:
            log.exception("could not record the slack connection state")

    def command(self, p: dict) -> dict:
        """A /factory slash command. Returns the reply (shown only to the person who typed it)."""
        if not authorized(p.get("user_id"), p.get("channel_id"), self.user_id):
            self._note_unknown(p.get("user_id"), p.get("user_name", ""), p.get("channel_id"))
            return {"response_type": "ephemeral", "text": "You are not allowed to use this." if self.user_id else NOT_LINKED}
        word = (p.get("text") or "").strip().lower().split(" ")[0]
        if word == "pause":
            (self.state_dir / "PAUSED").write_text("")
            text = "paused"
        elif word == "resume":
            (self.state_dir / "PAUSED").unlink(missing_ok=True)
            (self.state_dir / "pause_until").unlink(missing_ok=True)
            text = "resumed"
        elif word == "usage":
            c = sqlite3.connect(self.db_path)
            text = usage.summary(usage.load(c))
            c.close()
        elif word == "status":
            text = self._status()
        else:
            text = HELP
        return {"response_type": "ephemeral", "text": text}

    def action(self, p: dict) -> None:
        """A button click (block_actions)."""
        user = p.get("user") or {}
        channel = (p.get("channel") or {}).get("id") or (p.get("container") or {}).get("channel_id")
        if not authorized(user.get("id"), channel, self.user_id, self.channel):
            self._note_unknown(user.get("id"), user.get("username") or user.get("name") or "", channel)
            return
        for a in p.get("actions") or []:
            parsed = parse_callback(a.get("value") or "")
            if parsed and parsed[1] in self.allowed:
                action, repo, num = parsed
                c = sqlite3.connect(self.db_path)
                c.execute("INSERT OR REPLACE INTO approvals VALUES (?,?,?,?)", (repo, num, action, time.time()))
                c.commit()
                c.close()
                self.send(f"{action} queued for {repo}#{num}")

    def handle(self, env: dict, ws) -> None:
        """One Socket Mode envelope. Acknowledge within three seconds: a slash command carries its reply in the ack; a
        button click is acknowledged first and then acted on (acting posts to Slack, which can be slow)."""
        eid, kind, payload = env.get("envelope_id"), env.get("type"), env.get("payload") or {}
        if kind == "slash_commands":
            reply = None
            try:
                reply = self.command(payload)
            except Exception:
                log.exception("slack command failed")
            if eid:
                ws.send(json.dumps({"envelope_id": eid, **({"payload": reply} if reply else {})}))
            return
        if eid:
            ws.send(json.dumps({"envelope_id": eid}))
        if kind == "interactive" and payload.get("type") == "block_actions":
            try:
                self.action(payload)
            except Exception:
                log.exception("slack action failed")

    def serve(self, ws) -> None:
        """Read envelopes from one connection until it ends (a 'disconnect' envelope, an error or silence)."""
        while True:
            env = json.loads(ws.recv())
            if env.get("type") == "disconnect":
                return
            self.handle(env, ws)

    def run_forever(self) -> None:
        delay = 1
        while True:
            try:
                ws = wsclient.connect(self._api("apps.connections.open", self.app_token)["url"])
                try:
                    delay = 1
                    self._note_connection(None)
                    self.serve(ws)
                finally:
                    ws.close()
            except (socket.timeout, wsclient.Closed) as e:
                log.info("slack connection ended (%s); reconnecting", e or type(e).__name__)
            except Exception as e:
                log.exception("slack connection failed")
                self._note_connection(str(e) if isinstance(e, RuntimeError) else type(e).__name__)
                delay = min(delay * 2, 60)
            time.sleep(delay)

    def start(self) -> None:
        threading.Thread(target=self.run_forever, daemon=True, name="slack").start()
