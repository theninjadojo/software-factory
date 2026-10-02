"""HTML forms for settings, credentials and Telegram. Values come from the effective configuration, never from secrets."""
import json
import time

from ..events import ALL_EVENTS, LEVELS
from . import settings as S
from .integrations import SECRETS, secret_status
from .views import badge, csrf_field, ago, esc, ts

EVENT_HELP = {
    "needs_human": "A ticket needs a person (with Run and Skip buttons)", "failure": "A run, stage or CI fix failed",
    "rate_limit": "A plan or API limit was hit", "pr_ready": "A pull request is ready", "stage_done": "An analyst, designer or architect document is on the ticket",
    "ci_result": "CI passed, or finished without a fix", "ci_fix": "CI failed and an agent fix round is starting", "recovery": "An interrupted run was requeued",
    "conflict": "A factory pull request conflicts with its base branch, or a conflict was resolved",
    "started": "A run started (which model, what the classifier decided)", "startup": "The orchestrator started", "skipped": "A ticket was skipped from Telegram",
    "info": "Anything else",
}
TABS = [("general", "General"), ("routing", "Routing"), ("roles", "Role agents"), ("projects", "Projects"), ("classifier", "Classifier"), ("review", "Code review"), ("runner", "Agent runner"), ("ci", "CI feedback"), ("conflicts", "Merge conflicts")]


def _fmt(f: S.Field, v) -> str:
    if v is None:
        return ""
    if f.kind in ("repos", "hosts"):
        return "\n".join(v)
    if f.kind == "kv":
        return "\n".join(f"{k}={x}" for k, x in sorted(v.items()))
    return str(v)


def tabs(active: str, base: str = "/settings") -> str:
    return '<p class="tabs">' + " ".join(f'<a href="{base}?section={k}"{" class=active" if k == active else ""}>{esc(t)}</a>' for k, t in TABS) + "</p>"


def field_row(f: S.Field, eff: dict, base: dict, submitted=None) -> str:
    value = effective_value(f, eff, submitted)
    name = esc(f.key)
    if f.kind == "bool":
        on = (submitted.get(f.key) == "1") if submitted is not None else bool(value)
        control = f'<label class="check"><input type="checkbox" name="{name}" value="1"{" checked" if on else ""}> {esc(f.label)}</label>'
        head = ""
    else:
        head = f'<label for="{name}">{esc(f.label)}</label>'
        if f.kind == "select":
            control = f'<select id="{name}" name="{name}">' + "".join(f'<option{" selected" if c == value else ""}>{esc(c)}</option>' for c in f.choices) + "</select>"
        elif f.kind == "checks":
            have = set(submitted.getall(f.key)) if submitted is not None else set(value or [])
            control = " ".join(f'<label class="check"><input type="checkbox" name="{name}" value="{esc(c)}"{" checked" if c in have else ""}> {esc(c)}</label>' for c in f.choices)
        elif f.kind in ("repos", "hosts", "kv"):
            control = f'<textarea id="{name}" name="{name}" rows="{max(3, min(8, len((value or "").splitlines()) + 1))}">{esc(value)}</textarea>'
        else:
            control = f'<input id="{name}" name="{name}" value="{esc(value)}" autocomplete="off">'
    pinned = S.get_in(base, f.key)
    note = ""
    if S.get_in(eff, f.key) is not None and S.get_in(eff, f.key) != pinned and pinned is not None:
        note = f'<span class="muted"> · changed here (config.toml says: {esc(_fmt(f, pinned))})</span>'
    help_ = f'<div class="muted">{esc(f.help)}{note}</div>' if (f.help or note) else ""
    confirm = (f'<label class="check danger"><input type="checkbox" name="confirm__{name}" value="1"> I understand: {esc(f.danger)}</label>' if f.danger else "")
    return f'<div class="field">{head}{control}{help_}{confirm}</div>'


def effective_value(f: S.Field, eff: dict, submitted=None):
    if submitted is not None and f.kind not in ("bool", "checks"):
        return submitted.get(f.key, "")
    return _fmt(f, S.effective(eff, f.key)) if f.kind not in ("bool", "checks", "select") else S.effective(eff, f.key)


