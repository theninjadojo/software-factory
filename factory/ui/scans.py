"""The Scans pages: the scans and the smell library of factory/scanner.py, and the form to add, edit and delete them.

Reading uses the orchestrator's database read-only. The only writes are the overrides file (through settings._commit, which validates the
merged config exactly as the orchestrator will load it) and a "run now" request row the orchestrator picks up: the UI holds no GitHub token
and never opens a ticket itself. A smell's pattern is checked here with the strict subset (scanner.regex_problem) and tried only by the
worker recipe in a throw-away folder, with its per-file time limit."""
import copy
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .. import scanner as sn
from .. import schedules as sc
from ..config import deep_merge
from . import settings as S
from .schedules import STATUS_BADGE, in_text, last_run_cell, when_text, _field, _input, _radio
from .views import badge, csrf_field, esc

RECIPE = Path(__file__).resolve().parents[2] / "worker" / "recipes" / "smell-scan.py"
INTERVAL_HELP = "6h, 2d or 1w. At least 1 hour."
SAMPLE_CHARS = 20000
SAMPLE_SHOWN = 10
SAMPLE_SECONDS = 10


# ---------------------------------------------------------------- reading

def raw_scanner(cfg_path: str) -> dict:
    """The [scanner] table as configured now (hand-written file with the UI's overrides on top), as raw dicts."""
    return copy.deepcopy(deep_merge(S.base_raw(cfg_path), S.overrides_raw(cfg_path)).get("scanner", {}))


def read_state(db) -> tuple[dict, set]:
    """({name: state}, names with a run-now request). Empty when the orchestrator has not created the tables yet."""
    if db is None:
        return {}, set()
    try:
        names = {r[0] for r in db.execute("SELECT name FROM scan_state")}
        return {n: sn.get_state(db, n) for n in names}, sn.requested(db)
    except sqlite3.OperationalError:
        return {}, set()


def smell_label(cfg, sid: str) -> str:
    return esc(sid) + ("" if sid in sn.PRESETS else " <span class=muted>(yours)</span>")


# ---------------------------------------------------------------- list

def tabs(active: str) -> str:
    link = lambda href, text, key: f'<a href="{href}"{" aria-current=page" if key == active else ""}>{esc(text)}</a>'
    return f'<p class="muted">{link("/scans", "Scans", "scans")} · {link("/scans/edit", "New scan", "new")} · {link("/scans/smells", "Smell library", "smells")}</p>'


def toggle_html(cfg, csrf: str) -> str:
    """The master switch. Off: the banner says why nothing runs and offers to turn scans on (only when workers are on; else it links to Workers).
    On: a one-line state with a button to turn them off."""
    def form(want: str, label: str) -> str:
        return (f'<form method="post" action="/scans/enable" class="inline">{csrf_field(csrf)}<input type="hidden" name="enabled" value="{want}">'
                f'<button type="submit">{label}</button></form>')
    if cfg.scanner.enabled:
        return (f'<p class="muted" role="status">Scans are <strong>on</strong>. They need a worker with the <code>smell-scan</code> recipe. {form("0", "Turn scans off")}</p>')
    if not cfg.workers.enabled:
        return ('<p class="flash bad" role="status">Scans are switched off, and they need workers: <a href="/workers">turn on workers</a> first, '
                'then come back here. Nothing below runs until then.</p>')
    return ('<p class="flash bad" role="status">Scans are switched off. Nothing below runs until you turn them on; they also need a worker with the '
            f'<code>smell-scan</code> recipe. A scan that has just been added waits for its first due time. {form("1", "Turn scans on")}</p>')


