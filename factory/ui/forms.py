"""HTML forms for settings, credentials, Telegram and Slack. Values come from the effective configuration, never from secrets."""
import json
import time

from ..events import ALL_EVENTS, LEVELS
from ..roles import PROMPT_AGENTS, builtin_prompt
from . import settings as S
from .integrations import SECRETS, secret_status
from .views import badge, csrf_field, ago, esc, ts

EVENT_HELP = {
    "needs_human": "A ticket needs a person (with Run and Skip buttons)", "failure": "A run, stage or CI fix failed",
    "rate_limit": "A plan or API limit was hit", "pr_ready": "A pull request is ready", "stage_done": "An analyst, designer or architect document is on the ticket",
    "ci_result": "CI passed, or finished without a fix", "ci_fix": "CI failed and an agent fix round is starting", "recovery": "An interrupted run was requeued",
    "conflict": "A factory pull request conflicts with its base branch, or a conflict was resolved",
    "worker_offline": "Verification jobs are waiting and no worker has been seen (or a worker is back)",
    "fallback": "A model was unavailable (limit or API error) and the next fallback model was tried", "started": "A run started (which model, what the classifier decided)", "startup": "The orchestrator started", "skipped": "A ticket was skipped from chat",
    "info": "Anything else",
}
TABS = [("general", "General"), ("routing", "Routing"), ("roles", "Role agents"), ("projects", "Projects"), ("classifier", "Classifier"), ("review", "Code review"), ("pm", "Project manager"), ("mockups", "Design mockups"), ("runner", "Agent runner"), ("ci", "CI feedback"), ("conflicts", "Merge conflicts"), ("screens", "Screens"), ("prompts", "Agent prompts")]


def _fmt(f: S.Field, v) -> str:
    if v is None:
        return ""
    if f.kind in ("repos", "hosts", "models"):
        return "\n".join(v)
    if f.kind == "kv":
        return "\n".join(f"{k}={x}" for k, x in sorted(v.items()))
    if f.kind == "viewports":
        return "\n".join(f'{x["name"]} {x["width"]}x{x["height"]}' for x in S.canon(f.key, v))
    if f.kind == "wchecks":
        return "\n".join(f'{c["repo"]} {c["recipe"]} {c.get("platform", "any")}' + ("" if c.get("required", True) else " advisory") for c in v)
    return str(v)


SIDE = [("home", "Overview", "/settings"), ("general", "General", "/settings?section=general"), ("routing", "Routing", "/settings?section=routing"),
        ("roles", "Role agents", "/settings?section=roles"), ("projects", "Projects", "/settings?section=projects"),
        ("harnesses", "Harnesses", "/harnesses"), ("workers", "Workers", "/workers"), ("schedules", "Schedules", "/schedules"), ("credentials", "Credentials", "/credentials"), ("telegram", "Telegram", "/telegram"), ("slack", "Slack", "/slack"),
        ("labels", "Labels", "/settings?section=labels"), ("release", "Releases", "/release"), ("backup", "Backup", "/backup")]
SIDE_MORE = [(k, t, f"/settings?section={k}") for k, t in TABS if k not in {s[0] for s in SIDE}]


def side_list(active: str) -> str:
    link = lambda k, t, href: f'<a href="{href}"{" class=active aria-current=page" if k == active else ""}>{esc(t)}</a>'
    cls = "side on-home" if active == "home" else "side"            # on a phone the home keeps the list to one scrolling row
    return (f'<nav class="{cls}" aria-label="Settings">' + "".join(link(*x) for x in SIDE)
            + '<span class="side-h">More settings</span>' + "".join(link(*x) for x in SIDE_MORE) + "</nav>")


def labels_page(cfg) -> str:
    rows = [("Build label", cfg.trigger_label), ("Review label", cfg.review.label), ("Reviewed label", cfg.review.done_label), ("Conflicts label", cfg.conflicts.label)]
    return ('<p class="muted">Labels are edited per ticket, as the factory\'s GitHub account: open <a href="/tickets">Tickets</a> and press <strong>Labels</strong>. '
            'The labels the factory itself reacts to are set under General, Code review and Merge conflicts.</p><table class="meta">'
            + "".join(f"<tr><th>{esc(k)}</th><td><code>{esc(v)}</code></td></tr>" for k, v in rows) + "</table>")


