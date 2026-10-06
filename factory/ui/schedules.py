"""The Schedules pages: list, detail and the add/edit form for the scheduled jobs of factory/schedules.py.

Reading uses the orchestrator's database read-only. The only writes are the overrides file (through settings._commit, which validates the
merged config exactly as the orchestrator will load it), a credential file, and a "run now" request row the orchestrator picks up: the UI
holds no GitHub token and never opens a ticket itself. Everything a source returned is untrusted text and is escaped here."""
import copy
import json
import re
import sqlite3
import time
from pathlib import Path

from .. import schedules as sc
from ..config import deep_merge
from . import integrations as I
from . import settings as S
from .views import ago, badge, csrf_field, esc, ticket_link, ts

STATUS_BADGE = {"ok": "good", "skipped": "warn", "error": "bad", "new": ""}
LABEL_MODES = (("auto", "Let the classifier decide", "The analyst proposes changes. It builds only if the classifier is sure they help."),
               ("analyze", "Analysis only", "A person decides what happens next."),
               ("none", "Just open the ticket", "No labels. Nothing starts by itself."),
               ("custom", "These labels", "Comma separated, in the box below."))
PREVIEW_CHARS = 6000
INTERVAL_HELP = "30m, 6h, 2d or 1w. At least 5 minutes."


# ---------------------------------------------------------------- reading

def raw_list(cfg_path: str) -> list[dict]:
    """The schedules as configured now (hand-written file with the UI's overrides on top), as raw dicts."""
    return copy.deepcopy(deep_merge(S.base_raw(cfg_path), S.overrides_raw(cfg_path)).get("schedules", []))


def read_state(db) -> tuple[dict, set]:
    """({name: state}, names with a run-now request). Empty when the orchestrator has not created the tables yet."""
    if db is None:
        return {}, set()
    try:
        names = {r[0] for r in db.execute("SELECT name FROM schedule_state")}
        return {n: sc.get_state(db, n) for n in names}, sc.requested(db)
    except sqlite3.OperationalError:
        return {}, set()


def when_text(s) -> str:
    return f"every {s.every}" if s.every else f"cron {s.cron}"


def in_text(t: float | None, now: float) -> str:
    if t is None:
        return "—"
    d = int(t - now)
    if d <= 0:
        return "due"
    return f"in {d // 60 + 1} min" if d < 3600 else f"in {d // 3600} h {(d % 3600) // 60} min" if d < 172800 else f"in {d // 86400} d {(d % 86400) // 3600} h"


def source_text(src: dict) -> tuple[str, str]:
    if src.get("type") == "umami":
        return "umami", f"{src.get('days', 7)} days, {len(src.get('metrics', ['path', 'referrer', 'browser', 'device', 'country']))} metrics"
    host = re.sub(r"^https://([^/]+).*$", r"\1", str(src.get("url", "")))
    return str(src.get("type", "")), host


def last_run_cell(state: dict | None, now: float) -> str:
    if not state:
        return '<span class="muted">not seen yet</span>'
    if state["status"] == "new":
        return '<span class="muted">clock started</span>'
    label = f"error {state['fails']}/{sc.MAX_RETRIES}" if state["status"] == "error" and state["retry_at"] else state["status"]
    return f'{badge(label, STATUS_BADGE.get(state["status"], ""))}<br><span class="muted">{esc(ago(state["last_run"], now))}</span>'


# ---------------------------------------------------------------- list

