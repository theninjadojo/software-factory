"""Routes that show or change settings, credentials and Telegram. All POSTs arrive here already CSRF-checked."""
import json

from .. import db as dbm
from ..config import deep_merge
from ..router import decide
from . import forms, integrations as I, settings as S, views
from .views import esc

FLASH = {
    "saved": "Saved. The factory applies it at its next idle moment (never mid-task).",
    "secret": "Saved. The factory restarts when it is next idle so it picks the credential up.",
    "id": "Chat id saved. Press 'Send a test message' to confirm.",
}


def _send_page(h, status: int, title: str, body: str, active: str, csrf: str, flash=None, kind="ok") -> None:
    h._send(status, views.page(title, body, active, csrf, nav=views.NAV, flash=flash, flash_kind=kind))


def _files(h):
    base = S.base_raw(h.app.config_path)
    return base, deep_merge(base, S.overrides_raw(h.app.config_path))


def _unknown_senders(h) -> list:
    db = h.app.ro_db()
    if db is None:
        return []
    try:
        return json.loads((dbm.get_status(db).get("telegram_unknown_senders") or {}).get("value", "[]"))
    finally:
        db.close()


# ------------------------------------------------------------------ settings
def render_settings(h, section: str, csrf: str, submitted=None, tester: str = "") -> str:
    base, eff = _files(h)
    if section == "projects":
        return forms.projects_form(base, eff, csrf, submitted)
    body = forms.settings_form(section, eff, base, csrf, submitted)
    if section == "classifier":
        body += forms.classifier_tester(csrf, tester)
    return body


def settings_get(h, q: dict, csrf: str) -> None:
    section = q.get("section", "general")
    if section != "projects" and section not in S.SECTIONS:
        return h._send(404, "no such section", "text/plain")
    _send_page(h, 200, "Settings", render_settings(h, section, csrf), "/settings", csrf, FLASH.get(q.get("ok", "")))


def settings_save(h, form, csrf: str) -> None:
    section = form.get("section", "")
    try:
        S.save_section(h.app.config_path, h.app.state_dir(), section, form)
    except S.SettingsError as e:
        if section not in S.SECTIONS:
            return h._send(404, "no such section", "text/plain")
        return _send_page(h, 422, "Settings", render_settings(h, section, csrf, submitted=form), "/settings", csrf, " · ".join(e.messages), "bad")
    h._redirect(f"/settings?section={section}&ok=saved")


def projects_save(h, form, csrf: str) -> None:
    try:
        S.save_projects(h.app.config_path, h.app.state_dir(), form)
    except S.SettingsError as e:
        return _send_page(h, 422, "Settings", render_settings(h, "projects", csrf, submitted=form), "/settings", csrf, " · ".join(e.messages), "bad")
    h._redirect("/settings?section=projects&ok=saved")


def classify_test(h, form, csrf: str) -> None:
    cfg = h.app.cfg()
    labels = [x.strip() for x in (form.get("labels") or "").split(",") if x.strip()][:30]
    c, how = I.classify_sample(cfg, (form.get("title") or "")[:300], (form.get("body") or "")[:15000], labels)
    result = forms.classification_result(c, how, decide(cfg, c))
    _send_page(h, 200, "Settings", render_settings(h, "classifier", csrf, tester=result), "/settings", csrf)


# ------------------------------------------------------------------ credentials
def credentials_get(h, q: dict, csrf: str) -> None:
    _send_page(h, 200, "Credentials", forms.credentials_page(h.app.cfg(), csrf), "/credentials", csrf, FLASH.get(q.get("ok", "")))


def credentials_save(h, form, csrf: str) -> None:
    cfg = h.app.cfg()
    try:
        I.save_secret(cfg, form.get("name", ""), form.get("value", ""), form.get("kind", "subscription"))
    except ValueError as e:
        return _send_page(h, 400, "Credentials", forms.credentials_page(cfg, csrf), "/credentials", csrf, str(e), "bad")
    S.request_restart(h.app.state_dir())
    h._redirect("/credentials?ok=secret")


def credentials_test(h, form, csrf: str) -> None:
    cfg, name = h.app.cfg(), form.get("name", "")
    secret = I.read_secret(cfg, name) if name in I.SECRETS else None
    if not secret:
        return _send_page(h, 400, "Credentials", forms.credentials_page(cfg, csrf), "/credentials", csrf, "Nothing is stored for that credential yet.", "bad")
    if name == "github":
        r = I.check_github(secret, cfg.repos)
        table = ("<table><thead><tr><th>Repository</th><th>Access</th></tr></thead><tbody>" + "".join(
            f'<tr><td>{esc(repo)}</td><td>{views.badge(a, "good" if a == "push" else "warn" if a == "read" else "bad")}</td></tr>'
            for repo, a in r.get("access", {}).items()) + "</tbody></table>") if r.get("access") else ""
        return _send_page(h, 200, "Credentials", forms.credentials_page(cfg, csrf, table), "/credentials", csrf, r["message"], "ok" if r["ok"] else "bad")
    if name == "openrouter":
        r = I.check_openrouter(secret, cfg.jev_model)
        return _send_page(h, 200, "Credentials", forms.credentials_page(cfg, csrf), "/credentials", csrf, r["message"], "ok" if r["ok"] else "bad")
    _send_page(h, 400, "Credentials", forms.credentials_page(cfg, csrf), "/credentials", csrf, "There is no test for that credential.", "bad")