def field_row(f: S.Field, eff: dict, base: dict, submitted=None, row: bool = False) -> str:
    value = effective_value(f, eff, submitted)
    name = esc(f.key)
    if f.kind == "bool":
        on = (submitted.get(f.key) == "1") if submitted is not None else bool(value)
        if row:
            control = f'<input type="checkbox" id="{name}" name="{name}" value="1"{" checked" if on else ""}>'
            head = f'<label for="{name}">{esc(f.label)}</label>'
        else:
            control = f'<label class="check"><input type="checkbox" name="{name}" value="1"{" checked" if on else ""}> {esc(f.label)}</label>'
            head = ""
    else:
        head = f'<label for="{name}">{esc(f.label)}</label>'
        if f.kind == "select":
            control = f'<select id="{name}" name="{name}">' + "".join(f'<option{" selected" if c == value else ""}>{esc(c)}</option>' for c in f.choices) + "</select>"
        elif f.kind == "checks":
            have = set(submitted.getall(f.key)) if submitted is not None else set(value or [])
            control = " ".join(f'<label class="check"><input type="checkbox" name="{name}" value="{esc(c)}"{" checked" if c in have else ""}> {esc(c)}</label>' for c in f.choices)
        elif f.kind in ("repos", "hosts", "kv", "models", "wchecks", "viewports"):
            control = f'<textarea id="{name}" name="{name}" rows="{max(3, min(8, len((value or "").splitlines()) + 1))}">{esc(value)}</textarea>'
        elif f.kind == "prompt":           # the newline after the tag is dropped by the browser, so a leading one in the value survives
            control = f'<textarea id="{name}" name="{name}" rows="6" maxlength="{S.PROMPT_MAX}">\n{esc(value)}</textarea>'
            agent = f.key.partition(".")[2]
            if agent in PROMPT_AGENTS:
                control += (f'<details><summary class="muted">Built-in instructions (always included, and they win)</summary>'
                            f'<pre>{esc(builtin_prompt(agent))}</pre></details>')
        else:
            control = f'<input id="{name}" name="{name}" value="{esc(value)}" autocomplete="off">'
    pinned = S.get_in(base, f.key)
    note = ""
    if S.get_in(eff, f.key) is not None and S.get_in(eff, f.key) != pinned and pinned is not None:
        said = _fmt(f, pinned)
        said = said[:200] + "…" if f.kind == "prompt" and len(said) > 200 else said
        note = f'<span class="muted"> · changed here (config.toml says: {esc(said)})</span>'
    help_ = f'<div class="muted">{esc(f.help)}{note}</div>' if (f.help or note) else ""
    confirm = (f'<label class="check danger"><input type="checkbox" name="confirm__{name}" value="1"> I understand: {esc(f.danger)}</label>' if f.danger else "")
    if row:
        return f'<div class="srow"><div class="what">{head}{help_}</div><div class="ctl">{control}</div>{confirm}</div>'
    return f'<div class="field">{head}{control}{help_}{confirm}</div>'


def effective_value(f: S.Field, eff: dict, submitted=None):
    if submitted is not None and f.kind not in ("bool", "checks"):
        return submitted.get(f.key, "")
    return _fmt(f, S.effective(eff, f.key)) if f.kind not in ("bool", "checks", "select") else S.effective(eff, f.key)


def settings_form(section: str, eff: dict, base: dict, csrf: str, submitted=None) -> str:
    title, fields = S.SECTIONS[section][0], S.fields_for(section, eff)
    general = section == "general"
    rows = "".join(field_row(f, eff, base, submitted, row=general) for f in fields)
    return (f'<form method="post" action="/settings/save" class="settings{" rows" if general else ""}">{csrf_field(csrf)}'
            f'<input type="hidden" name="section" value="{esc(section)}">{rows}<button>{"Save changes" if general else "Save " + esc(title.lower())}</button></form>'
            '<p class="muted">Saving writes <code>config.overrides.toml</code>; your <code>config.toml</code> is never touched. '
            + ("Changes apply the next time the factory is idle.</p>" if general else "Changes apply when the orchestrator is next idle (it never restarts mid-task).</p>"))