def list_page(cfg, db, csrf: str, now: float | None = None) -> str:
    now = time.time() if now is None else now
    states, asked = read_state(db)
    rows, soonest, failing = "", None, 0
    for s in cfg.schedules:
        st = states.get(s.name)
        nxt = sc.next_run(s, st, now)
        if nxt is not None and (soonest is None or nxt < soonest[0]):
            soonest = (nxt, s.name)
        failing += bool(st and st["status"] == "error" and st["retry_at"])
        kind, extra = source_text(s.source)
        if not s.enabled:
            when = f'{esc(when_text(s))}<br><span class="muted">off</span>'
        elif st and st["retry_at"]:
            when = f'{esc(when_text(s))}<br><span class="muted">retry {esc(in_text(st["retry_at"], now))}</span>'
        else:
            when = f'{esc(when_text(s))}<br><span class="muted">{esc(in_text(nxt, now) if nxt else "starts when first seen")}</span>'
        ticket = (f'{ticket_link(s.repo, st["issue"])}' if st and st["issue"] else '<span class="muted">none yet</span>')
        action = ('<span class="muted">queued</span>' if s.name in asked else
                  f'<form method="post" action="/schedules/run" class="inline">{csrf_field(csrf)}<input type="hidden" name="name" value="{esc(s.name)}">'
                  '<button type="submit">Run now</button></form>')
        rows += (f'<tr><td data-l="Schedule"><a href="/schedules/view?name={esc(s.name)}">{esc(s.name)}</a>'
                 f'{"" if s.enabled else " " + badge("off", "")}<br><span class="muted">{esc(s.repo)}</span></td>'
                 f'<td data-l="Source">{esc(kind)}<br><span class="muted">{esc(extra)}</span></td><td data-l="Runs">{when}</td>'
                 f'<td data-l="Last run">{last_run_cell(st, now)}</td><td data-l="Ticket">{ticket}</td><td data-l="">{action}</td></tr>')
    on = sum(1 for s in cfg.schedules if s.enabled)
    cards = ('<div class="cards">'
             f'<div class="card"><h3>Active</h3><strong>{on}</strong> <span class="muted">of {len(cfg.schedules)}</span></div>'
             f'<div class="card"><h3>Next run</h3><strong>{esc(in_text(soonest[0], now)) if soonest else "—"}</strong>'
             f'<br><span class="muted">{esc(soonest[1]) if soonest else "nothing scheduled"}</span></div>'
             f'<div class="card"><h3>Needs attention</h3><strong class="{"bad-text" if failing else ""}">{failing}</strong>'
             f'<br><span class="muted">{"failing, being retried" if failing else "all well"}</span></div>'
             f'<div class="card"><h3>Mode</h3><strong>{"dry-run" if cfg.dry_run else "LIVE"}</strong>'
             f'<br><span class="muted">{"timed runs are skipped" if cfg.dry_run else "timed runs open tickets"}</span></div></div>')
    head = ('<p class="muted">Fetch data on a timer, save a snapshot, and open a ticket for the factory to act on. '
            '<a href="/schedules/edit">New schedule</a></p>')
    if not cfg.schedules:
        return head + ('<p>No schedules yet. A schedule opens a ticket on a repository on a timer, carrying fresh data (for example last '
                       'week\'s analytics) for the analyst to review. <a href="/schedules/edit">Add the first one</a>, or see <code>docs/schedules.md</code>.</p>')
    table = ('<div class="scroll"><table class=stack><thead><tr><th>Schedule</th><th>Source</th><th>Runs</th><th>Last run</th><th>Ticket</th><th></th></tr></thead>'
             f'<tbody>{rows}</tbody></table></div>')
    return (head + cards + "<h2>All schedules</h2>" + table +
            '<p class="muted">Changes are saved to the overrides file and applied when the factory is next idle. '
            '<strong>Run now</strong> ignores dry-run and the timer, and opens a real ticket within a poll.</p>')


# ---------------------------------------------------------------- detail

