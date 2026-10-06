"""Routes that show or change settings, credentials, Telegram and Slack. All POSTs arrive here already CSRF-checked."""
import json
import logging
import re
import shutil
import sqlite3
import threading
from pathlib import Path

from .. import backup as B
from .. import selfupdate as SU, tools, updates, version, workerupdate
from .. import db as dbm
from ..config import deep_merge
from ..router import decide
import time

from .. import schedules as sched
from . import chat as CH, features as FT, flooredit as FE, forms, integrations as I, labels as L, localtickets as LT, release as REL, review as RV, schedules as SC, scans as SN, screens as SB, settings as S, ticketreview as TRV, updatespage as UP, views, workers as WK
from .views import esc

log = logging.getLogger("factory.ui")

FLASH = {
    "saved": "Saved. The factory applies it at its next idle moment (never mid-task).",
    "secret": "Saved. The factory restarts when it is next idle so it picks the credential up.",
    "id": "Chat id saved. Press 'Send a test message' to confirm.",
    "slack_id": "Saved. Slack turns on when the factory is next idle; then press 'Send a test message' to confirm.",
    "slack_token": "Token saved. The factory connects to Slack when it is next idle (it restarts to pick the token up).",
    "run": "Run requested. The factory runs it within a poll (up to a minute) and opens a real ticket, whatever dry-run says.",
    "sched_saved": "Schedule saved. The factory applies it at its next idle moment (never mid-task).",
    "sched_deleted": "Schedule deleted. Snapshots already on disk were kept.",
    "scan_saved": "Scan saved. The factory applies it at its next idle moment (never mid-task).",
    "scan_deleted": "Scan deleted. Tickets already opened were kept.",
    "scan_run": "Scan requested. It starts within a poll (up to a minute); tickets follow when the worker has finished. Nothing runs in dry-run or while paused.",
    "smell_saved": "Smell saved. The factory applies it at its next idle moment (never mid-task).",
    "smell_deleted": "Smell deleted. Tickets already opened were kept.",
    "live": "Going live. The factory applies it at its next idle moment (never mid-task); after that it starts agents and opens PRs for labeled issues.",
    "layout_saved": "Floor layout saved. The floor is drawn from it; Edit layout changes it again or resets it.",
    "dry": "Back to dry run. The factory applies it at its next idle moment: it only logs decisions and writes nothing.",
    "restore": "Restore staged. The configuration is already replaced; the factory swaps in the database when it is next idle and stays paused until you resume it. "
               "Enter the credentials again on the Credentials and Harnesses pages.",
}


def _send_page(h, status: int, title: str, body: str, active: str, csrf: str, flash=None, kind="ok", section: str = "") -> None:
    """Every admin page renders inside the Settings layout: the Settings tab is lit and the side list marks the page."""
    cached = L.needs_cached()
    side = forms.side_list(section or active.lstrip("/"))
    h._send(status, views.page(title, body, "/settings", csrf, nav=views.NAV, flash=flash, flash_kind=kind, badges={"/tickets": len(cached)} if cached else None, side=side))


def _files(h):
    base = S.base_raw(h.app.config_path)
    return base, deep_merge(base, S.overrides_raw(h.app.config_path))


def _status_json(h, key: str, default: str = "[]"):
    """A JSON value the orchestrator keeps in the status table (people who used a chat app, the Slack connection)."""
    db = h.app.ro_db()
    if db is None:
        return json.loads(default)
    try:
        return json.loads((dbm.get_status(db).get(key) or {}).get("value", default))
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
    if not q.get("section"):                # the home: every feature and its state
        body = FT.home(h.app.cfg(), csrf, q.get("show", ""), q.get("q", ""), _status_json(h, "slack_connection", "null"))
        return _send_page(h, 200, "Settings", body, "/settings", csrf, FLASH.get(q.get("ok", "")), section="home")
    section = q.get("section")
    if section == "labels":
        return _send_page(h, 200, "Settings", forms.labels_page(h.app.cfg()), "/settings", csrf, FLASH.get(q.get("ok", "")), section="labels")
    if section != "projects" and section not in S.SECTIONS:
        return h._send(404, "no such section", "text/plain")
    _send_page(h, 200, "Settings", render_settings(h, section, csrf), "/settings", csrf, FLASH.get(q.get("ok", "")), section=section)


def mode_set(h, form, csrf: str) -> None:
    """The Floor page's dry-run switch. Going live needs the confirmation box; going back to dry run never does."""
    want_dry = form.get("dry_run") == "1"
    if not want_dry and form.get("confirm") != "1":
        return h._redirect("/?mode=confirm")
    try:
        changed = S.set_dry_run(h.app.config_path, h.app.state_dir(), want_dry)
    except S.SettingsError as e:
        return _send_page(h, 422, "Settings", "", "/settings", csrf, " · ".join(e.messages), "bad", section="general")
    h._redirect("/?ok=" + ("dry" if want_dry else "live") if changed else "/")