def settings_form(section: str, eff: dict, base: dict, csrf: str, submitted=None) -> str:
    title, fields = S.SECTIONS[section][0], S.fields_for(section, eff)
    rows = "".join(field_row(f, eff, base, submitted) for f in fields)
    return (f'{tabs(section)}<form method="post" action="/settings/save" class="settings">{csrf_field(csrf)}'
            f'<input type="hidden" name="section" value="{esc(section)}">{rows}<button>Save {esc(title.lower())}</button></form>'
            '<p class="muted">Saving writes <code>config.overrides.toml</code>; your <code>config.toml</code> is never touched. '
            'Changes apply when the orchestrator is next idle (it never restarts mid-task).</p>')


def projects_form(base: dict, eff: dict, csrf: str, submitted=None) -> str:
    projects = list(eff.get("projects", [])) + [{"name": "", "description": "", "repos": []}]
    out = [tabs("projects"), '<p class="muted">Repositories that work together are one project: agents get all of them side by side and may change several in one task.</p>',
           f'<form method="post" action="/settings/projects" class="settings">{csrf_field(csrf)}']
    for i, p in enumerate(projects[:20]):
        repos = list(p.get("repos", [])) + [{"repo": "", "role": ""}, {"repo": "", "role": ""}]
        rows = "".join(
            f'<tr><td><input name="p{i}_r{j}_repo" placeholder="owner/name" value="{esc(r["repo"])}"></td>'
            f'<td><input name="p{i}_r{j}_role" placeholder="what this repo is for" value="{esc(r.get("role", ""))}" class="wide"></td></tr>' for j, r in enumerate(repos[:30]))
        out.append(f'<fieldset><legend>{esc(p["name"] or "New project")}</legend><div class="field"><label>Name</label><input name="p{i}_name" value="{esc(p["name"])}"></div>'
                   f'<div class="field"><label>Description</label><input name="p{i}_desc" value="{esc(p.get("description", ""))}" class="wide"></div>'
                   f'<table><thead><tr><th>Repository</th><th>Role</th></tr></thead><tbody>{rows}</tbody></table></fieldset>')
    out.append('<button>Save projects</button></form><p class="muted">Leave a project\'s name and repositories empty to remove it.</p>')
    return "".join(out)


def classifier_tester(csrf: str, result: str = "") -> str:
    return ('<h2>Try it</h2><p class="muted">Runs the configured classifier on text you paste. With Jev selected, the text is sent to OpenRouter.</p>'
            f'<form method="post" action="/classify/test" class="settings">{csrf_field(csrf)}'
            '<div class="field"><label>Title</label><input name="title" class="wide"></div>'
            '<div class="field"><label>Body</label><textarea name="body" rows="5"></textarea></div>'
            '<div class="field"><label>Labels (comma separated)</label><input name="labels" placeholder="bug, complexity:low" class="wide"></div>'
            f'<button>Classify</button></form>{result}')


