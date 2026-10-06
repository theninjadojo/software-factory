"""The Settings home and the settings shown where they matter.

Every feature the factory has is listed on /settings with its state (on, off or needing set-up), the screens it changes and
where it is set, so nothing is found only by opening the right section. The screens a setting shapes (Tickets, Factory, a
ticket) show it in a strip, and a simple switch can be flipped there after a confirmation (SWITCHES, the only keys
/settings/feature writes). Anything with keys or several steps links to its own page instead."""
from dataclasses import dataclass
from urllib.parse import quote

from ..tracker import display
from .integrations import SECRETS, secret_status
from .views import badge, csrf_field, esc

# key -> (name, what turning it on does, what turning it off does). The only settings /settings/feature may change.
SWITCHES = {
    "local.enabled": ("Local tickets", "New ticket will ask where a ticket lives, and Import can move GitHub issues across. Pull requests still go to GitHub.",
                      "New tickets go to GitHub again. Local tickets you already have are kept, and come back when you turn this on."),
    "ci.enabled": ("CI feedback", "The factory watches CI on its pull requests and an agent tries to fix a failure.",
                   "CI is no longer watched; failures on factory pull requests are not reported or fixed."),
    "review.enabled": ("Code review", "A second agent reviews each pull request the factory opens and comments on it. It never approves or changes code.",
                       "Pull requests are no longer reviewed by an agent."),
    "conflicts.enabled": ("Merge conflicts", "When a factory pull request conflicts with its base branch, an agent merges the base in and resolves the conflicted files.",
                          "Conflicts are no longer resolved by an agent."),
    "pm.enabled": ("Project manager", "An agent ranks each repository's factory tickets and records blockers; a build waits for its open blockers.",
                   "Tickets are no longer ranked, and builds no longer wait for blockers."),
}
APPLIES = "The factory picks it up when it is next idle; nothing running is interrupted."


@dataclass
class Feature:
    key: str                  # anchor on the Settings home, and the switch key when `switch` is set
    group: str
    name: str
    text: str
    word: str                 # the state, in a word or two
    kind: str                 # on | off | setup | info: drives the badge and the filter
    where: tuple              # the screens it changes
    href: str                 # where it is set in full
    open_label: str = "Open"
    switch: str = ""          # a SWITCHES key: offer Turn on / Turn off here


GROUPS = [("source", "Where work comes from", "What the factory picks up, and from where."),
          ("work", "How the work is done", "Who does it, with which model, and how much at once."),
          ("checks", "Before a pull request is done", "The checks a change goes through."),
          ("loop", "Keeping you in the loop", "Where you hear about it, and answer from."),
          ("run", "Running the factory", "Mode, keys, labels and safety nets.")]
FILTERS = [("", "All"), ("on", "On"), ("off", "Off"), ("setup", "Needs set-up")]


def _n(k: int, one: str, many: str = "") -> str:
    return f"{k} {one if k == 1 else (many or one + 's')}"


def _onoff(on: bool) -> tuple[str, str]:
    return ("On", "on") if on else ("Off", "off")