def list_page(cfg, db, csrf: str, now: float | None = None) -> str:
    now = time.time() if now is None else now
    states, asked = read_state(db)
    scans = cfg.scanner.scans
    rows, soonest, failing = "", None, 0
    for s in scans:
        st = states.get(s.name)
        nxt = sc.next_run(s, st, now)
        if nxt is not None and s.enabled and (soonest is None or nxt < soonest[0]):
            soonest = (nxt, s.name)
        failing += bool(st and st["status"] == "error" and st["retry_at"])
        if not s.enabled:
            when = f'{esc(when_text(s))}<br><span class="muted">off</span>'
        elif st and st["retry_at"]:
            when = f'{esc(when_text(s))}<br><span class="muted">retry {esc(in_text(st["retry_at"], now))}</span>'
        else:
            when = f'{esc(when_text(s))}<br><span class="muted">{esc(in_text(nxt, now) if nxt else "starts when first seen")}</span>'
        action = ('<span class="muted">queued</span>' if s.name in asked else
                  f'<form method="post" action="/scans/run" class="inline">{csrf_field(csrf)}<input type="hidden" name="name" value="{esc(s.name)}">'
                  '<button type="submit">Run now</button></form>')
        rows += (f'<tr><td data-l="Scan"><a href="/scans/edit?name={esc(s.name)}">{esc(s.name)}</a>'
                 f'{"" if s.enabled else " " + badge("off", "")}<br><span class="muted">{esc(s.repo)}</span></td>'
                 f'<td data-l="Smells">{", ".join(smell_label(cfg, x) for x in s.smells)}</td><td data-l="Runs">{when}</td>'
                 f'<td data-l="Last run">{last_run_cell(st, now)}</td><td data-l="">{action}</td></tr>')
    on = sum(1 for s in scans if s.enabled)
    cards = ('<div class="cards">'
             f'<div class="card"><h3>Active</h3><strong>{on}</strong> <span class="muted">of {len(scans)}</span></div>'
             f'<div class="card"><h3>Next run</h3><strong>{esc(in_text(soonest[0], now)) if soonest else "—"}</strong>'
             f'<br><span class="muted">{esc(soonest[1]) if soonest else "nothing scheduled"}</span></div>'
             f'<div class="card"><h3>Needs attention</h3><strong class="{"bad-text" if failing else ""}">{failing}</strong>'
             f'<br><span class="muted">{"failing, being retried" if failing else "all well"}</span></div>'
             f'<div class="card"><h3>Mode</h3><strong>{"dry-run" if cfg.dry_run else "LIVE"}</strong>'
             f'<br><span class="muted">{"scans do not run" if cfg.dry_run else "scans open tickets"}</span></div></div>')
    head = ('<p class="muted">Look for code smells on a timer and open one ticket per smell, with a recommended fix. '
            '<a href="/scans/edit">New scan</a> · <a href="/scans/smells">Smell library</a></p>')
    off = toggle_html(cfg, csrf)
    if not scans:
        return head + off + ('<p>No scans yet. A scan reads a repository on a timer and opens one ticket per smell, each with a recommended fix. '
                             '<a href="/scans/edit">Add the first one</a>, or see <code>docs/scanner.md</code>.</p>')
    table = ('<div class="scroll"><table class=stack><thead><tr><th>Scan</th><th>Smells</th><th>Runs</th><th>Last run</th><th></th></tr></thead>'
             f'<tbody>{rows}</tbody></table></div>')
    return (head + off + cards + "<h2>All scans</h2>" + table +
            '<p class="muted">Changes are saved to the overrides file and applied when the factory is next idle. Scans do not run in dry-run or while paused. '
            '<strong>Run now</strong> skips the timer and starts the scan at the next poll.</p>')


# ---------------------------------------------------------------- the smell library

def smells_page(cfg, csrf: str) -> str:
    rows = ""
    for sid, (name, desc, rule) in sn.smell_table(cfg.scanner).items():
        preset = sid in sn.PRESETS
        what = f'<code>{esc(rule["pattern"])}</code>' if "pattern" in rule else esc(desc)
        files = esc(", ".join(rule.get("globs", []))) if "pattern" in rule else '<span class="muted">—</span>'
        rows += (f'<tr><td data-l="Name"><a href="/scans/smells/edit?id={esc(sid)}">{esc(sid)}</a><br><span class="muted">{esc(name)}</span></td>'
                 f'<td data-l="Kind">{"preset" if preset else "yours"}</td><td data-l="Pattern or rule" class="wrap">{what}</td><td data-l="Files">{files}</td>'
                 f'<td data-l="Fix advice">{"yes" if sn.advice_for(cfg.scanner, sid) else "no"}</td>'
                 f'<td data-l=""><a href="/scans/smells/edit?id={esc(sid)}">Edit</a></td></tr>')
    own = "" if cfg.scanner.smells else '<p class="muted">Only the built-in smells so far. Add your own.</p>'
    return (tabs("smells") + '<p><a href="/scans/smells/edit">New smell</a></p>'
            '<div class="scroll"><table class=stack><thead><tr><th>Name</th><th>Kind</th><th>Pattern or rule</th><th>Files</th><th>Fix advice</th><th></th></tr></thead>'
            f'<tbody>{rows}</tbody></table></div>{own}'
            '<p class="muted">Presets cannot be changed, but they can carry a recommended fix. Your own smells are a regular expression matched per line.</p>')