def _feature_back(form) -> str:
    """Back to the Settings home, or to a page the strips live on (the Floor or Tickets)."""
    b = form.get("back", "")
    return b if b == "/settings" or (L.BACK.fullmatch(b) and "//" not in b) else "/settings"


def feature_set(h, form, csrf: str) -> None:
    """A switch from the Settings home or from the screen it shapes; the confirmation was the form itself."""
    key, back = form.get("key", ""), _feature_back(form)
    try:
        changed = S.set_switch(h.app.config_path, h.app.state_dir(), key, form.get("on") == "1")
    except S.SettingsError as e:
        msg, kind = " · ".join(e.messages), "bad"
    else:
        name = FT.SWITCHES[key][0]
        msg, kind = (f"{name} turned {'on' if form.get('on') == '1' else 'off'}. {FT.APPLIES}" if changed else f"{name} was already {'on' if form.get('on') == '1' else 'off'}."), "ok"
    if back == "/settings":
        return _send_page(h, 200 if kind == "ok" else 400, "Settings", FT.home(h.app.cfg(), csrf, slack_conn=_status_json(h, "slack_connection", "null")),
                          "/settings", csrf, msg, kind, section="home")
    L.flash_set(csrf, msg, kind)
    h._redirect(back)


def parallel_set(h, form, csrf: str) -> None:
    try:
        S.set_parallel(h.app.config_path, h.app.state_dir(), int(form.get("value", "")))
        L.flash_set(csrf, f"Agents at once: {int(form.get('value'))}. {FT.APPLIES}")
    except (ValueError, S.SettingsError):
        L.flash_set(csrf, "Agents at once must be from 1 to 8.", "bad")
    h._redirect("/")


def settings_save(h, form, csrf: str) -> None:
    section = form.get("section", "")
    try:
        S.save_section(h.app.config_path, h.app.state_dir(), section, form)
    except S.SettingsError as e:
        if section not in S.SECTIONS:
            return h._send(404, "no such section", "text/plain")
        return _send_page(h, 422, "Settings", render_settings(h, section, csrf, submitted=form), "/settings", csrf, " · ".join(e.messages), "bad", section=section)
    h._redirect(f"/settings?section={section}&ok=saved")


def projects_save(h, form, csrf: str) -> None:
    try:
        S.save_projects(h.app.config_path, h.app.state_dir(), form)
    except S.SettingsError as e:
        return _send_page(h, 422, "Settings", render_settings(h, "projects", csrf, submitted=form), "/settings", csrf, " · ".join(e.messages), "bad", section="projects")
    h._redirect("/settings?section=projects&ok=saved")


def classify_test(h, form, csrf: str) -> None:
    cfg = h.app.cfg()
    labels = [x.strip() for x in (form.get("labels") or "").split(",") if x.strip()][:30]
    c, how = I.classify_sample(cfg, (form.get("title") or "")[:300], (form.get("body") or "")[:15000], labels)
    result = forms.classification_result(c, how, decide(cfg, c))
    _send_page(h, 200, "Settings", render_settings(h, "classifier", csrf, tester=result), "/settings", csrf, section="classifier")


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
        table = ("<table class=stack><thead><tr><th>Repository</th><th>Access</th></tr></thead><tbody>" + "".join(
            f'<tr><td data-l="Repository">{esc(repo)}</td><td data-l="Access">{views.badge(a, "good" if a == "push" else "warn" if a == "read" else "bad")}</td></tr>'
            for repo, a in r.get("access", {}).items()) + "</tbody></table>") if r.get("access") else ""
        return _send_page(h, 200, "Credentials", forms.credentials_page(cfg, csrf, table), "/credentials", csrf, r["message"], "ok" if r["ok"] else "bad")
    if name == "openrouter":
        r = I.check_openrouter(secret, cfg.jev_model)
        return _send_page(h, 200, "Credentials", forms.credentials_page(cfg, csrf), "/credentials", csrf, r["message"], "ok" if r["ok"] else "bad")
    if name in ("slack_bot", "slack_app"):
        r = I.slack_check(secret) if name == "slack_bot" else I.slack_app_check(secret)
        return _send_page(h, 200, "Credentials", forms.credentials_page(cfg, csrf), "/credentials", csrf, r["message"], "ok" if r["ok"] else "bad")
    _send_page(h, 400, "Credentials", forms.credentials_page(cfg, csrf), "/credentials", csrf, "There is no test for that credential.", "bad")


# ------------------------------------------------------------------ harnesses
def harnesses_get(h, q: dict, csrf: str) -> None:
    _, eff = _files(h)
    if tools.latest_due(h.app.state_dir()):          # the npm registry is asked in the background, never on the request
        threading.Thread(target=tools.refresh_latest, args=(h.app.state_dir(),), daemon=True).start()
    shown = L.flash_pop(csrf)
    _send_page(h, 200, "Harnesses", forms.harnesses_page(h.app.cfg(), eff, csrf, state_dir=h.app.state_dir()), "/harnesses", csrf,
               shown[0] if shown else FLASH.get(q.get("ok", "")), shown[1] if shown else "ok")