def detail_page(cfg, db, state_dir: Path, name: str, csrf: str, now: float | None = None) -> str | None:
    now = time.time() if now is None else now
    s = next((x for x in cfg.schedules if x.name == name), None)
    if s is None:
        return None
    states, asked = read_state(db)
    st = states.get(name)
    nxt = sc.next_run(s, st, now)
    kind, extra = source_text(s.source)
    labels = ", ".join(f"<code>{esc(l)}</code>" for l in (s.labels if s.labels is not None else (cfg.auto_label,))) or '<span class="muted">none</span>'
    cred = ""
    if s.source.get("token_file"):
        p = Path(s.source["token_file"])
        ok = p.is_file() and p.stat().st_size > 0
        cred = f'<tr><th>Credential</th><td><code>{esc(p.name)}</code> {badge("saved" if ok else "missing", "good" if ok else "bad")}</td></tr>'
    meta = ('<table class="meta">'
            f'<tr><th>Repository</th><td>{esc(s.repo)}</td></tr>'
            f'<tr><th>Runs</th><td>{esc(when_text(s))} · {"off" if not s.enabled else esc("next " + in_text(nxt, now)) if nxt else "starts when first seen"}</td></tr>'
            f'<tr><th>Source</th><td>{esc(kind)} · {esc(extra)}</td></tr>'
            f'<tr><th>Labels</th><td>{labels}</td></tr>{cred}'
            f'<tr><th>Instructions</th><td class="wrap muted">{esc(s.instructions or "(general review)")}</td></tr></table>')
    actions = ('<p>' + ('<span class="muted">Run requested; the factory runs it within a poll.</span>' if name in asked else
               f'<form method="post" action="/schedules/run" class="inline">{csrf_field(csrf)}<input type="hidden" name="name" value="{esc(name)}">'
               '<button type="submit">Run now</button></form>')
               + f' <a href="/schedules/edit?name={esc(name)}">Edit</a></p>')
    hist = []
    if db is not None:
        try:
            hist = sc.history(db, name, 20)
        except sqlite3.OperationalError:
            pass
    if hist:
        body = "".join(
            f'<tr><td data-l="When">{esc(ago(h["at"], now))}<br><span class="muted">{esc(ts(h["at"]))}</span></td>'
            f'<td data-l="Result">{badge(h["status"], STATUS_BADGE.get(h["status"], ""))}{" <span class=muted>manual</span>" if h["manual"] else ""}</td>'
            f'<td data-l="Ticket">{ticket_link(s.repo, h["issue"]) if h["issue"] else "—"}</td>'
            f'<td data-l="Detail" class="wrap">{esc(h["detail"])}</td><td data-l="Snapshot">{esc(h["snapshot"] or "—")}</td></tr>' for h in hist)
        history = ('<h2>History</h2><div class="scroll"><table class=stack><thead><tr><th>When</th><th>Result</th><th>Ticket</th><th>Detail</th><th>Snapshot</th></tr></thead>'
                   f'<tbody>{body}</tbody></table></div>')
    else:
        history = '<h2>History</h2><p class="muted">No runs yet.</p>'
    snap = ""
    files = sc.snapshots(state_dir, name)
    if files:
        text = files[0].read_text(errors="replace")[:PREVIEW_CHARS]
        snap = (f'<h2>Latest snapshot</h2><p class="muted">{esc(files[0].name)} · {files[0].stat().st_size // 1024 + 1} KB · untrusted data from the source</p>'
                f'<pre>{esc(text)}</pre>')
    return f'<p><a href="/schedules">← all schedules</a></p>{actions}{meta}{history}{snap}'


# ---------------------------------------------------------------- the form

def flat(entry: dict | None, cfg) -> dict:
    """An entry (a raw schedule dict) as the form's flat field values."""
    e = entry or {}
    src = e.get("source", {})
    labels = e.get("labels")
    analyst = next((r.label for r in cfg.roles if r.name == "analyst"), "factory:analyze")
    mode = "auto" if labels is None else "none" if labels == [] else "analyze" if labels == [analyst] else "custom"
    return {"name": e.get("name", ""), "repo": e.get("repo", cfg.repos[0] if cfg.repos else ""),
            "when": "cron" if e.get("cron") else "every", "every": e.get("every", "7d"), "cron": e.get("cron", "0 9 * * 1"),
            "stype": src.get("type", "umami"), "base_url": src.get("base_url", "https://api.umami.is/v1"), "auth": src.get("auth", "api-key"),
            "website_id": src.get("website_id", ""), "days": str(src.get("days", 7)),
            "metrics": list(src.get("metrics", ["path", "referrer", "browser", "device", "country"])),
            "url": src.get("url", ""), "header": src.get("header", "Authorization"), "prefix": src.get("prefix", "Bearer "),
            "title": e.get("title", "Scheduled review: ${name} ${date}"), "instructions": e.get("instructions", ""),
            "labels": mode, "keep_labels": ", ".join(labels or []),
            "skip_if_open": "1" if e.get("skip_if_open", True) else "", "enabled": "1" if e.get("enabled", True) else "",
            "keep": str(e.get("keep", 30)), "max_data_chars": str(e.get("max_data_chars", 30000))}