def smell_flat(entry: dict | None, cfg, sid: str = "") -> dict:
    """A smell (a raw dict, or a preset id with its advice) as the form's field values."""
    if sid in sn.PRESETS:
        return {"id": sid, "pattern": "", "globs": "", "advice": sn.advice_for(cfg.scanner, sid), "sample": "", "preset": "1"}
    e = entry or {}
    return {"id": e.get("id", ""), "pattern": e.get("pattern", ""), "globs": ", ".join(e.get("globs", ["**/*"])), "advice": e.get("advice", ""), "sample": "", "preset": ""}


def smell_from_form(form) -> dict:
    return {k: str(form.get(k, "")) for k in ("id", "pattern", "globs", "advice", "sample", "preset")}


def smell_form_page(cfg, v: dict, csrf: str, original: str = "", preview: str = "", bad: str = "") -> str:
    """`bad` names the field to mark invalid (it gets a red border and aria-invalid)."""
    preset = bool(v["preset"])
    new = not original
    mark = lambda f: ' aria-invalid="true"' if bad == f else ""
    fix = _field("Recommended fix (shown on every ticket)",
                 f'<textarea name="advice" rows="4" maxlength="{sn.MAX_ADVICE}"{mark("advice")}>{esc(v["advice"])}</textarea>',
                 "Written by an admin, so shown as trusted. The analyst also proposes a fix for each finding.")
    if preset:
        top = (f'<div class="field"><label>Name</label><code>{esc(original)}</code> <span class="muted">preset: its rule cannot be changed, only its advice</span>'
               f'<input type="hidden" name="id" value="{esc(original)}"><input type="hidden" name="preset" value="1"></div>')
        fields = top + fix
        buttons = '<p><button type="submit">Save smell</button></p>'
    else:
        top = (_input("id", v, "Lower-case letters, digits and dashes. It cannot be changed later.", "Name", extra="required maxlength=41" + mark("id")) if new else
               f'<div class="field"><label>Name</label><code>{esc(original)}</code><input type="hidden" name="id" value="{esc(original)}"></div>')
        fields = (top
                  + _input("pattern", v, "Lines over 2000 characters are skipped.", "Pattern (regular expression, matched per line)", extra="required maxlength=500 spellcheck=false" + mark("pattern"))
                  + _input("globs", v, "Comma-separated globs, for example **/*.py, **/*.ts.", "Files", extra=mark("globs"))
                  + fix
                  + _field("Sample text to test on", f'<textarea name="sample" rows="5" maxlength="{SAMPLE_CHARS}">{esc(v["sample"])}</textarea>',
                           "Optional. Paste a few lines; Test shows which match. Nothing is saved and no repository is read."))
        buttons = ('<p><button type="submit">Save smell</button> '
                   '<button type="submit" formaction="/scans/smells/sample" class="secondary">Test on sample text</button></p>')
    body = (f'<p><a href="/scans/smells">← smell library</a></p><form method="post" action="/scans/smells/save" class="settings">{csrf_field(csrf)}'
            f'<input type="hidden" name="orig" value="{esc(original)}">{fields}{buttons}</form>{preview}')
    if not new and not preset:
        body += ('<h2>Delete</h2><form method="post" action="/scans/smells/delete" class="settings">' + csrf_field(csrf) +
                 f'<input type="hidden" name="id" value="{esc(original)}"><label class="check danger"><input type="checkbox" name="confirm" value="1"> '
                 f'Delete {esc(original)}. Tickets already opened are kept.</label><button type="submit">Delete smell</button></form>')
    return body