def harnesses_save(h, form, csrf: str) -> None:
    try:
        S.save_harness(h.app.config_path, h.app.state_dir(), form.get("name", ""), form)
    except S.SettingsError as e:
        _, eff = _files(h)
        return _send_page(h, 422, "Harnesses", forms.harnesses_page(h.app.cfg(), eff, csrf, state_dir=h.app.state_dir()), "/harnesses", csrf, " · ".join(e.messages), "bad")
    h._redirect("/harnesses?ok=saved")


def harnesses_credential(h, form, csrf: str) -> None:
    try:
        I.save_harness_credential(h.app.cfg(), form.get("name", ""), form.get("value", ""))
    except ValueError as e:
        _, eff = _files(h)
        return _send_page(h, 400, "Harnesses", forms.harnesses_page(h.app.cfg(), eff, csrf, state_dir=h.app.state_dir()), "/harnesses", csrf, str(e), "bad")
    S.request_restart(h.app.state_dir())
    h._redirect("/harnesses?ok=secret")


def tools_check(h, form, csrf: str) -> None:
    tools.refresh_latest(h.app.state_dir())
    L.flash_set(csrf, "Checked the newest versions.")
    h._redirect("/harnesses")


def tools_update(h, form, csrf: str) -> None:
    names = list(tools.TOOLS) if form.get("name") == "all" else [form.get("name", "")]
    asked = tools.request(h.app.state_dir(), names)
    log.info("tools: update requested for %s", asked or "nothing")
    L.flash_set(csrf, ("Update requested. The factory starts it within a poll (up to a minute) and it takes a few minutes; runs in flight are not interrupted."
                       if asked else "Pick one of the listed tools."), "ok" if asked else "bad")
    h._redirect("/harnesses")


# ------------------------------------------------------------------ telegram
def _telegram(h, csrf: str, flash=None, kind="ok", status: int = 200, unknown=None, result: str = "") -> None:
    _, eff = _files(h)
    _send_page(h, status, "Telegram", forms.telegram_page(h.app.cfg(), eff, csrf, unknown if unknown is not None else _status_json(h, "telegram_unknown_senders"), result), "/telegram", csrf, flash, kind)


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
    senders = found + [u for u in _status_json(h, "telegram_unknown_senders") if u["id"] not in known]
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
    r = I.telegram_send(token, cfg.telegram_chat_id, "Test message from the Shikumi UI. Telegram is set up correctly.")
    _telegram(h, csrf, r["message"], "ok" if r["ok"] else "bad")


# ------------------------------------------------------------------ slack
def _slack(h, csrf: str, flash=None, kind="ok", status: int = 200) -> None:
    _, eff = _files(h)
    page = forms.slack_page(h.app.cfg(), eff, csrf, _status_json(h, "slack_unknown_senders"), _status_json(h, "slack_connection", "null"))
    _send_page(h, status, "Slack", page, "/slack", csrf, flash, kind)


def slack_get(h, q: dict, csrf: str) -> None:
    _slack(h, csrf, FLASH.get(q.get("ok", "")))


def slack_save(h, form, csrf: str) -> None:
    try:
        S.save_slack(h.app.config_path, h.app.state_dir(), form)
    except S.SettingsError as e:
        return _slack(h, csrf, " · ".join(e.messages), "bad", 422)
    h._redirect("/slack?ok=saved")


def slack_token(h, form, csrf: str) -> None:
    name = form.get("name", "")
    if name not in I.SLACK_PREFIX:
        return _slack(h, csrf, "Unknown token.", "bad", 400)
    try:
        I.save_secret(h.app.cfg(), name, form.get("value", ""))
    except ValueError as e:
        return _slack(h, csrf, str(e), "bad", 400)
    S.request_restart(h.app.state_dir())
    h._redirect("/slack?ok=slack_token")


def slack_check(h, form, csrf: str) -> None:
    name = form.get("name", "")
    secret = I.read_secret(h.app.cfg(), name) if name in I.SLACK_PREFIX else None
    if not secret:
        return _slack(h, csrf, "Save that token first.", "bad", 400)
    r = I.slack_check(secret) if name == "slack_bot" else I.slack_app_check(secret)
    _slack(h, csrf, r["message"], "ok" if r["ok"] else "bad")


def slack_use(h, form, csrf: str) -> None:
    try:
        S.set_slack_user(h.app.config_path, h.app.state_dir(), form.get("user_id", ""), form.get("channel", ""))
    except S.SettingsError:
        return _slack(h, csrf, "That is not a valid member or channel id.", "bad", 400)
    h._redirect("/slack?ok=slack_id")


def slack_test(h, form, csrf: str) -> None:
    cfg = h.app.cfg()
    token = I.read_secret(cfg, "slack_bot")
    if not token or not cfg.slack_channel:
        return _slack(h, csrf, "Set the bot token and the channel id first.", "bad", 400)
    r = I.slack_send(token, cfg.slack_channel, "Test message from the Shikumi UI. Slack is set up correctly.")
    _slack(h, csrf, r["message"], "ok" if r["ok"] else "bad")