def features(cfg, slack_conn=None) -> list[Feature]:
    """Every feature with its state, read from the effective configuration (never from secrets: only whether they are set)."""
    secrets = {name: secret_status(cfg, name)["set"] for name in SECRETS}
    slack_on = bool(cfg.slack_channel and cfg.slack_user_id)
    slack = (("On", "on") if slack_on else ("Needs set-up", "setup") if (secrets.get("slack_bot") or secrets.get("slack_app")) else ("Off", "off"))
    if slack_on and slack_conn and not slack_conn.get("ok"):
        slack = ("Not connected", "setup")
    required = [n for n in ("github", "claude") if n in secrets]
    creds_ok = all(secrets[n] for n in required)
    mock = {"block": ("Required", "on"), "warn": ("Warn only", "on"), "off": ("Off", "off")}.get(cfg.mockups.mode, (cfg.mockups.mode, "info"))
    tiers = ", ".join(f"{t} {r.model}" for t, r in cfg.routes.items())
    F = Feature
    return [
        F("github", "source", "GitHub issues", f"Issues in {_n(len(cfg.repos), 'repository', 'repositories')} start when they carry the build label {cfg.trigger_label}.",
          "On", "on", ("Tickets", "Factory"), "/settings?section=general", "Repositories and label"),
        F("local", "source", "Local tickets", "Keep tickets in the factory (L-1, L-2 …) instead of GitHub issues. Import moves issues across; pull requests still go to GitHub.",
          *_onoff(cfg.local_enabled), ("Tickets", "New ticket", "A ticket"), "/settings?section=general", "Details", "local.enabled"),
        F("schedules", "source", "Schedules", "Tickets the factory opens on a timetable, such as a weekly dependency check.",
          _n(len(cfg.schedules), "schedule"), "info", ("Tickets", "Factory"), "/schedules", "Schedules"),
        F("projects", "source", "Projects", "Repositories that work together, each with a one-line role the agents read.",
          _n(len(cfg.projects), "project"), "info", ("Tickets", "A ticket"), "/settings?section=projects", "Projects"),
        F("comments", "source", "Comment triage", "An agent reads new comments on tickets the factory worked on and works out what they ask for.",
          *_onoff(cfg.comments.enabled), ("A ticket",), "/settings?section=comments", "Comment triage"),
        F("design_links", "source", "Design links", "Design exports linked in a ticket are fetched from hosts you list and shown to agents.",
          *_onoff(cfg.design_links.enabled), ("A ticket",), "/settings?section=design_links", "Design links"),
        F("ticket_review", "source", "Ticket review", "On request, an agent proposes which open tickets are already built or duplicates.",
          *_onoff(cfg.ticket_review.enabled), ("Tickets",), "/settings?section=ticket_review", "Ticket review"),
        F("scanner", "source", "Scan limits", "How many tickets and findings a code-smell scan may open.",
          f"{cfg.scanner.max_tickets} tickets a run", "info", ("Tickets",), "/settings?section=scanner", "Scan limits"),
        F("routing", "work", "Models by ticket size", f"Which model and effort each size of ticket gets: {tiers}.",
          _n(len(cfg.routes), "tier"), "info", ("A ticket",), "/settings?section=routing", "Models by ticket size"),
        F("roles", "work", "Stage agents", "The stages before a build: " + (", ".join(r.name for r in cfg.roles) or "none") + ".",
          _n(len(cfg.roles), "role"), "info", ("A ticket", "Factory"), "/settings?section=roles", "Stage agents"),
        F("classifier", "work", "Classifier", f"Decides each ticket's kind and size; below confidence {cfg.confidence_threshold:g} it asks you.",
          "Jev" if cfg.classifier_backend == "jev" else "Labels", "info", ("A ticket", "Needs you"), "/settings?section=classifier", "Classifier"),
        F("runner", "work", "Sandbox & limits", "How many agents run at once, their time and memory limits, and the only hosts a sandbox may reach.",
          f"{cfg.runner.max_parallel} at once", "info", ("Factory",), "/settings?section=runner", "Sandbox & limits"),
        F("harnesses", "work", "Harnesses", "The agent programs available, and the credential each one uses.",
          _n(len(cfg.harnesses) or 1, "harness", "harnesses"), "info", ("Models by ticket size", "Stage agents"), "/harnesses", "Harnesses"),
        F("workers", "work", "Verification workers", "Run a repository's checks (Android, iOS …) on another machine before anything is pushed.",
          *_onoff(cfg.workers.enabled), ("Factory", "A ticket"), "/workers", "Workers"),
        F("ci", "checks", "CI feedback", f"Watches CI on factory pull requests; after a failure an agent tries up to {_n(cfg.ci.fix_rounds, 'fix')}.",
          *_onoff(cfg.ci.enabled), ("A ticket", "PRs & CI"), "/settings?section=ci", "CI feedback", "ci.enabled"),
        F("review", "checks", "Code review", "A second agent reviews each pull request the factory opens. It never approves or changes code.",
          *_onoff(cfg.review.enabled), ("A ticket", "PRs & CI"), "/settings?section=review", "Code review", "review.enabled"),
        F("conflicts", "checks", "Merge conflicts", f"An agent resolves conflicts with the base branch, at most {cfg.conflicts.max_attempts} times per pull request.",
          *_onoff(cfg.conflicts.enabled), ("A ticket", "PRs & CI"), "/settings?section=conflicts", "Merge conflicts", "conflicts.enabled"),
        F("mockups", "checks", "Design mockups", "Whether a build waits for the designer's mockups (and, if you ask, for your approval of them).",
          *mock, ("A ticket", "Screens"), "/settings?section=mockups", "Design mockups"),
        F("pm", "checks", "Project manager", "An agent ranks tickets and records blockers; a build waits for its open blockers.",
          *_onoff(cfg.pm.enabled), ("Tickets",), "/settings?section=pm", "Project manager", "pm.enabled"),
        F("telegram", "loop", "Telegram", "Alerts, and Run or Skip buttons, from your Telegram account only.",
          *((cfg.telegram_verbosity, "on") if cfg.telegram_chat_id else ("Needs set-up", "setup") if secrets.get("telegram") else ("Off", "off")),
          ("Factory", "Needs you"), "/telegram", "Telegram"),
        F("slack", "loop", "Slack", "Alerts, buttons and /factory in a Slack channel. Set it up from start to finish on the Slack page.",
          *slack, ("Factory", "Needs you"), "/slack", "Slack"),
        F("chat", "loop", "Ticket chat", "Ask about a ticket and get a quick answer from a short read-only run.",
          *_onoff(cfg.chat.enabled), ("A ticket",), "/settings?section=chat", "Ticket chat"),
        F("screens", "loop", "Screens", "Screenshots of each app at every viewport, compared with the approved ones.",
          "Every " + cfg.screens.board_every if cfg.screens.board_every else "On request", "info", ("Screens",), "/settings?section=screens", "Screens"),
        F("mode", "run", "Mode", "Live: agents run and open pull requests. Dry run: decisions are only logged.",
          *(("Dry run", "off") if cfg.dry_run else ("Live", "on")), ("Factory",), "/?mode=confirm", "Change on the Factory"),
        F("credentials", "run", "Credentials", f"{sum(secrets.values())} of {len(secrets)} set." + ("" if creds_ok else " The factory cannot work without the GitHub token and the Claude credential."),
          *(("Set", "info") if creds_ok else ("Needs set-up", "setup")), ("Everywhere",), "/credentials", "Credentials"),
        F("general", "run", "Polling and labels", f"Checks for work every {cfg.poll_seconds} s; who may apply the labels the factory reacts to.",
          f"{cfg.poll_seconds} s", "info", ("Factory",), "/settings?section=general", "General"),
        F("labels", "run", "Labels", "The labels the factory reads and writes.", "", "info", ("Tickets",), "/settings?section=labels", "Labels"),
        F("prompts", "work", "Agent instructions", "Your own instructions for each agent, before its built-in ones.", "", "info", ("A ticket",), "/settings?section=prompts", "Agent instructions"),
        F("health", "run", "Health alerts", "Telegram alerts when disk, memory, polling or runs go wrong.",
          *_onoff(cfg.health.enabled), ("Telegram",), "/settings?section=health", "Health alerts"),
        F("update_check", "run", "Update checks", "A banner when a newer release is out.",
          *_onoff(cfg.updates.check), ("Everywhere",), "/settings?section=update_check", "Update checks"),
        F("backup", "run", "Backup", "Download the database and settings, or restore them.", "", "info", ("Settings",), "/backup", "Backup"),
    ]