# ------------------------------------------------------------------ harnesses
def harnesses_get(h, q: dict, csrf: str) -> None:
    _, eff = _files(h)
    _send_page(h, 200, "Harnesses", forms.harnesses_page(h.app.cfg(), eff, csrf), "/harnesses", csrf, FLASH.get(q.get("ok", "")))


def harnesses_save(h, form, csrf: str) -> None:
    try:
        S.save_harness(h.app.config_path, h.app.state_dir(), form.get("name", ""), form)
    except S.SettingsError as e:
        _, eff = _files(h)
        return _send_page(h, 422, "Harnesses", forms.harnesses_page(h.app.cfg(), eff, csrf), "/harnesses", csrf, " · ".join(e.messages), "bad")
    h._redirect("/harnesses?ok=saved")


def harnesses_credential(h, form, csrf: str) -> None:
    try:
        I.save_harness_credential(h.app.cfg(), form.get("name", ""), form.get("value", ""))
    except ValueError as e:
        _, eff = _files(h)
        return _send_page(h, 400, "Harnesses", forms.harnesses_page(h.app.cfg(), eff, csrf), "/harnesses", csrf, str(e), "bad")
    S.request_restart(h.app.state_dir())
    h._redirect("/harnesses?ok=secret")


# ------------------------------------------------------------------ telegram
def _telegram(h, csrf: str, flash=None, kind="ok", status: int = 200, unknown=None, result: str = "") -> None:
    _, eff = _files(h)
    _send_page(h, status, "Telegram", forms.telegram_page(h.app.cfg(), eff, csrf, unknown if unknown is not None else _unknown_senders(h), result), "/telegram", csrf, flash, kind)


def telegram_get(h, q: dict, csrf: str) -> None:
    _telegram(h, csrf, FLASH.get(q.get("ok", "")))


def telegram_save(h, form, csrf: str) -> None:
    try:
        S.save_telegram(h.app.config_path, h.app.state_dir(), form)
    except S.SettingsError as e:
        return _telegram(h, csrf, " · ".join(e.messages), "bad", 422)
    h._redirect("/telegram?ok=saved")


def telegram_detect(h, form, csrf: str) -> None:
    token = I.read_secret(h.app.cfg(), "telegram")
    if not token:
        return _telegram(h, csrf, "Save the bot token on the Credentials page first.", "bad", 400)
    found = I.telegram_senders(token)
    known = {u["id"] for u in found}
    senders = found + [u for u in _unknown_senders(h) if u["id"] not in known]
    msg = ("Found the account(s) below. Pick yours." if senders else
           "No messages yet. Send /start to your bot in Telegram, then press Find again. (If the factory is already using Telegram, "
           "people who message the bot are listed here once they do.)")
    _telegram(h, csrf, msg, "ok" if senders else "bad", unknown=senders)


def telegram_use(h, form, csrf: str) -> None:
    try:
        S.set_chat_id(h.app.config_path, h.app.state_dir(), int(form.get("chat_id", "")))
    except (ValueError, S.SettingsError):
        return _telegram(h, csrf, "That is not a valid chat id.", "bad", 400)
    h._redirect("/telegram?ok=id")


def telegram_test(h, form, csrf: str) -> None:
    cfg = h.app.cfg()
    token = I.read_secret(cfg, "telegram")
    if not token or not cfg.telegram_chat_id:
        return _telegram(h, csrf, "Set the bot token and your chat id first.", "bad", 400)
    r = I.telegram_send(token, cfg.telegram_chat_id, "Test message from the software-factory UI. Telegram is set up correctly.")
    _telegram(h, csrf, r["message"], "ok" if r["ok"] else "bad")


GET = {"/settings": settings_get, "/credentials": credentials_get, "/telegram": telegram_get, "/harnesses": harnesses_get}
POST = {"/settings/save": settings_save, "/settings/projects": projects_save, "/classify/test": classify_test,
        "/credentials/save": credentials_save, "/harnesses/save": harnesses_save, "/harnesses/credential": harnesses_credential, "/credentials/test": credentials_test,
        "/telegram/save": telegram_save, "/telegram/detect": telegram_detect, "/telegram/use": telegram_use, "/telegram/test": telegram_test}