# ------------------------------------------------------------------ verification workers
def workers_get(h, q: dict, csrf: str) -> None:
    db = h.app.ro_db()
    try:
        if db is None:
            return _send_page(h, 200, "Workers", '<p class="muted">The orchestrator has not created its database yet.</p>', "/workers", csrf)
        if "id" in q:
            body = WK.job_page(db, int(q["id"])) if q["id"].isdigit() and len(q["id"]) < 10 else None
            if body is None:
                return _send_page(h, 404, "Workers", '<p class="muted">No such job.</p>', "/workers", csrf, section="workers")
            return _send_page(h, 200, f"Job #{int(q['id'])}", body, "/workers", csrf, section="workers")
        shown = L.flash_pop(csrf)
        _send_page(h, 200, "Workers", WK.workers_page(h.app.cfg(), db, csrf=csrf), "/workers", csrf,
                   shown[0] if shown else None, shown[1] if shown else "ok", section="workers")
    finally:
        if db is not None:
            db.close()


def workers_update(h, form, csrf: str) -> None:
    """The Update worker button: the worker updates to the factory's release the next time it is idle and asks."""
    db = sqlite3.connect(h.app.cfg().db_path, timeout=10)
    try:
        ok = workerupdate.request(db, form.get("worker", ""), time.time())
    finally:
        db.close()
    log.info("workers: update %s for %s", "requested" if ok else "refused", form.get("worker", "")[:41])
    L.flash_set(csrf, "The worker updates the next time it is idle (within a minute or two). It restarts through its service." if ok
                else "That is not a known worker.", "ok" if ok else "bad")
    h._redirect("/workers")


def workers_auto_update(h, form, csrf: str) -> None:
    on = form.get("on") == "1"
    workerupdate.set_auto(h.app.state_dir(), on)
    log.info("workers: automatic updates %s", "on" if on else "off")
    L.flash_set(csrf, "Workers will update by themselves when they are behind." if on else "Workers update only when you press Update worker.")
    h._redirect("/workers")


def workers_add(h, form, csrf: str) -> None:
    from ..workerapi import add_token
    cfg = h.app.cfg()
    if not cfg.workers.enabled:
        return _send_page(h, 400, "Workers", '<p class="muted">Turn workers on in Settings first.</p>', "/workers", csrf, kind="bad", section="workers")
    name, recipes = form.get("name", "").strip(), " ".join(form.get("recipes", "web").split()) or "web"
    if not re.fullmatch(r"[a-z]+( [a-z]+)*", recipes):
        return _send_page(h, 422, "Workers", "", "/workers", csrf, "Recipes are words like web, android, ios.", "bad", section="workers")
    try:
        token = add_token(cfg.workers.tokens_file, name)
    except ValueError as e:
        return _send_page(h, 422, "Workers", "", "/workers", csrf, str(e), "bad", section="workers")
    except OSError as e:
        return _send_page(h, 500, "Workers", "", "/workers", csrf, f"Could not write {cfg.workers.tokens_file}: {e.strerror}", "bad", section="workers")
    _send_page(h, 200, "Worker created", WK.created_page(cfg, name, token, recipes), "/workers", csrf, section="workers")


# ------------------------------------------------------------------ schedules
def _sched_form(h, csrf: str, v: dict, original: str, status: int = 200, flash=None, kind="ok", preview: str = "") -> None:
    cfg = h.app.cfg()
    cred = next((x.get("source", {}).get("token_file") for x in SC.raw_list(h.app.config_path) if x.get("name") == original), None)
    ok = bool(cred) and Path(cred).is_file() and Path(cred).stat().st_size > 0
    _send_page(h, status, "Edit schedule" if original else "New schedule", SC.form_page(cfg, v, csrf, original, ok, preview), "/schedules", csrf,
               flash, kind, section="schedules")


def schedules_get(h, q: dict, csrf: str) -> None:
    cfg, db = h.app.cfg(), h.app.ro_db()
    try:
        if h.path.split("?")[0] == "/schedules/edit":
            name = q.get("name", "")
            entry = next((x for x in SC.raw_list(h.app.config_path) if x.get("name") == name), None) if name else None
            if name and entry is None:
                return _send_page(h, 404, "Schedules", '<p class="muted">No such schedule.</p>', "/schedules", csrf, section="schedules")
            return _sched_form(h, csrf, SC.flat(entry, cfg), name)
        if h.path.split("?")[0] == "/schedules/view":
            body = SC.detail_page(cfg, db, h.app.state_dir(), q.get("name", ""), csrf)
            if body is None:
                return _send_page(h, 404, "Schedules", '<p class="muted">No such schedule.</p>', "/schedules", csrf, section="schedules")
            return _send_page(h, 200, q["name"], body, "/schedules", csrf, FLASH.get(q.get("ok", "")), section="schedules")
        _send_page(h, 200, "Schedules", SC.list_page(cfg, db, csrf), "/schedules", csrf, FLASH.get(q.get("ok", "")), section="schedules")
    finally:
        if db is not None:
            db.close()