def projects_form(base: dict, eff: dict, csrf: str, submitted=None) -> str:
    projects = list(eff.get("projects", [])) + [{"name": "", "description": "", "repos": []}]
    out = ['<p class="muted">Repositories that work together are one project: agents get all of them side by side and may change several in one task.</p>',
           f'<form method="post" action="/settings/projects" class="settings">{csrf_field(csrf)}']
    for i, p in enumerate(projects[:20]):
        repos = list(p.get("repos", [])) + [{"repo": "", "role": ""}, {"repo": "", "role": ""}]
        rows = "".join(
            f'<tr><td data-l="Repository"><input name="p{i}_r{j}_repo" placeholder="owner/name" value="{esc(r["repo"])}"></td>'
            f'<td data-l="Role"><input name="p{i}_r{j}_role" placeholder="what this repo is for" value="{esc(r.get("role", ""))}" class="wide"></td></tr>' for j, r in enumerate(repos[:30]))
        out.append(f'<fieldset><legend>{esc(p["name"] or "New project")}</legend><div class="field"><label>Name</label><input name="p{i}_name" value="{esc(p["name"])}"></div>'
                   f'<div class="field"><label>Description</label><input name="p{i}_desc" value="{esc(p.get("description", ""))}" class="wide"></div>'
                   f'<table class="stack"><thead><tr><th>Repository</th><th>Role</th></tr></thead><tbody>{rows}</tbody></table></fieldset>')
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
        hint = '<p class="muted">Pasting a new value replaces the current one.</p>' if st["set"] else ""
        test = f'<button name="action" value="test" formaction="/credentials/test">Test</button>' if name in ("github", "openrouter", "slack_bot", "slack_app") else ""
        cards.append(
            f'<form method="post" action="/credentials/save" class="card cred">{csrf_field(csrf)}<input type="hidden" name="name" value="{esc(name)}">'
            f'<h3>{esc(label)}</h3><p>{status}</p>{hint}{extra}<div class="field"><input type="password" name="value" placeholder="paste new value" autocomplete="off" class="wide" aria-label="{esc(label)} – new value"></div>'
            f'<button>Save</button> {test}</form>')
    return ('<p class="muted">Credentials are stored as files readable only by the factory user and are <strong>never shown again</strong>. '
            'Saving restarts the orchestrator when it is next idle.</p>' + result + '<div class="cards cred-grid">' + "".join(cards) + "</div>")