def from_form(form) -> dict:
    v = {k: str(form.get(k, "")) for k in ("name", "repo", "when", "every", "cron", "stype", "base_url", "auth", "website_id", "days", "url",
                                           "header", "prefix", "title", "instructions", "labels", "keep_labels", "skip_if_open", "enabled",
                                           "keep", "max_data_chars")}
    v["metrics"] = form.getall("metric")
    return v


def _field(label: str, control: str, hint: str = "") -> str:
    return f'<div class="field"><label>{esc(label)}</label>{control}{f"<div class=muted>{esc(hint)}</div>" if hint else ""}</div>'


def _input(name: str, v: dict, hint: str = "", label: str = "", kind: str = "text", ph: str = "", extra: str = "") -> str:
    shown = "" if kind == "password" else esc(v.get(name, ""))
    return (f'<div class="field"><label for="f_{name}">{esc(label)}</label><input id="f_{name}" name="{name}" type="{kind}" value="{shown}"'
            f'{f" placeholder=\"{esc(ph)}\"" if ph else ""} {extra} autocomplete="off">{f"<div class=muted>{esc(hint)}</div>" if hint else ""}</div>')


def _radio(name: str, value: str, label: str, current: str, hint: str = "") -> str:
    return (f'<label class="check"><input type="radio" name="{name}" value="{esc(value)}"{" checked" if current == value else ""}> '
            f'<span>{esc(label)}{f"<br><span class=muted>{esc(hint)}</span>" if hint else ""}</span></label>')


def form_page(cfg, v: dict, csrf: str, original: str = "", cred_ok: bool = False, preview: str = "") -> str:
    new = not original
    analyst = next((r.label for r in cfg.roles if r.name == "analyst"), "factory:analyze")
    repos = "".join(f'<option value="{esc(r)}"{" selected" if r == v["repo"] else ""}>{esc(r)}</option>' for r in cfg.repos)
    metrics = "".join(f'<label class="check"><input type="checkbox" name="metric" value="{m}"{" checked" if m in v["metrics"] else ""}> {m}</label>'
                      for m in sorted(sc.UMAMI_METRICS))
    modes = list(LABEL_MODES)
    labels = "".join(_radio("labels", k, t + (f" ({cfg.auto_label})" if k == "auto" else f" ({analyst})" if k == "analyze" else ""), v["labels"], h) for k, t, h in modes)
    labels += _input("keep_labels", v, "For These labels: for example factory:analyze, priority: low.", "Labels")
    key_hint = ("A key is already saved. Leave empty to keep it, or type a new one to replace it." if cred_ok else
                "Stored in a file only the factory can read, never shown again.")
    body = (f'<p><a href="{"/schedules" if new else "/schedules/view?name=" + esc(original)}">← {"all schedules" if new else esc(original)}</a></p>'
            f'<form method="post" action="/schedules/save" class="settings">{csrf_field(csrf)}<input type="hidden" name="orig" value="{esc(original)}">'
            '<fieldset><legend>What and where</legend>'
            + (_input("name", v, "Lowercase letters, digits and dashes. Names the snapshot folder.", "Name", extra="required maxlength=41") if new else
               f'<div class="field"><label>Name</label><code>{esc(original)}</code><input type="hidden" name="name" value="{esc(original)}"></div>')
            + _field("Repository", f'<select name="repo">{repos}</select>', "Only repositories the factory already handles.")
            + '</fieldset><fieldset><legend>When</legend>'
            + _radio("when", "every", "Every", v["when"]) + _radio("when", "cron", "On a cron schedule (UTC)", v["when"])
            + _input("every", v, INTERVAL_HELP + " The first run is one interval after saving; use Run now to go sooner.", "Interval")
            + _input("cron", v, "Five fields: minute hour day-of-month month weekday. 0 9 * * 1 is Mondays 09:00 UTC.", "Cron")
            + '</fieldset><fieldset><legend>Data source</legend>'
            + _radio("stype", "umami", "Umami", v["stype"]) + _radio("stype", "http", "HTTPS JSON endpoint", v["stype"])
            + '<h3>Umami</h3>' + _input("base_url", v, "Cloud: https://api.umami.is/v1. Self-hosted: https://your-host/api", "Base URL")
            + _field("Auth", f'<select name="auth"><option value="api-key"{" selected" if v["auth"] == "api-key" else ""}>API key (Umami Cloud)</option>'
                     f'<option value="bearer"{" selected" if v["auth"] == "bearer" else ""}>Bearer token (self-hosted)</option></select>')
            + _input("website_id", v, "", "Website ID") + _input("days", v, "1 to 365. The previous period is fetched too, for comparison.", "Days covered")
            + f'<div class="field"><label>Metrics to include</label>{metrics}</div>'
            + '<h3>HTTPS JSON endpoint</h3>' + _input("url", v, "Must start with https://. Fetched with GET.", "URL")
            + _input("header", v, "Header that carries the credential.", "Credential header") + _input("prefix", v, "Put before the key, for example “Bearer ”.", "Credential prefix")
            + _input("key", {}, key_hint, "API key / token", "password", extra="maxlength=500")
            + '</fieldset><fieldset><legend>The ticket</legend>'
            + _input("title", v, "${date} and ${name} are filled in.", "Title")
            + _field("Instructions for the agent", f'<textarea name="instructions" rows="4" maxlength="4000">{esc(v["instructions"])}</textarea>', "Leave empty for a general review.")
            + f'<div class="field"><label>What happens after the ticket opens</label>{labels}</div>'
            + f'<label class="check"><input type="checkbox" name="skip_if_open" value="1"{" checked" if v["skip_if_open"] else ""}> Skip a run while the previous ticket is still open</label>'
            + _input("max_data_chars", v, "Of the fetched data, put in the ticket.", "Most data in the ticket (characters)", "number")
            + _input("keep", v, "Snapshots of the fetched data kept on disk for this schedule. 0 keeps none.", "Snapshots kept", "number")
            + '</fieldset>'
            f'<label class="check"><input type="checkbox" name="enabled" value="1"{" checked" if v["enabled"] else ""}> Enabled</label>'
            '<p><button type="submit">Save schedule</button> '
            '<button type="submit" formaction="/schedules/test" class="secondary">Test fetch</button> '
            '<span class="muted">Test fetch checks the source and the saved credential and shows the data. It opens no ticket and saves nothing.</span></p></form>')
    if preview:
        body += preview
    if not new:
        body += ('<h2>Delete</h2><form method="post" action="/schedules/delete" class="settings">' + csrf_field(csrf) +
                 f'<input type="hidden" name="name" value="{esc(original)}"><label class="check danger"><input type="checkbox" name="confirm" value="1"> '
                 f'Delete {esc(original)}. Snapshots already on disk are kept.</label><button type="submit">Delete schedule</button></form>')
    return body