def schedules_run(h, form, csrf: str) -> None:
    name = form.get("name", "")
    if name not in {s.name for s in h.app.cfg().schedules}:
        return h._send(404, "no such schedule", "text/plain")
    h.app.request_schedule_run(name)
    h._redirect("/schedules?ok=run")


def _sched_prepare(h, form):
    """(cfg, values, original name, existing raw entry, key) from a submitted form."""
    cfg, orig = h.app.cfg(), form.get("orig", "")
    existing = next((x for x in SC.raw_list(h.app.config_path) if x.get("name") == orig), None) if orig else None
    v = SC.from_form(form)
    if orig:
        v["name"] = orig
    key = (form.get("key") or "").strip()
    v["_key"] = key
    return cfg, v, orig, existing, key


def schedules_save(h, form, csrf: str) -> None:
    cfg, v, orig, existing, key = _sched_prepare(h, form)
    if orig and existing is None:
        return h._send(404, "no such schedule", "text/plain")
    try:
        SC.check_key(key)
        entry = SC.build(v, cfg, existing, not orig, {s.name for s in cfg.schedules})
        SC.store(h.app.config_path, h.app.state_dir(), orig or entry["name"], entry)
        if key:
            SC.save_credential(cfg, entry, key)
    except S.SettingsError as e:
        return _sched_form(h, csrf, v, orig, 422, " · ".join(e.messages), "bad")
    h._redirect(f"/schedules/view?name={entry['name']}&ok=sched_saved")


def schedules_test(h, form, csrf: str) -> None:
    cfg, v, orig, existing, key = _sched_prepare(h, form)
    try:
        entry = SC.build(v, cfg, existing, not orig, {s.name for s in cfg.schedules} | {orig})
    except S.SettingsError as e:
        return _sched_form(h, csrf, v, orig, 422, " · ".join(e.messages), "bad")
    if key:
        return _sched_form(h, csrf, v, orig, 200, preview=SC.preview_html(error="Save the schedule first so the new key is stored, then test."))
    try:
        data = sched.preview(entry["source"], time.time())
    except Exception as e:
        return _sched_form(h, csrf, v, orig, 200, preview=SC.preview_html(error=f"{type(e).__name__}: {str(e)[:300]}"))
    _sched_form(h, csrf, v, orig, 200, preview=SC.preview_html(data))


def schedules_delete(h, form, csrf: str) -> None:
    name = form.get("name", "")
    if not any(x.get("name") == name for x in SC.raw_list(h.app.config_path)):
        return h._send(404, "no such schedule", "text/plain")
    if form.get("confirm") != "1":
        return _send_page(h, 400, "Schedules", '<p class="muted">Tick the box to confirm the delete.</p>', "/schedules", csrf, kind="bad", section="schedules")
    try:
        SC.store(h.app.config_path, h.app.state_dir(), name, None)
    except S.SettingsError as e:
        return _send_page(h, 422, "Schedules", "", "/schedules", csrf, " · ".join(e.messages), "bad", section="schedules")
    h._redirect("/schedules?ok=sched_deleted")


# ------------------------------------------------------------------ scans
def _scan_page(h, csrf: str, title: str, body: str, status: int = 200, flash=None, kind="ok") -> None:
    _send_page(h, status, title, body, "/scans", csrf, flash, kind, section="scans")


def _smell_form(h, csrf: str, v: dict, original: str, status: int = 200, flash=None, kind="ok", preview: str = "", bad: str = "") -> None:
    _scan_page(h, csrf, "Edit smell" if original else "New smell", SN.smell_form_page(h.app.cfg(), v, csrf, original, preview, bad), status, flash, kind)


def scans_get(h, q: dict, csrf: str) -> None:
    cfg, path = h.app.cfg(), h.path.split("?")[0]
    if path == "/scans/smells/edit":
        sid = q.get("id", "")
        if not sid:
            return _smell_form(h, csrf, SN.smell_flat(None, cfg), "")
        entry = next((x for x in SN.raw_scanner(h.app.config_path).get("smells", []) if x.get("id") == sid), None)
        if entry is None and sid not in SN.sn.PRESETS:
            return _scan_page(h, csrf, "Smell library", '<p class="muted">No such smell.</p>', 404)
        return _smell_form(h, csrf, SN.smell_flat(entry, cfg, sid), sid)
    if path == "/scans/smells":
        return _scan_page(h, csrf, "Smell library", SN.smells_page(cfg, csrf), flash=FLASH.get(q.get("ok", "")))
    if path == "/scans/edit":
        name = q.get("name", "")
        entry = next((x for x in SN.raw_scanner(h.app.config_path).get("scans", []) if x.get("name") == name), None) if name else None
        if name and entry is None:
            return _scan_page(h, csrf, "Scans", '<p class="muted">No such scan.</p>', 404)
        return _scan_page(h, csrf, "Edit scan" if name else "New scan", SN.scan_form_page(cfg, SN.scan_flat(entry, cfg), csrf, name))
    db = h.app.ro_db()
    try:
        _scan_page(h, csrf, "Scans", SN.list_page(cfg, db, csrf), flash=FLASH.get(q.get("ok", "")))
    finally:
        if db is not None:
            db.close()