def slack_page(cfg, eff: dict, csrf: str, unknown: list, conn: dict | None = None, result: str = "") -> str:
    """Slack is set up here from start to finish: create the app from a manifest, paste its two tokens, type /factory in Slack and
    pick yourself, then choose how chatty it is. Nothing needs editing on disk."""
    from .integrations import SLACK_MANIFEST, slack_create_url
    sl = eff.get("slack", {})
    mode_events = isinstance(sl.get("events"), list)
    chosen = set(sl.get("events") or [])
    level = sl.get("verbosity", "normal")
    bot, app = secret_status(cfg, "slack_bot"), secret_status(cfg, "slack_app")
    on = bool(cfg.slack_channel and cfg.slack_user_id)
    checks = "".join(
        f'<label class="check evt"><input type="checkbox" name="slack.events" value="{esc(e)}"{" checked" if e in chosen else ""}> <strong>{esc(e)}</strong> '
        f'<span class="muted">{esc(EVENT_HELP.get(e, ""))}</span></label>' for e in sorted(ALL_EVENTS, key=lambda x: (x not in LEVELS["quiet"], x not in LEVELS["normal"], x)))
    lv = "".join(f'<option{" selected" if v == level else ""}>{v}</option>' for v in ("quiet", "normal", "verbose"))
    tokens = "".join(
        f'<form method="post" action="/slack/token" class="card cred">{csrf_field(csrf)}<input type="hidden" name="name" value="{name}">'
        f'<h3>{label}</h3><p>{badge("set", "good") if st["set"] else badge("not set", "warn")} <span class="muted">{hint}</span></p>'
        f'<div class="field"><input type="password" name="value" placeholder="{prefix}..." autocomplete="off" class="wide" aria-label="{label}"></div>'
        f'<button>Save</button> <button name="action" value="test" formaction="/slack/check">Test</button></form>'
        for name, label, prefix, hint, st in (
            ("slack_bot", "Bot token", "xoxb-", "Install App → Bot User OAuth Token.", bot),
            ("slack_app", "App-level token", "xapp-", "Basic Information → App-Level Tokens → Generate, with the scope connections:write.", app)))
    if conn is None:
        link = badge("not connected yet", "warn") + (' <span class="muted">The factory connects when it is next idle after the tokens are saved.</span>'
                                                      if bot["set"] and app["set"] else ' <span class="muted">Save both tokens first.</span>')
    elif conn.get("ok"):
        link = badge("connected", "good")
    else:
        link = badge("connection failing", "bad") + f' <span class="muted">{esc(conn.get("error", ""))}</span>'
    found = ""
    if unknown:
        found = ("<table class=stack><thead><tr><th>Name</th><th>Member id</th><th>Channel</th><th></th></tr></thead><tbody>" + "".join(
            f'<tr><td data-l="Name">{esc(u["name"])}</td><td data-l="Member id">{esc(u["id"])}</td><td data-l="Channel">{esc(u.get("channel", ""))}</td>'
            f'<td data-l="Actions"><form method="post" action="/slack/use" class="inline">{csrf_field(csrf)}<input type="hidden" name="user_id" value="{esc(u["id"])}">'
            f'<input type="hidden" name="channel" value="{esc(u.get("channel", ""))}"><button>Use this</button></form></td></tr>'
            for u in unknown) + "</tbody></table><p class=muted>Only this person will ever be obeyed, and alerts go to that channel. Check it is you.</p>")
    return (
        f'<p>{badge("Slack is on", "good") if on else badge("Slack is off", "warn")} '
        '<span class="muted">Slack works instead of Telegram or alongside it. It uses Socket Mode: the factory connects out to Slack, so nothing '
        f'needs to be reachable from the internet.</span></p>{result}'
        '<h2>1. Create the Slack app</h2>'
        f'<p><a class="btn" href="{esc(slack_create_url())}" target="_blank" rel="noopener noreferrer">Create the app in Slack</a> '
        '<span class="muted">Pick your workspace, confirm, then press <em>Install to Workspace</em>.</span></p>'
        '<details><summary>The app manifest (to paste by hand under Create New App → From a manifest)</summary>'
        f'<pre>{esc(json.dumps(SLACK_MANIFEST, indent=2))}</pre></details>'
        f'<h2>2. Paste its tokens</h2><div class="cards">{tokens}</div>'
        '<p class="muted">Saved tokens are never shown again. Saving one restarts the factory when it is next idle.</p>'
        f'<h2>3. Link your Slack account</h2><p>{link}</p>'
        '<p class="muted">In Slack, open the channel for alerts (or a direct message with the app), type <code>/invite @Shikumi</code>, then '
        '<code>/factory</code>. You appear below; press <em>Use this</em>. You can also type the ids yourself further down.</p>'
        f'{found or "<p class=muted>Nobody has used /factory yet. Reload this page after you have.</p>"}'
        f'<h2>4. Settings</h2><form method="post" action="/slack/save" class="settings">{csrf_field(csrf)}'
        f'<div class="field"><label>Channel id</label><input name="slack.channel" value="{esc(sl.get("channel", ""))}" autocomplete="off" placeholder="C0123ABCDEF">'
        '<div class="muted">Alerts go here and buttons are only accepted from here (channel details, at the bottom). Empty turns Slack off.</div></div>'
        f'<div class="field"><label>Your member id</label><input name="slack.user_id" value="{esc(sl.get("user_id", ""))}" autocomplete="off" placeholder="U0123ABCDEF">'
        '<div class="muted">The only person the factory will obey (profile → ⋮ → Copy member ID). Empty turns Slack off.</div></div>'
        f'<div class="field"><label>Address of this UI</label><input name="slack.ui_url" value="{esc(sl.get("ui_url", ""))}" autocomplete="off" placeholder="https://factory.example.internal">'
        '<div class="muted">Optional: adds an <em>Open in UI</em> button to messages with questions for you.</div></div>'
        f'<div class="field"><label>How chatty</label><label class="check"><input type="radio" name="slack.mode" value="level"{"" if mode_events else " checked"}> Use a level</label> '
        f'<select name="slack.verbosity">{lv}</select>'
        '<div class="muted">quiet: needs-a-person, failures, rate limits · normal: + PR ready, stage done, CI results · verbose: everything</div>'
        f'<label class="check"><input type="radio" name="slack.mode" value="events"{" checked" if mode_events else ""}> Pick the events myself</label>'
        f'<div class="events">{checks}</div></div><button>Save Slack settings</button></form>'
        '<h2>5. Check</h2><p class="muted">Messages and button clicks from anyone else are never acted on; they are listed in step 3.</p>'
        f'<form method="post" action="/slack/test" class="inline">{csrf_field(csrf)}<button>Send a test message</button></form>')


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
        found = ("<h2>People who messaged the bot</h2><table class=stack><thead><tr><th>Name</th><th>Id</th><th></th></tr></thead><tbody>" + "".join(
            f'<tr><td data-l="Name">{esc(u["name"])} <span class="muted">{esc(u.get("chat", ""))}</span></td><td data-l="Id">{int(u["id"])}</td><td data-l="Actions">'
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
