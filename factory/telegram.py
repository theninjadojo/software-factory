"""Telegram: alerts out, a few fixed commands and Run/Skip/answer buttons in.
Trust model: only updates from the configured chat id are acted on; everything else is dropped.
Free text is never executed or forwarded to an agent. Messages go out as plain text (no parse_mode)."""
import json
import logging
import re
import sqlite3
import threading
import time
import urllib.request
from pathlib import Path

from . import db as dbm
from . import pause, usage

log = logging.getLogger("factory.telegram")
HELP = "Commands: /status /usage /pause /resume /help"


def authorized(update: dict, chat_id: int) -> bool:
    msg = update.get("message") or (update.get("callback_query") or {}).get("message") or {}
    sender = (update.get("message") or update.get("callback_query") or {}).get("from", {})
    return msg.get("chat", {}).get("id") == chat_id and sender.get("id") == chat_id


def parse_callback(data: str) -> tuple[str, str, int] | None:
    """'run|owner/repo|12' -> ('run','owner/repo',12); 'stage:architect|...', 'accept|...' (accept the recommendations) and
    'q:q1:b|...' (option b of question q1) likewise. Anything else -> None. An answer is checked against the ticket's questions later."""
    try:
        action, repo, num = data.split("|")
        if (action in ("run", "skip", "accept") or re.fullmatch(r"stage:[a-z]{1,20}", action)
                or re.fullmatch(r"q:[a-z0-9][a-z0-9-]{0,15}:[a-z0-9][a-z0-9-]{0,7}", action)) and repo.count("/") == 1:
            return action, repo, int(num)
    except ValueError:
        pass
    return None


class Telegram:
    def __init__(self, token: str, chat_id: int, state_dir: str, db_path: str, allowed_repos: list[str]):
        self.token, self.chat_id = token, chat_id
        self.state_dir, self.db_path, self.allowed = Path(state_dir), db_path, set(allowed_repos)

    def _call(self, method: str, **params):
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{self.token}/{method}",
            data=json.dumps(params).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=40) as r:
            return json.load(r)["result"]

    def send(self, text: str, buttons: list | None = None) -> None:
        """buttons: one row of (text, data), or a list of rows. Data 'url:https://...' is a link (the configured UI address)."""
        params = {"chat_id": self.chat_id, "text": text[:3500]}
        if buttons:
            rows = buttons if isinstance(buttons[0], list) else [buttons]
            params["reply_markup"] = {"inline_keyboard": [[{"text": t, "url": d[4:]} if d.startswith("url:") else {"text": t, "callback_data": d}
                                                           for t, d in row] for row in rows if row]}
        try:
            self._call("sendMessage", **params)
        except Exception:
            log.exception("telegram send failed")   # alerts must never break the orchestrator

    def _status(self) -> str:
        c = sqlite3.connect(self.db_path)
        rows = c.execute("select repo,issue,outcome,datetime(decided_at,'unixepoch','localtime') "
                         "from decisions order by decided_at desc limit 5").fetchall()
        p = pause.paused(self.state_dir)
        return f"paused: {p or 'no'}\n" + "\n".join(f"{t} {r}#{i} {o}" for r, i, o, t in rows)

    def _note_unknown(self, update: dict) -> None:
        """Remember (id, first name, chat type) of someone else messaging the bot, never their text, so the UI can offer
        'use this id'. Nothing is acted on."""
        try:
            src = update.get("message") or update.get("callback_query") or {}
            who = src.get("from") or {}
            if not who.get("id"):
                return
            chat = (src.get("message") or src).get("chat", {})
            db = dbm.connect(self.db_path)
            known = json.loads((dbm.get_status(db).get("telegram_unknown_senders") or {}).get("value", "[]"))
            known = [k for k in known if k["id"] != who["id"]] + [
                {"id": who["id"], "name": str(who.get("first_name", ""))[:40], "chat": chat.get("type", ""), "ts": time.time()}]
            dbm.set_status(db, "telegram_unknown_senders", json.dumps(known[-5:]))
            db.close()
        except Exception:
            log.exception("could not record unknown sender")

    def handle(self, update: dict) -> None:
        if not authorized(update, self.chat_id):
            self._note_unknown(update)
            return
        if (cb := update.get("callback_query")):
            parsed = parse_callback(cb.get("data", ""))
            self._call("answerCallbackQuery", callback_query_id=cb["id"])
            if parsed and parsed[1] in self.allowed:
                action, repo, num = parsed
                c = sqlite3.connect(self.db_path)
                c.execute("INSERT OR REPLACE INTO approvals VALUES (?,?,?,?)", (repo, num, action, time.time()))
                c.commit()
                self.send(f"{action} queued for {repo}#{num}")
            return
        text = ((update.get("message") or {}).get("text") or "").strip().split("@")[0].lower()
        if text == "/pause":
            (self.state_dir / "PAUSED").write_text("")
            self.send("paused")
        elif text == "/resume":
            (self.state_dir / "PAUSED").unlink(missing_ok=True)
            (self.state_dir / "pause_until").unlink(missing_ok=True)
            self.send("resumed")
        elif text == "/usage":
            c = sqlite3.connect(self.db_path)
            self.send(usage.summary(usage.load(c)))
            c.close()
        elif text == "/status":
            self.send(self._status())
        elif text in ("/help", "/start"):
            self.send(HELP)

    def poll_forever(self) -> None:
        offset = 0
        while True:
            try:
                for u in self._call("getUpdates", offset=offset, timeout=30, allowed_updates=["message", "callback_query"]):
                    offset = u["update_id"] + 1
                    self.handle(u)
            except Exception:
                log.exception("telegram poll failed")
                time.sleep(10)

    def start(self) -> None:
        threading.Thread(target=self.poll_forever, daemon=True, name="telegram").start()