def scans_run(h, form, csrf: str) -> None:
    name = form.get("name", "")
    if name not in {s.name for s in h.app.cfg().scanner.scans}:
        return h._send(404, "no such scan", "text/plain")
    h.app.request_scan_run(name)
    h._redirect("/scans?ok=scan_run")


def scans_save(h, form, csrf: str) -> None:
    cfg, orig = h.app.cfg(), form.get("orig", "")
    existing = next((x for x in SN.raw_scanner(h.app.config_path).get("scans", []) if x.get("name") == orig), None) if orig else None
    if orig and existing is None:
        return h._send(404, "no such scan", "text/plain")
    v = SN.scan_from_form(form)
    if orig:
        v["name"] = orig
    try:
        entry = SN.build_scan(v, cfg, existing, {s.name for s in cfg.scanner.scans})
        SN.store(h.app.config_path, h.app.state_dir(), "scans", orig or entry["name"], entry)
    except S.SettingsError as e:
        return _scan_page(h, csrf, "Edit scan" if orig else "New scan", SN.scan_form_page(cfg, v, csrf, orig), 422, " · ".join(e.messages), "bad")
    h._redirect("/scans?ok=scan_saved")


def scans_delete(h, form, csrf: str) -> None:
    name = form.get("name", "")
    if not any(x.get("name") == name for x in SN.raw_scanner(h.app.config_path).get("scans", [])):
        return h._send(404, "no such scan", "text/plain")
    if form.get("confirm") != "1":
        return _scan_page(h, csrf, "Scans", '<p class="muted">Tick the box to confirm the delete.</p>', 400, kind="bad")
    try:
        SN.store(h.app.config_path, h.app.state_dir(), "scans", name, None)
    except S.SettingsError as e:
        return _scan_page(h, csrf, "Scans", "", 422, " · ".join(e.messages), "bad")
    h._redirect("/scans?ok=scan_deleted")


def _smell_prepare(h, form):
    """(cfg, values, original id, existing raw entry) from a submitted smell form."""
    cfg, orig = h.app.cfg(), form.get("orig", "")
    existing = next((x for x in SN.raw_scanner(h.app.config_path).get("smells", []) if x.get("id") == orig), None) if orig else None
    v = SN.smell_from_form(form)
    if orig:
        v["id"] = orig
        v["preset"] = "1" if orig in SN.sn.PRESETS else ""
    return cfg, v, orig, existing


def smells_save(h, form, csrf: str) -> None:
    cfg, v, orig, existing = _smell_prepare(h, form)
    try:
        if v["preset"]:
            if orig not in SN.sn.PRESETS:
                return h._send(404, "no such smell", "text/plain")
            advice = v["advice"].replace("\r\n", "\n").strip()
            if (why := SN.sn.advice_problem(advice)):
                raise S.SettingsError(["Advice is too long (max %d)." % SN.sn.MAX_ADVICE if "too long" in why else f"Advice {why}."])
            SN.store_advice(h.app.config_path, h.app.state_dir(), orig, advice)
        else:
            if orig and existing is None:
                return h._send(404, "no such smell", "text/plain")
            entry = SN.build_smell(v, existing, {x.id for x in cfg.scanner.smells})
            SN.store(h.app.config_path, h.app.state_dir(), "smells", orig or entry["id"], entry, "id")
    except S.SettingsError as e:
        return _smell_form(h, csrf, v, orig, 422, " · ".join(e.messages), "bad", bad=SN.invalid_field(e.messages))
    h._redirect("/scans/smells?ok=smell_saved")


def smells_sample(h, form, csrf: str) -> None:
    """Try the pattern on pasted text with the worker recipe: nothing is saved and no repository is read."""
    cfg, v, orig, existing = _smell_prepare(h, form)
    if (why := SN.pattern_problem(v["pattern"])):
        return _smell_form(h, csrf, v, orig, 422, why, "bad", bad="pattern")
    try:
        preview = SN.sample_html(SN.run_sample(v["pattern"], v["sample"].replace("\r\n", "\n")))
    except ValueError as e:
        preview = SN.sample_html(error=str(e))
    _smell_form(h, csrf, v, orig, 200, preview=preview)


def smells_delete(h, form, csrf: str) -> None:
    sid = form.get("id", "")
    if not any(x.get("id") == sid for x in SN.raw_scanner(h.app.config_path).get("smells", [])):
        return h._send(404, "no such smell", "text/plain")
    if form.get("confirm") != "1":
        return _scan_page(h, csrf, "Smell library", '<p class="muted">Tick the box to confirm the delete.</p>', 400, kind="bad")
    if (users := SN.smell_users(h.app.cfg(), sid)):
        return _scan_page(h, csrf, "Smell library", "", 422, f"{sid} is still used by: {', '.join(users)}. Take it out of those scans first.", "bad")
    try:
        SN.store(h.app.config_path, h.app.state_dir(), "smells", sid, None, "id")
    except S.SettingsError as e:
        return _scan_page(h, csrf, "Smell library", "", 422, " · ".join(e.messages), "bad")
    h._redirect("/scans/smells?ok=smell_deleted")