def preview_html(data=None, error: str = "") -> str:
    if error:
        return f'<h2>Test fetch</h2><div class="flash bad" role="alert">{esc(error)}</div>'
    text = json.dumps(data, indent=1, sort_keys=True)
    cut = len(text) > PREVIEW_CHARS
    return (f'<h2>Test fetch</h2><p class="muted">The source answered. This is untrusted data and was not saved.{" Shortened here." if cut else ""}</p>'
            f'<pre>{esc(text[:PREVIEW_CHARS])}</pre>')


# ---------------------------------------------------------------- saving

def secrets_dir(cfg) -> Path:
    return Path(cfg.runner.claude_env_file).parent


def build(v: dict, cfg, existing: dict | None, new: bool, taken: set[str]) -> dict:
    """The raw schedule dict for a submitted form, keeping every key of `existing` the form does not own. Raises SettingsError."""
    errs: list[str] = []
    name = v["name"].strip()
    if not sc.NAME.fullmatch(name):
        errs.append("Name: lowercase letters, digits and dashes, starting with a letter or digit.")
    elif new and name in taken:
        errs.append(f"Name: there is already a schedule called {name}.")
    if v["repo"] not in cfg.repos:
        errs.append("Repository: choose one the factory handles.")
    e = {k: x for k, x in (existing or {}).items() if k not in ("every", "cron")}
    e.update({"name": name, "repo": v["repo"], "title": v["title"].strip(), "enabled": v["enabled"] == "1", "skip_if_open": v["skip_if_open"] == "1"})
    if v["when"] == "cron":
        e["cron"] = v["cron"].strip()
        if sc.parse_cron(e["cron"]) is None:
            errs.append("Cron: five fields (minute hour day-of-month month weekday), UTC.")
    else:
        e["every"] = v["every"].strip()
        if sc.parse_every(e["every"]) is None or sc.parse_every(e["every"]) < 300:
            errs.append(f"Interval: {INTERVAL_HELP}")
    instructions = v["instructions"].replace("\r\n", "\n").strip()
    if len(instructions) > 4000:
        errs.append("Instructions: at most 4000 characters.")
    if instructions:
        e["instructions"] = instructions
    else:
        e.pop("instructions", None)
    analyst = next((r.label for r in cfg.roles if r.name == "analyst"), "factory:analyze")
    mode = v["labels"]
    if mode == "auto":
        e.pop("labels", None)
    elif mode == "analyze":
        e["labels"] = [analyst]
    elif mode == "none":
        e["labels"] = []
    elif mode == "custom":
        chosen = [x.strip() for x in v["keep_labels"].split(",") if x.strip()]
        if not chosen or len(chosen) > 10 or any(len(x) > 50 for x in chosen):
            errs.append("Labels: one to ten labels, comma separated, each at most 50 characters.")
        e["labels"] = chosen
    elif mode != "keep":
        errs.append("Labels: choose what happens after the ticket opens.")
    for k, lo, hi, what in (("keep", 0, 1000, "Snapshots kept: a whole number from 0 to 1000."),
                            ("max_data_chars", 0, 1000000, "Most data in the ticket: a whole number from 0 to 1000000.")):
        raw = v.get(k, "").strip()
        if not raw:
            continue
        if not raw.isdigit() or not lo <= int(raw) <= hi:
            errs.append(what)
        else:
            e[k] = int(raw)
    old = (existing or {}).get("source", {})
    token_file = old.get("token_file") or str(secrets_dir(cfg) / f"schedule-{name or 'new'}")
    if v["stype"] == "umami":
        days = int(v["days"]) if v["days"].strip().isdigit() else 0
        src = {**(old if old.get("type") == "umami" else {}), "type": "umami", "base_url": v["base_url"].strip(), "auth": v["auth"],
               "website_id": v["website_id"].strip(), "days": days, "metrics": [m for m in v["metrics"] if m in sc.UMAMI_METRICS],
               "token_file": token_file}
        if not src["metrics"]:
            errs.append("Metrics: pick at least one.")
    elif v["stype"] == "http":
        src = {**(old if old.get("type") == "http" else {}), "type": "http", "url": v["url"].strip(), "header": v["header"].strip() or "Authorization",
               "prefix": v["prefix"]}
        if old.get("token_file") or v.get("_key"):
            src["token_file"] = token_file
    else:
        errs.append("Source: choose Umami or an HTTPS endpoint.")
        src = {}
    if src and (why := sc.source_problem(src)):
        errs.append(why[0].upper() + why[1:] + ".")
    e["source"] = src
    if errs:
        raise S.SettingsError(errs)
    return e


def store(cfg_path: str, state_dir: Path, name: str, entry: dict | None) -> None:
    """Replace (or add, or with entry=None remove) one schedule. The whole `schedules` list is kept in the overrides file, because a list
    in the overrides replaces the config file's list: the first save copies the hand-written ones across."""
    current = raw_list(cfg_path)
    out, hit = [], False
    for x in current:
        if x.get("name") == name:
            hit = True
            if entry is not None:
                out.append(entry)
        else:
            out.append(x)
    if not hit and entry is not None:
        out.append(entry)
    base, new_ov = S.base_raw(cfg_path), copy.deepcopy(S.overrides_raw(cfg_path))
    if out == base.get("schedules", []):
        new_ov.pop("schedules", None)
    elif out:
        new_ov["schedules"] = out
    else:
        new_ov["schedules"] = []
    S._commit(cfg_path, state_dir, new_ov)


def save_credential(cfg, entry: dict, key: str) -> None:
    I.write_secret(Path(entry["source"]["token_file"]), key)


def check_key(key: str) -> None:
    if key and not I.SECRET_RE.match(key):
        raise S.SettingsError(["API key: 8 to 500 characters, no spaces or line breaks."])