def classification_result(c, how: str, decision) -> str:
    rows = [("Kind", c.kind), ("Tier → route", f"{c.complexity} → {decision.route.model} ({decision.route.effort})" if decision.route else c.complexity),
            ("Effort", c.effort or "—"), ("Next stage", f"{c.stage} ({c.stage_confidence:.2f})" if c.stage else "—"),
            ("Needs a person", "yes" if c.needs_human else "no"), ("Confidence", f"{c.confidence:.2f}"),
            ("Outcome", decision.action + (": " + decision.reason if decision.action == "human" else "")), ("Classified by", how)]
    return '<table class="meta">' + "".join(f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>" for k, v in rows) + "</table>"


def credentials_page(cfg, csrf: str, result: str = "") -> str:
    cards = []
    for name, (label, _) in SECRETS.items():
        st = secret_status(cfg, name)
        status = (badge("set", "good") + f' <span class="muted">updated {esc(ago(st["updated"]))}' + (f" · {esc(st['kind'])}" if st.get("kind") else "") + "</span>"
                  if st["set"] else badge("not set", "warn") + (f' <span class="muted">{esc(st.get("note", ""))}</span>' if st.get("note") else ""))
        extra = ""
        if name == "claude":
            extra = ('<div class="field"><label class="check"><input type="radio" name="kind" value="subscription" checked> Subscription token '
                     '(<code>claude setup-token</code>)</label> <label class="check"><input type="radio" name="kind" value="apikey"> API key</label>'
                     '<div class="muted">Unattended use of a subscription is a gray area in Anthropic\'s terms; an API key is the supported route.</div></div>')
        test = f'<button name="action" value="test" formaction="/credentials/test">Test</button>' if name in ("github", "openrouter") else ""
        cards.append(
            f'<form method="post" action="/credentials/save" class="card cred">{csrf_field(csrf)}<input type="hidden" name="name" value="{esc(name)}">'
            f'<h3>{esc(label)}</h3><p>{status}</p>{extra}<div class="field"><input type="password" name="value" placeholder="paste a new value to replace it" autocomplete="off" class="wide"></div>'
            f'<button>Save</button> {test}</form>')
    return ('<p class="muted">Credentials are stored as files readable only by the factory user and are <strong>never shown again</strong>. '
            'Saving restarts the orchestrator when it is next idle.</p>' + result + '<div class="cards">' + "".join(cards) + "</div>")


def telegram_page(cfg, eff: dict, csrf: str, unknown: list, result: str = "") -> str:
    tg = eff.get("telegram", {})
    mode_events = isinstance(tg.get("events"), list)
    chosen = set(tg.get("events") or [])
    level = tg.get("verbosity", "normal")
    token = secret_status(cfg, "telegram")
    checks = "".join(
        f'<label class="check evt"><input type="checkbox" name="telegram.events" value="{esc(e)}"{" checked" if e in chosen else ""}> <strong>{esc(e)}</strong> '
        f'<span class="muted">{esc(EVENT_HELP.get(e, ""))}</span></label>' for e in sorted(ALL_EVENTS, key=lambda x: (x not in LEVELS["quiet"], x not in LEVELS["normal"], x)))
    lv = "".join(f'<option{" selected" if v == level else ""}>{v}</option>' for v in ("quiet", "normal", "verbose"))
    found = ""
    if unknown:
        found = ("<h2>People who messaged the bot</h2><table><thead><tr><th>Name</th><th>Id</th><th></th></tr></thead><tbody>" + "".join(
            f'<tr><td>{esc(u["name"])} <span class="muted">{esc(u.get("chat", ""))}</span></td><td>{int(u["id"])}</td><td>'
            f'<form method="post" action="/telegram/use" class="inline">{csrf_field(csrf)}<input type="hidden" name="chat_id" value="{int(u["id"])}"><button>Use this id</button></form></td></tr>'
            for u in unknown) + "</tbody></table><p class=muted>Only this account will ever be obeyed. Check the id is yours.</p>")
    return (
        f'<p>{badge("bot token set", "good") if token["set"] else badge("bot token not set", "warn")} '
        f'<span class="muted">Create a bot with BotFather and paste its token on the <a href="/credentials">Credentials</a> page.</span></p>{result}'
        f'<form method="post" action="/telegram/save" class="settings">{csrf_field(csrf)}'
        f'<div class="field"><label>Your Telegram user id</label><input name="telegram.chat_id" value="{esc(tg.get("chat_id", 0))}" autocomplete="off">'
        '<div class="muted">The only account the bot will obey. 0 turns Telegram off.</div></div>'
        f'<div class="field"><label>How chatty</label><label class="check"><input type="radio" name="telegram.mode" value="level"{"" if mode_events else " checked"}> Use a level</label> '
        f'<select name="telegram.verbosity">{lv}</select>'
        '<div class="muted">quiet: needs-a-person, failures, rate limits · normal: + PR ready, stage done, CI results · verbose: everything</div>'
        f'<label class="check"><input type="radio" name="telegram.mode" value="events"{" checked" if mode_events else ""}> Pick the events myself</label>'
        f'<div class="events">{checks}</div></div><button>Save Telegram settings</button></form>'
        f'<h2>Set up</h2><p class="muted">Message your bot once, then press Find. Messages from anyone else are never acted on.</p>'
        f'<form method="post" action="/telegram/detect" class="inline">{csrf_field(csrf)}<button>Find my chat id</button></form> '
        f'<form method="post" action="/telegram/test" class="inline">{csrf_field(csrf)}<button>Send a test message</button></form>{found}')


def harnesses_page(cfg, eff: dict, csrf: str, errors=None) -> str:
    from .integrations import harness_credential_status
    used = {}
    for k, r in cfg.routes.items():
        used.setdefault(r.harness, []).append(f"routing.{k}")
    for r in cfg.roles:
        used.setdefault(r.harness, []).append(f"role {r.name}")
    cards = []
    for name, h in cfg.harnesses.items():
        cred = harness_credential_status(cfg, name)
        flags = badge("enabled", "good") if h.enabled else badge("disabled", "warn")
        if h.experimental:
            flags += " " + badge("experimental", "warn")
        if name == "claude-code":
            credential = '<p class="muted">Credential: managed on the <a href="/credentials">Credentials</a> page.</p>'
        else:
            status = (badge("set", "good") + f' <span class="muted">{esc(cred["vars"])} · updated {esc(ago(cred["updated"]))}</span>') if cred["set"] else badge("not set", "warn")
            credential = (f'<form method="post" action="/harnesses/credential" class="inline-form">{csrf_field(csrf)}<input type="hidden" name="name" value="{esc(name)}">'
                          f'<p>Credential ({esc(h.env_var)}): {status}</p><input type="password" name="value" placeholder="paste a key to replace it" autocomplete="off" class="wide"> '
                          '<button>Save key</button></form>')
        uses = ", ".join(used.get(name, [])) or "nothing yet"
        notice = (f'<p class="muted"><strong>Data:</strong> {esc(h.data_notice)}</p>' if h.data_notice else "") \
            + (f'<p class="muted">Model id: {esc(h.model_hint)}</p>' if h.model_hint else "")
        cards.append(
            f'<form method="post" action="/harnesses/save" class="card harness">{csrf_field(csrf)}<input type="hidden" name="name" value="{esc(name)}">'
            f'<h3>{esc(name)}</h3><p>{flags}</p><p class="muted">{esc(h.notes)}</p>{notice}<p class="muted">Used by: {esc(uses)}</p>'
            f'<div class="field"><label class="check"><input type="checkbox" name="enabled" value="1"{" checked" if h.enabled else ""}> Enabled</label></div>'
            f'<div class="field"><label>Image</label><input name="image" value="{esc(h.image)}" class="wide"></div>'
            f'<div class="field"><label>Command run in the sandbox</label><textarea name="command" rows="4">{esc(h.command)}</textarea>'
            '<div class="muted">Set by you, never by ticket text. Reads the task from <code>/task/prompt.txt</code>; <code>$MODEL</code> and <code>$MAX_TURNS</code> are available.</div></div>'
            f'<div class="field"><label>Hosts it may reach</label><textarea name="allow_hosts" rows="2">{esc(chr(10).join(h.allow_hosts))}</textarea></div>'
            '<label class="check danger"><input type="checkbox" name="confirm" value="1"> I understand: enabling a harness or changing its command, image or hosts changes what runs in the sandbox and what it can reach.</label>'
            f'<div><button>Save {esc(name)}</button></div></form>{credential}')
    note = ('<p class="muted">A harness is the agent program the sandbox runs. Routes and roles choose one by name (Settings → Routing and Role agents). '
            'Codex, Gemini and OpenCode (OpenRouter, Zen) are <strong>experimental templates</strong>: the plumbing is tested, but their command lines have not been verified end to end. '
            'Build their images from <code>sandbox/codex</code>, <code>sandbox/gemini</code> and <code>sandbox/opencode</code>. Each login also has its own terms for unattended use.</p>')
    return note + '<div class="cards one">' + "".join(cards) + "</div>"