# ------------------------------------------------------------------ updates
def updates_get(h, q: dict, csrf: str) -> None:
    shown = L.flash_pop(csrf)
    _send_page(h, 200, "Updates", UP.page_body(h.app.cfg(), h.app.state_dir(), csrf), "/updates", csrf,
               shown[0] if shown else None, shown[1] if shown else "ok", section="updates")


def updates_status(h, q: dict, csrf: str) -> None:
    h._send(200, UP.live_body(h.app.state_dir(), csrf))


def updates_check(h, form, csrf: str) -> None:
    cfg, state = h.app.cfg(), h.app.state_dir()
    updates.refresh(state, cfg.updates.repo, token=h.app.update_token(cfg))
    got = updates._read(state).get("latest")
    if not got:
        L.flash_set(csrf, "Could not read the releases. Check the internet connection and that the factory's GitHub token can read the repository.", "bad")
    elif updates.available(state, version.current()):
        L.flash_set(csrf, f"{got['tag']} is available.")
    else:
        L.flash_set(csrf, f"You are up to date ({got['tag']} is the latest release).")
    h.app.refresh_update_notice()
    h._redirect("/updates")


def updates_apply(h, form, csrf: str) -> None:
    state = h.app.state_dir()
    got = updates.available(state, version.current())
    if not got or form.get("tag") != got["tag"]:
        L.flash_set(csrf, "That release is no longer the newest one. Check again.", "bad")
        return h._redirect("/updates")
    why = SU.start(state, got["tag"])
    log.info("updates: update to %s %s", got["tag"], "started" if not why else f"refused: {why}")
    L.flash_set(csrf, why or f"Updating to {got['tag']}. The factory restarts when it is done; this page may be unreachable for a minute.", "bad" if why else "ok")
    h._redirect("/updates")


def updates_auto(h, form, csrf: str) -> None:
    mode = form.get("mode", "")
    why = SU.set_auto(h.app.state_dir(), mode)
    log.info("updates: auto-update %s %s", mode, "set" if not why else f"refused: {why}")
    L.flash_set(csrf, why or {"off": "Automatic updates are off.", "patch": "Patch releases will install themselves every night.",
                              "all": "Every new release will install itself every night."}[mode], "bad" if why else "ok")
    h._redirect("/updates")


# ------------------------------------------------------------------ backup and restore
def _size(n: float) -> str:
    return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{max(1, int(n / 1024))} KB"


def _backup_page(h, csrf: str, status: int = 200, flash=None, kind="ok") -> None:
    state, cfg = h.app.state_dir(), h.app.cfg()
    try:
        size = _size(Path(cfg.db_path).stat().st_size)
    except OSError:
        size = "no database yet"
    last = B.last_backup(state)
    pending = (state / B.MARKER).exists()
    body = (
        f'<p class="muted">The database is {esc(size)}. Last backup downloaded: {esc(views.ago(last["at"])) if last else "never"}.</p>'
        + ('<p class="warn">A restore is staged and waits for the factory\'s next idle moment.</p>' if pending else "")
        + f'<form method="post" action="/backup/download" class="card">{views.csrf_field(csrf)}<h3>Download a backup</h3>'
          '<p>A zip with the database (tickets, runs, events, PRs, schedules, design files and images), <code>config.toml</code> and the settings overrides. '
          'It <strong>leaves out</strong> every credential file, the UI password, run output and logs, agent-written patches, and queued approvals and jobs. '
          'Keep it somewhere private anyway: it names your repositories, tickets and settings.</p><button>Download backup</button></form>'
        + f'<form method="post" action="/backup/restore" enctype="multipart/form-data" class="card">{views.csrf_field(csrf)}<h3>Restore from a backup</h3>'
          '<p><strong>This replaces this factory\'s database and both config files</strong> (the old config files are kept as <code>.bak</code>, '
          'the old database as <code>factory.db.pre-restore-&lt;time&gt;</code> next to it). Credentials are not in a backup: enter them again afterwards. '
          'The factory stays paused until you resume it.</p>'
          '<div class="field"><input type="file" name="file" accept=".zip" required></div>'
          '<div class="field"><label class="check"><input type="checkbox" name="confirm" value="1"> I understand this replaces the current data</label></div>'
          '<button>Restore</button></form>')
    _send_page(h, status, "Backup", body, "/backup", csrf, flash, kind, section="backup")


def backup_get(h, q: dict, csrf: str) -> None:
    _backup_page(h, csrf, flash=FLASH.get(q.get("ok", "")))