def sample_html(result: dict | None = None, error: str = "") -> str:
    if error:
        return f'<h2>Test</h2><div class="flash bad" role="alert">{esc(error)}</div>'
    found, lines = result["findings"], result["lines"]
    shown = "".join(f'line {esc(f["line"])}: {esc(f["snippet"])}\n' for f in found[:SAMPLE_SHOWN])
    more = f" First {SAMPLE_SHOWN} shown." if len(found) > SAMPLE_SHOWN else ""
    note = "".join(f'<br>{esc(x)}' for x in result["notes"])
    return (f'<h2>Test</h2><div role="region" aria-label="Test result"><p class="muted" aria-live="polite">{len(found)} match(es) in {lines} line(s).{more} '
            f'Your own sample text, not saved.{note}</p>{f"<pre>{shown}</pre>" if found else ""}</div>')


def run_sample(pattern: str, text: str) -> dict:
    """Run the real worker recipe on `text` in a throw-away folder (same matcher, same per-file time limit; nothing else is read).
    Returns {"findings", "lines", "notes"}; raises ValueError with a message for the admin."""
    with tempfile.TemporaryDirectory() as work, tempfile.TemporaryDirectory() as out:
        Path(work, "sample.txt").write_text(text[:SAMPLE_CHARS])
        params, found = Path(out, "params.json"), Path(out, "findings.json")
        params.write_text(json.dumps({"exclude": [], "smells": [{"id": "sample", "type": "regex", "pattern": pattern, "globs": ["**/*"]}]}))
        env = {"PATH": os.environ.get("PATH", ""), "FACTORY_JOB_PARAMS": str(params), "FACTORY_FINDINGS_FILE": str(found)}
        try:
            r = subprocess.run([sys.executable, str(RECIPE)], cwd=work, env=env, capture_output=True, text=True, timeout=SAMPLE_SECONDS)
        except subprocess.TimeoutExpired:
            raise ValueError("The test took too long and was stopped. Try a more specific pattern.")
        if r.returncode != 0 or not found.exists():
            raise ValueError("The test could not run: " + (r.stderr.strip().splitlines() or ["no output"])[-1][:200])
        return {"findings": json.loads(found.read_text()), "lines": len(text[:SAMPLE_CHARS].splitlines()),
                "notes": [x.removeprefix("[smell-scan] ") for x in r.stderr.splitlines() if x.startswith("[smell-scan]")][:3]}


# ---------------------------------------------------------------- the scan form

def scan_flat(entry: dict | None, cfg) -> dict:
    e = entry or {}
    return {"name": e.get("name", ""), "repo": e.get("repo", cfg.repos[0] if cfg.repos else ""),
            "when": "cron" if e.get("cron") else "every", "every": e.get("every", "1w"), "cron": e.get("cron", "0 6 * * 1"),
            "smells": list(e.get("smells", ["todo-debt"])), "exclude": ", ".join(e.get("exclude", [])), "enabled": "1" if e.get("enabled", True) else ""}


def scan_from_form(form) -> dict:
    v = {k: str(form.get(k, "")) for k in ("name", "repo", "when", "every", "cron", "exclude", "enabled")}
    v["smells"] = form.getall("smell")
    return v