def _badge(f: Feature) -> str:
    if not f.word:
        return ""
    return badge(f.word, {"on": "good", "off": "warn", "setup": "warn"}.get(f.kind, ""))


def switch_form(key: str, on: bool, csrf: str, back: str, label: str = "", open_: bool = False, primary: bool = True) -> str:
    """Turn a SWITCHES setting on or off after a confirmation: the button opens the explanation, the second button changes it."""
    name, on_text, off_text = SWITCHES[key]
    want = not on
    verb = "Turn on" if want else "Turn off"
    return (f'<details class="ft-switch"{" open" if open_ else ""} id="sw-{esc(key.split(".")[0])}"><summary class="btn{"" if primary and want else " secondary"}">{esc(label or verb)}</summary>'
            f'<form method="post" action="/settings/feature" class="ft-confirm card" aria-label="{esc(verb)} {esc(name)}">{csrf_field(csrf)}'
            f'<input type="hidden" name="key" value="{esc(key)}"><input type="hidden" name="on" value="{1 if want else 0}"><input type="hidden" name="back" value="{esc(back)}">'
            f'<p><strong>{esc(verb)} {esc(name if name.startswith("CI") else name[0].lower() + name[1:])}?</strong> {esc(on_text if want else off_text)} {APPLIES}</p>'
            f'<button>{esc(verb)}</button></form></details>')