def backup_download(h, form, csrf: str) -> None:
    cfg, state = h.app.cfg(), h.app.state_dir()
    if not Path(cfg.db_path).exists():
        return _backup_page(h, csrf, 404, "The orchestrator has not created its database yet.", "bad")
    work = B.tmpdir(state, B.TMP_BACKUP)
    try:
        try:
            bundle = B.build_bundle(h.app.config_path, cfg.db_path, work)
        except (B.BackupError, sqlite3.Error, OSError) as e:
            return _backup_page(h, csrf, 500, f"The backup failed: {e}", "bad")
        h._send_file(bundle, "application/zip", time.strftime("shikumi-backup-%Y%m%d-%H%M%S.zip", time.gmtime()))
        B.record_backup(state, bundle.stat().st_size)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def backup_restore(h, fields: dict, csrf: str, body: Path, span, work: Path) -> None:
    """The upload is saved at `body` (its file part is `span`); the CSRF token has been checked. Nothing changes unless it all validates."""
    if fields.get("confirm") != "1":
        return _backup_page(h, csrf, 400, "Tick the box to confirm the restore.", "bad")
    if span is None or span[1] <= span[0]:
        return _backup_page(h, csrf, 400, "Choose a backup file.", "bad")
    zp = work / "upload.zip"
    B.copy_span(body, span, zp)
    try:
        B.stage_restore(h.app.config_path, h.app.state_dir(), zp)
    except B.BackupError as e:
        return _backup_page(h, csrf, 422, f"Not restored: {e}", "bad")
    h._redirect("/backup?ok=restore")


GET = {"/release": REL.release_get, "/updates": updates_get, "/updates/status": updates_status, "/backup": backup_get, "/floor/edit": FE.edit_get, "/schedules": schedules_get, "/schedules/view": schedules_get, "/schedules/edit": schedules_get, "/scans": scans_get, "/scans/edit": scans_get, "/scans/smells": scans_get, "/scans/smells/edit": scans_get, "/workers": workers_get, "/workers/job": workers_get, "/settings": settings_get, "/credentials": credentials_get, "/telegram": telegram_get, "/slack": slack_get, "/harnesses": harnesses_get,
       "/tickets": L.list_get, "/labels": L.list_get, "/labels/issue": L.issue_get, "/tickets/local/attachment": LT.download, "/ticket/review": RV.review_get, "/tickets/review": TRV.review_get, "/fragment/chat": CH.fragment_get, "/screens": SB.board_get, "/screens/edit": SB.edit_get, "/screens/captures": SB.captures_fragment, "/screens/canvas": SB.canvas_get, "/screens/review": RV.board_review_get}
POST = {"/prs/merge": L.merge_pr, "/release/bump": REL.bump_post, "/release/merge": REL.merge_post, "/updates/check": updates_check, "/updates/apply": updates_apply, "/updates/auto": updates_auto, "/backup/download": backup_download, "/floor/layout/save": FE.save, "/floor/layout/reset": FE.reset, "/mode/set": mode_set, "/workers/add": workers_add, "/workers/update": workers_update, "/workers/auto-update": workers_auto_update, "/schedules/run": schedules_run, "/schedules/save": schedules_save, "/schedules/test": schedules_test, "/schedules/delete": schedules_delete, "/scans/run": scans_run, "/scans/save": scans_save, "/scans/delete": scans_delete, "/scans/smells/save": smells_save, "/scans/smells/sample": smells_sample, "/scans/smells/delete": smells_delete,
        "/settings/save": settings_save, "/settings/feature": feature_set, "/settings/parallel": parallel_set, "/settings/projects": projects_save, "/classify/test": classify_test,
        "/credentials/save": credentials_save, "/harnesses/save": harnesses_save, "/harnesses/credential": harnesses_credential, "/tools/check": tools_check, "/tools/update": tools_update, "/credentials/test": credentials_test,
        "/telegram/save": telegram_save, "/telegram/detect": telegram_detect, "/telegram/use": telegram_use, "/telegram/test": telegram_test, "/slack/save": slack_save, "/slack/token": slack_token, "/slack/check": slack_check, "/slack/use": slack_use, "/slack/test": slack_test,
        "/tickets/start": L.start, "/tickets/create": L.create, "/tickets/close": L.close, "/tickets/local/comment": LT.comment, "/tickets/local/edit": LT.edit, "/tickets/local/state": LT.set_state, "/tickets/local/attachment/delete": LT.attachment_delete, "/tickets/import": L.import_issues, "/tickets/review/run": TRV.run_post, "/tickets/review/apply": TRV.apply_post, "/tickets/review/reject": TRV.reject_post, "/tickets/answer": L.answer, "/tickets/answer-all": L.answer_all, "/labels/add": L.add, "/labels/remove": L.remove, "/labels/replace": L.replace,
        "/tickets/chat/send": CH.send, "/tickets/chat/confirm": CH.confirm, "/tickets/chat/dismiss": CH.dismiss, "/review/add": RV.add, "/review/delete": RV.delete, "/review/send": RV.send, "/screens/refresh": SB.refresh, "/screens/capture": SB.capture, "/screens/captures/save": SB.captures_save, "/screens/save": SB.save, "/screens/delete": SB.delete, "/screens/issue": RV.board_issue}
POST_UPLOAD = {"/tickets/create": L.create, "/tickets/local/attach": LT.attach}      # multipart forms: handlers also get the files