def scan_form_page(cfg, v: dict, csrf: str, original: str = "") -> str:
    new = not original
    repos = "".join(f'<option value="{esc(r)}"{" selected" if r == v["repo"] else ""}>{esc(r)}</option>' for r in cfg.repos)
    smells = "".join(f'<label class="check"><input type="checkbox" name="smell" value="{esc(sid)}"{" checked" if sid in v["smells"] else ""}> '
                     f'{smell_label(cfg, sid)} <span class="muted">{esc(name)}</span></label>' for sid, (name, _, _) in sn.smell_table(cfg.scanner).items())
    body = (f'<p><a href="/scans">← all scans</a></p>'
            f'<form method="post" action="/scans/save" class="settings">{csrf_field(csrf)}<input type="hidden" name="orig" value="{esc(original)}">'
            '<fieldset><legend>What and where</legend>'
            + (_input("name", v, "Lowercase letters, digits and dashes.", "Name", extra="required maxlength=41") if new else
               f'<div class="field"><label>Name</label><code>{esc(original)}</code><input type="hidden" name="name" value="{esc(original)}"></div>')
            + _field("Repository", f'<select name="repo">{repos}</select>', "Only repositories the factory already handles.")
            + f'<div class="field"><label>Smells</label>{smells}<div class="muted">Add your own in the <a href="/scans/smells">smell library</a>.</div></div>'
            + _input("exclude", v, "Comma-separated globs left out of the scan, for example vendor/**, **/*.min.js.", "Skip files")
            + '</fieldset><fieldset><legend>When</legend>'
            + _radio("when", "every", "Every", v["when"]) + _radio("when", "cron", "On a cron schedule (UTC)", v["when"])
            + _input("every", v, INTERVAL_HELP + " The first run is one interval after the scan is first seen; use Run now to go sooner.", "Interval")
            + _input("cron", v, "Five fields: minute hour day-of-month month weekday. 0 6 * * 1 is Mondays 06:00 UTC.", "Cron")
            + '</fieldset>'
            f'<label class="check"><input type="checkbox" name="enabled" value="1"{" checked" if v["enabled"] else ""}> Enabled</label>'
            '<p><button type="submit">Save scan</button></p></form>')
    if not new:
        body += ('<h2>Delete</h2><form method="post" action="/scans/delete" class="settings">' + csrf_field(csrf) +
                 f'<input type="hidden" name="name" value="{esc(original)}"><label class="check danger"><input type="checkbox" name="confirm" value="1"> '
                 f'Delete {esc(original)}. Tickets already opened are kept.</label><button type="submit">Delete scan</button></form>')
    return body


# ---------------------------------------------------------------- saving

def _globs(text: str) -> list[str]:
    return [g.strip() for g in text.split(",") if g.strip()]


def _bad_globs(globs: list[str]) -> bool:
    return not all(not g.startswith("/") and ".." not in g.split("/") for g in globs)


def pattern_problem(pattern: str) -> str:
    """The message for a pattern that cannot be saved, or ""."""
    if not pattern or len(pattern) > 500:
        return "Pattern: required, at most 500 characters."
    try:
        if re.compile(pattern).search(""):
            return "Pattern: it must not match an empty line. Nothing was saved."
    except re.error as e:
        return f"The pattern is not a valid regular expression: {e}. Nothing was saved."
    if (why := sn.regex_problem(pattern)):
        return f"That pattern is too slow or too broad ({why}). Try a more specific one. Nothing was saved."
    return ""


def build_smell(v: dict, existing: dict | None, taken: set[str]) -> dict:
    """The raw smell dict for a submitted form, keeping every key of `existing` the form does not own. Raises SettingsError(messages, field)."""
    errs: list[str] = []
    sid = v["id"].strip()
    if not sc.NAME.fullmatch(sid):
        errs.append("Name: use lower-case letters, digits and dashes.")
    elif existing is None and (sid in taken or sid in sn.PRESETS):
        errs.append(f"A smell named {sid} already exists.")
    if (why := pattern_problem(v["pattern"])):
        errs.append(why)
    globs = _globs(v["globs"]) or ["**/*"]
    if _bad_globs(globs):
        errs.append("Files: relative globs without ..")
    advice = v["advice"].replace("\r\n", "\n").strip()
    if (why := sn.advice_problem(advice)):
        errs.append("Advice is too long (max %d)." % sn.MAX_ADVICE if "too long" in why else f"Advice {why}.")
    if errs:
        raise S.SettingsError(errs)
    e = dict(existing or {})
    e.update({"id": sid, "name": e.get("name") or sid, "pattern": v["pattern"], "globs": globs})
    if advice:
        e["advice"] = advice
    else:
        e.pop("advice", None)
    return e


def invalid_field(messages: list[str]) -> str:
    """Which form field the first message is about."""
    m = messages[0] if messages else ""
    return "id" if m.startswith(("Name", "A smell named")) else "advice" if m.startswith("Advice") else "globs" if m.startswith("Files") else "pattern"