def _card(f: Feature, csrf: str) -> str:
    where = "".join(f'<span class="ft-where">{esc(w)}</span>' for w in f.where)
    switch = switch_form(f.switch, f.kind == "on", csrf, "/settings") if f.switch and csrf else ""
    return (f'<article class="ft-card card" id="f-{esc(f.key)}" data-kind="{esc(f.kind)}"><div class="ft-top"><h3>{esc(f.name)}</h3>{_badge(f)}</div>'
            f'<p class="muted">{esc(f.text)}</p><p class="ft-shows"><span class="muted">Shows on</span> {where}</p>'
            f'<div class="ft-acts">{switch}<a class="btn secondary" href="{esc(f.href)}">{esc(f.open_label)}</a></div></article>')


def home(cfg, csrf: str, show: str = "", q: str = "", slack_conn=None) -> str:
    """The Settings home: every feature grouped by what it is for, with a search and On / Off / Needs set-up filters (both are
    plain GET parameters, so they work without scripts)."""
    fs = features(cfg, slack_conn)
    count = lambda k: sum(1 for f in fs if f.kind == k)
    show = show if show in {k for k, _ in FILTERS} else ""
    q = q.strip()[:60]
    keep = [f for f in fs if (not show or f.kind == show) and (not q or q.lower() in (f.name + " " + f.text + " " + " ".join(f.where)).lower())]
    chips = "".join(f'<a class="ft-chip{" on" if k == show else ""}" href="/settings?show={k}{"&amp;q=" + esc(quote(q)) if q else ""}"'
                    f'{" aria-current=page" if k == show else ""}>{esc(t)}{f" <b>{count(k)}</b>" if k else ""}</a>' for k, t in FILTERS)
    mode = (('<span class="ft-mode-w good">Live</span><span>Agents run and open pull requests for labelled tickets.</span>', "Switch to dry run…") if not cfg.dry_run
            else ('<span class="ft-mode-w warn">Dry run</span><span>Decisions are only logged; nothing is run or written.</span>', "Go live…"))
    attn = [f for f in fs if f.kind == "setup"]
    attn_html = ("" if not attn else '<section class="ft-attn" aria-labelledby="ft-attn-h"><h2 id="ft-attn-h">Needs your attention</h2>'
                 + "".join(f'<div class="ft-attn-row card"><div><strong>{esc(f.name)}: {esc(f.word.lower())}</strong><span class="muted">{esc(f.text)}</span></div>'
                           f'<a class="btn" href="{esc(f.href)}">Set up {esc(f.name)}</a></div>' for f in attn) + "</section>")
    head = (f'<p class="muted ft-lede">Everything the factory does, grouped by what it is for. {APPLIES}</p>'
            f'<section class="ft-mode card" aria-label="Mode"><div class="ft-mode-t">{mode[0]}</div><a class="btn secondary" href="/?mode=confirm">{mode[1]}</a></section>'
            f'{attn_html}'
            f'<div class="ft-bar"><span class="muted">{count("on")} on · {count("off")} off · {count("setup")} need set-up</span></div>'
            f'<div class="ft-tools"><form method="get" action="/settings" class="ft-search" role="search"><label for="ft-q">Find a setting</label>'
            f'<span class="ft-q"><input id="ft-q" type="search" name="q" value="{esc(q)}" placeholder="local tickets, dry run, Slack…">'
            f'{f"<input type=hidden name=show value={esc(show)}>" if show else ""}<button class="secondary">Find</button></span></form>'
            f'<nav class="ft-chips" aria-label="Show">{chips}</nav></div>')
    body = ""
    for gid, name, blurb in GROUPS:
        cards = [f for f in keep if f.group == gid]
        if cards:
            body += (f'<section class="ft-group" aria-labelledby="g-{gid}"><h2 id="g-{gid}">{esc(name)}</h2><p class="muted">{esc(blurb)}</p>'
                     f'<div class="ft-grid">{"".join(_card(f, csrf) for f in cards)}</div></section>')
    hits = _field_hits(q) if q else []
    if hits:
        body = (f'<section class="ft-group" aria-labelledby="g-fields"><h2 id="g-fields">Settings inside the pages</h2><ul class="ft-hits">'
                + "".join(f'<li><a href="{esc(href)}"><strong>{esc(label)}</strong><span class="muted">{esc(where)}</span></a></li>' for label, where, href in hits)
                + "</ul></section>") + body
    return head + (body or '<p class="muted">No setting matches. <a href="/settings">Show them all</a></p>')