def build_scan(v: dict, cfg, existing: dict | None, taken: set[str]) -> dict:
    errs: list[str] = []
    name = v["name"].strip()
    if not sc.NAME.fullmatch(name):
        errs.append("Name: lowercase letters, digits and dashes, starting with a letter or digit.")
    elif existing is None and name in taken:
        errs.append(f"Name: there is already a scan called {name}.")
    if v["repo"] not in cfg.repos:
        errs.append("Repository: choose one the factory handles.")
    known = set(sn.smell_table(cfg.scanner))
    smells = [x for x in v["smells"] if x in known]
    if not smells:
        errs.append("Choose at least one smell.")
    exclude = _globs(v["exclude"])
    if _bad_globs(exclude):
        errs.append("Skip files: relative globs without ..")
    e = {k: x for k, x in (existing or {}).items() if k not in ("every", "cron")}
    e.update({"name": name, "repo": v["repo"], "smells": smells, "enabled": v["enabled"] == "1"})
    if exclude:
        e["exclude"] = exclude
    else:
        e.pop("exclude", None)
    if v["when"] == "cron":
        e["cron"] = v["cron"].strip()
        if sc.parse_cron(e["cron"]) is None:
            errs.append("Cron: five fields (minute hour day-of-month month weekday), UTC.")
    else:
        e["every"] = v["every"].strip()
        if sc.parse_every(e["every"]) is None or sc.parse_every(e["every"]) < 3600:
            errs.append(f"Interval: {INTERVAL_HELP}")
    if errs:
        raise S.SettingsError(errs)
    return e


def store(cfg_path: str, state_dir: Path, key: str, name: str, entry: dict | None, id_key: str = "name") -> None:
    """Replace (or add, or with entry=None remove) one entry of the `scanner.<key>` list ("smells" or "scans"). The whole list is kept in the
    overrides file, because a list in the overrides replaces the config file's list: the first save copies the hand-written entries across."""
    current = raw_scanner(cfg_path).get(key, [])
    out, hit = [], False
    for x in current:
        if x.get(id_key) == name:
            hit = True
            if entry is not None:
                out.append(entry)
        else:
            out.append(x)
    if not hit and entry is not None:
        out.append(entry)
    base, new_ov = S.base_raw(cfg_path), copy.deepcopy(S.overrides_raw(cfg_path))
    table = new_ov.setdefault("scanner", {})
    if out == base.get("scanner", {}).get(key, []):
        table.pop(key, None)
    else:
        table[key] = out
    if not table:
        new_ov.pop("scanner")
    S._commit(cfg_path, state_dir, new_ov)


def set_enabled(cfg_path: str, state_dir: Path, on: bool) -> None:
    """Write `[scanner] enabled` to the overrides (dropped when the hand-written file already says the same). _commit refuses it when workers are off."""
    base, new_ov = S.base_raw(cfg_path), copy.deepcopy(S.overrides_raw(cfg_path))
    table = new_ov.setdefault("scanner", {})
    if on == bool(base.get("scanner", {}).get("enabled", False)):
        table.pop("enabled", None)
    else:
        table["enabled"] = on
    if not table:
        new_ov.pop("scanner")
    S._commit(cfg_path, state_dir, new_ov)


def store_advice(cfg_path: str, state_dir: Path, sid: str, text: str) -> None:
    """Set a preset's recommended fix. The table merges key by key, so only this key is written; an empty text removes it (or, when the
    hand-written file has one, overrides it with empty)."""
    base, new_ov = S.base_raw(cfg_path), copy.deepcopy(S.overrides_raw(cfg_path))
    table = new_ov.setdefault("scanner", {})
    adv = table.setdefault("advice", {})
    if text == base.get("scanner", {}).get("advice", {}).get(sid, ""):
        adv.pop(sid, None)                     # the hand-written file already says this
    else:
        adv[sid] = text
    if not adv:
        table.pop("advice")
    if not table:
        new_ov.pop("scanner")
    S._commit(cfg_path, state_dir, new_ov)


def smell_users(cfg, sid: str) -> list[str]:
    return [s.name for s in cfg.scanner.scans if sid in s.smells]