def _field_hits(q: str) -> list[tuple[str, str, str]]:
    """Every single setting whose name or help mentions the search, with the page it is on, so a setting is found
    without knowing which page holds it."""
    from . import settings as S
    q, out = q.lower(), []
    for key, (title, fields) in S.SECTIONS.items():
        for f in fields:
            if q in (f.label + " " + f.help).lower():
                where = title + (" › Advanced" if f.adv else "")
                out.append((f.label, where, f"/settings?section={key}#s-{f.key}"))
    return out[:30]


def strip(items: list[tuple[str, str]], extra: str = "", label: str = "The settings that shape this page", anchor: str = "") -> str:
    """A row of (label, value html) facts about the settings behind a screen, with a link to all of them."""
    cells = "".join(f'<div class="ft-cell"><span class="ft-lab">{esc(k)}</span><span>{v}</span></div>' for k, v in items)
    ident = f' id="{esc(anchor)}"' if anchor else ""
    return (f'<section class="ft-strip card" aria-label="{esc(label)}"{ident}><div class="ft-cells">{cells}</div>{extra}'
            f'<p class="ft-foot muted">{esc(label)}. <a href="/settings">All settings</a></p></section>')


def tickets_strip(cfg, csrf: str, ask: bool = False) -> str:
    """Tickets: where tickets live, the build label, schedules, and the local-tickets switch (opened when New ticket sent you here)."""
    live_in = "GitHub and the factory" if cfg.local_enabled else f"GitHub · {_n(len(cfg.repos), 'repository', 'repositories')}"
    local = (badge("On", "good") + ' <span class="muted">L-1, L-2 … live in the factory</span>' if cfg.local_enabled
             else badge("Off", "warn") + ' <span class="muted">every ticket is a GitHub issue</span>')
    items = [("Tickets live in", esc(live_in)), ("Build label", f'<code>{esc(cfg.trigger_label)}</code>'),
             ("Schedules", f'<a href="/schedules">{esc(_n(len(cfg.schedules), "schedule"))}</a>'), ("Local tickets", local)]
    sw = switch_form("local.enabled", cfg.local_enabled, csrf, "/tickets", "Keep tickets in the factory" if not cfg.local_enabled else "Turn off",
                     open_=ask, primary=False) if csrf else ""
    return strip(items, f'<div class="ft-strip-acts">{sw}</div>' if sw else "", "The settings that shape Tickets", "tk-settings")


def factory_strip(cfg, paused, csrf: str) -> str:
    """The Factory: mode, whether it takes new work, agents at once, and where it tells you, plus the checks that are off."""
    slack = (badge(cfg.slack_verbosity, "good") if cfg.slack_channel and cfg.slack_user_id else '<a href="/slack">Set up</a>')
    tg = badge(cfg.telegram_verbosity, "good") if cfg.telegram_chat_id else '<a href="/telegram">Set up</a>'
    n = cfg.runner.max_parallel
    step = lambda v, sign, lab: (f'<form method="post" action="/settings/parallel" class="inline">{csrf_field(csrf)}<input type="hidden" name="value" value="{v}">'
                                 f'<button class="secondary ft-step" aria-label="{lab}"{" disabled" if not 1 <= v <= 8 else ""}>{sign}</button></form>')
    agents = f'<span class="ft-stepper">{step(n - 1, "−", "Fewer agents at once")}<b class="mono">{n}</b>{step(n + 1, "+", "More agents at once")}</span>' if csrf else f"<b>{n}</b>"
    items = [("Mode", badge("Live", "good") if not cfg.dry_run else badge("Dry run", "warn")),
             ("Taking new work", badge("Paused", "warn") if paused else badge("Yes", "good")),
             ("Agents at once", agents),
             ("Telling you", f'Telegram {tg} · Slack {slack}')]
    off = [(name, key) for key, (name, _, _) in SWITCHES.items() if key != "local.enabled" and not _enabled(cfg, key)]
    if not cfg.local_enabled:
        off.insert(0, ("Local tickets", "local.enabled"))
    extra = ""
    if off:
        extra = ('<p class="ft-off"><span class="muted">Off:</span> '
                 + " · ".join(f'<a href="/settings#f-{esc(k.split(".")[0])}">{esc(nm)}</a>' for nm, k in off) + "</p>")
    return strip(items, extra, "The settings that shape the factory right now")


def _enabled(cfg, key: str) -> bool:
    if key == "local.enabled":
        return cfg.local_enabled
    section, _ = key.split(".")
    return bool(getattr(getattr(cfg, section), "enabled"))


def ticket_handling(cfg, repo: str, issue: int, detail: str = "") -> str:
    """How one ticket is handled: its tier and what that tier runs, the stages, and the checks; each links to where it is set."""
    import re
    m = re.search(r"classified \w+/(\w+)", detail or "")
    tier = m.group(1) if m and m.group(1) in cfg.routes else None
    route = cfg.routes.get(tier or "medium")
    rows = [("Size", f'{esc(tier)} <span class="muted">(classifier)</span>' if tier else '<span class="muted">not classified yet; medium is the default</span>'),
            ("Model and effort", f'<span class="mono">{esc(route.model)}</span> · {esc(route.effort)}' if route else "—"),
            ("Harness", esc(route.harness) if route else "—"),
            ("Stages", esc(" → ".join([r.name for r in cfg.roles] + ["build"]))),
            ("Asks you between stages", "Yes" if cfg.auto_confirm_stages else "No"),
            ("CI fixes", (badge("on", "good") + f" up to {cfg.ci.fix_rounds}") if cfg.ci.enabled else badge("off", "warn")),
            ("Code review", badge("on", "good") if cfg.review.enabled else badge("off", "warn")),
            ("Merge conflicts", badge("on", "good") if cfg.conflicts.enabled else badge("off", "warn"))]
    dl = "".join(f'<div class="ft-row"><dt>{esc(k)}</dt><dd>{v}</dd></div>' for k, v in rows)
    return (f'<section class="sd-card ft-handling" aria-labelledby="hd-h"><div class="sd-cardhead"><h3 id="hd-h">How {esc(display(int(issue)))} is handled</h3></div>'
            f'<dl>{dl}</dl><p class="muted sd-fine">These apply to every ticket of this size. '
            '<a href="/settings?section=routing">Models by ticket size</a> · <a href="/settings?section=roles">Stage agents</a> · <a href="/settings#f-ci">Checks</a></p></section>')
