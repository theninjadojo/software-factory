"""The factory floor: the default page. A read-only, live picture of how a ticket moves through the factory and where work is
right now, with an inspector for the selected station and a tray of the tickets that need a person.

Everything here is server-rendered and escaped; the page's content security policy forbids inline styles, so positions live
in style.css (one class per station) and progress is a <progress> element. Nothing on this page changes state except the
tray's buttons, which post to the same /tickets/start action as the Tickets page (CSRF, re-validated server-side)."""
import time

from . import views
from .views import ago, badge, csrf_field, dur, esc

ICONS = {
    "poll": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "trust": '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/>',
    "classify": '<path d="M5 6h6l4 6h4M5 18h6l4-6"/>',
    "analyst": '<circle cx="11" cy="11" r="6"/><path d="M16 16l5 5"/>',
    "designer": '<path d="M4 20l4-1 11-11-3-3L5 16z"/>',
    "architect": '<path d="M4 20V8l8-4 8 4v12M9 20v-6h6v6"/>',
    "build": '<path d="M14 6a4 4 0 005 5l-9 9a2 2 0 01-3-3z"/>',
    "needs": '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4 4-6 8-6s8 2 8 6"/>',
    "doc": '<path d="M6 3h8l4 4v14H6z"/><path d="M9 12h6M9 16h6"/>',
    "pr": '<circle cx="6" cy="6" r="2"/><circle cx="6" cy="18" r="2"/><circle cx="18" cy="18" r="2"/><path d="M6 8v8M18 16V10a3 3 0 00-3-3h-3"/>',
    "review": '<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
    "ci": '<circle cx="12" cy="12" r="9"/><path d="M8 12l3 3 5-6"/>',
    "conflicts": '<circle cx="6" cy="6" r="2"/><circle cx="18" cy="6" r="2"/><circle cx="12" cy="19" r="2"/><path d="M6 8c0 5 6 5 6 9M18 8c0 5-6 5-6 9"/>',
}
# id -> (name, what it does, where to read more, link text)
STATIONS = {
    "poll": ("Poll", "Looks at GitHub for tickets carrying a trigger label (factory:auto, factory:ready and the stage labels).", "/events", "Events"),
    "trust": ("Trust", "Ignores a label unless it was applied by someone with write access to the repository.", "/tickets", "Tickets"),
    "classify": ("Classify", "Decides what a ticket is and which stage should run next. Low confidence goes to the analyst, or to a person.", "/events?kind=decision", "Decisions"),
    "analyst": ("Analyst", "A read-only stage. Writes the requirements as a comment on the ticket.", "/runs?status=stage", "Runs"),
    "designer": ("Designer", "A read-only stage. Writes the design, and mockups when the ticket needs them.", "/runs?status=stage", "Runs"),
    "architect": ("Architect", "A read-only stage. Writes a technical plan as a comment on the ticket.", "/runs?status=stage", "Runs"),
    "build": ("Build", "Edits the code in a sandbox. The factory validates the change and opens a pull request.", "/runs", "Runs"),
    "needs": ("Needs you", "Tickets the factory will not decide alone: low confidence, open questions, or a build it is unsure about.", "/tickets", "Tickets"),
    "doc": ("Ticket comment", "Where stage documents and their open questions are posted.", "/tickets", "Tickets"),
    "pr": ("PRs", "Pull requests the factory opened, tracked for CI and merge conflicts.", "/prs", "PRs and CI"),
    "review": ("Review", "An independent read-only reviewer that comments on a pull request. Off unless enabled.", "/settings", "Settings"),
    "ci": ("CI", "Watches the checks on each pull request and runs a fix round when they fail.", "/prs", "PRs and CI"),
    "conflicts": ("Conflicts", "Merges the base branch into a conflicting pull request. Off unless enabled.", "/prs", "PRs and CI"),
}
ORDER = tuple(STATIONS)
ROLE_STATIONS = {"analyst", "designer", "architect"}
KIND_STATION = {"implement": "build", "auto": "classify", "build": "build", "fix": "ci", "review": "review", "conflicts": "conflicts"}
RAIL = (("poll", "Intake"), ("analyst", None), ("designer", None), ("architect", None), ("build", None), ("pr", "PRs and CI"))
DAY = 86400


def span(seconds, compact: bool = False) -> str:
    """A length of time ("6m 10s", or "6m" when compact) from a number of seconds."""
    return f"{max(1, int(float(seconds)) // 60)}m" if compact and float(seconds) >= 120 else dur(time.time() - float(seconds))


def short(repo: str, issue) -> str:
    """repo#n as a link to the ticket on GitHub, with just the repository's own name shown."""
    r = str(repo)
    return views.gh_link(f"https://github.com/{r}/issues/{int(issue)}", f"{r.split('/')[-1]}#{int(issue)}") if views.REPO.match(r) else esc(f"{r}#{issue}")


def station_of(kind, stage) -> str | None:
    """Which station a run belongs to."""
    if kind == "stage":
        return stage if stage in ROLE_STATIONS else ("review" if stage == "reviewer" else None)
    return KIND_STATION.get(kind)


def gather(d: dict, needs, now: float) -> dict:
    """id -> {ns, count, count_kind, running, queued, state}: the live picture, from the database the orchestrator writes."""
    cfg, st = d["cfg"], d["status"]
    out = {sid: {"ns": "", "count": 0, "count_kind": "mute", "running": [], "queued": [], "state": ""} for sid in ORDER}
    for r in d["running"]:
        if (sid := station_of(r["kind"], r["stage"])):
            out[sid]["running"].append(r)
    for q in d["queued"]:
        kind = q.get("kind", "")
        if (sid := kind if kind in ROLE_STATIONS else KIND_STATION.get(kind)):
            out[sid]["queued"].append(q)
    last_ok = float(st["last_poll_ok"]["value"]) if "last_poll_ok" in st else None
    decided = d.get("decided", {})
    prs = d.get("prs", [])
    out["poll"]["ns"] = f"in {max(0, int(last_ok + cfg['poll_seconds'] - now))}s" if last_ok else "no data yet"
    out["trust"]["ns"] = f"{int(decided.get('ignored', 0))} ignored"
    out["classify"]["ns"] = f"{sum(int(v) for k, v in decided.items() if k != 'ignored')} today"
    for sid in ("analyst", "designer", "architect", "build", "review", "conflicts"):
        o = out[sid]
        if o["running"]:
            o["state"], o["count"], o["count_kind"] = "run", len(o["running"]), ""
            o["ns"] = f'#{int(o["running"][0]["issue"])} · {o["running"][0]["model"]}'
        else:
            o["count"] = len(o["queued"])
            o["ns"] = f'{len(o["queued"])} queued' if o["queued"] else "idle"
    n_needs = len(needs) if needs is not None else None
    out["needs"].update(ns=("unknown" if n_needs is None else f"{n_needs} waiting"), count=n_needs or 0, count_kind="red",
                        state="warn" if n_needs else "")
    done_today = sum(1 for r in d.get("recent", []) if r["status"] == "stage" and r["started"] and now - r["started"] < DAY)
    out["doc"]["ns"] = f"{done_today} posted today"
    out["pr"]["ns"] = f"{len(prs)} open"
    out["ci"]["ns"] = f"{len(prs)} PRs" if cfg["ci"] == "on" else "off"
    if cfg["ci"] != "on":
        out["ci"]["state"] = "off"
    if not cfg.get("review"):
        out["review"].update(ns="off", state="off")
    if not cfg.get("conflicts"):
        out["conflicts"].update(ns="off", state="off")
    else:
        out["conflicts"]["ns"] = out["conflicts"]["ns"] if out["conflicts"]["running"] else f'{d.get("conflicting", 0)} now'
    return out


def default_station(live: dict) -> str:
    return next((sid for sid in ORDER if live[sid]["running"]), "classify")


# ------------------------------------------------------------------ pieces
def icon(sid: str) -> str:
    return f'<svg class="fl-i" viewBox="0 0 24 24" aria-hidden="true">{ICONS[sid]}</svg>'


def node(sid: str, o: dict, sel: str) -> str:
    name = STATIONS[sid][0]
    cls = " ".join(x for x in ("fl-n", f"fl-{sid}", o["state"], "sel" if sid == sel else "") if x)
    cnt = f'<span class="fl-cnt {o["count_kind"]}">{int(o["count"])}</span>' if o["count"] else ""
    bar = ""
    if o["running"]:
        r = o["running"][0]
        bar = f'<progress class="fl-bar" max="100" value="{progress(r, None)}" aria-label="Progress of the run"></progress>'
    label = f'{name}, {o["ns"]}' + (", selected" if sid == sel else "")
    return (f'<a class="{cls}" href="/?station={sid}" aria-label="{esc(label)}"{" aria-current=true" if sid == sel else ""}>{cnt}'
            f'<span class="fl-ico">{icon(sid)}</span><span><span class="fl-nt">{esc(name)}</span><span class="fl-ns">{esc(o["ns"])}</span></span>{bar}</a>')


def progress(run: dict, avg) -> int:
    """A guess in percent: elapsed against the average run of this kind (or ten minutes). Never 100 while it is still running."""
    expected = max(60.0, float(avg or 600))
    return int(min(95, 100 * (time.time() - float(run["started"] or time.time())) / expected))


def belts(live: dict) -> str:
    hot = lambda sid: " hot" if live[sid]["running"] else ""
    any_run = " hot" if any(o["running"] for o in live.values()) else ""
    parts = ['<span class="fl-b b-poll"></span>', '<span class="fl-b b-trust"></span>', f'<span class="fl-b b-cls{any_run}"></span>',
             '<span class="fl-b v b-trunk"></span>']
    for sid in ("analyst", "designer", "architect", "build", "needs"):
        parts.append(f'<span class="fl-b b-in-{sid}{hot(sid)}"></span>')
    for sid in ("analyst", "designer", "architect"):
        parts.append(f'<span class="fl-b b-out-{sid}{hot(sid)}"></span>')
    parts += [f'<span class="fl-b b-out-build{hot("build")}"></span>', '<span class="fl-b b-pr-review"></span>',
              '<span class="fl-b v b-pr-ci"></span>', '<span class="fl-b b-ci-conflicts"></span>']
    return "".join(parts)


def crates(live: dict) -> str:
    """A crate with the ticket number on the belt into each station that is working (or has a ticket waiting)."""
    out = []
    for sid in ("analyst", "designer", "architect", "build"):
        o = live[sid]
        run = o["running"][0] if o["running"] else None
        q = o["queued"][0] if o["queued"] else None
        if run:
            out.append(f'<span class="fl-crate c-{sid}">#{int(run["issue"])}</span>')
        elif q:
            out.append(f'<span class="fl-crate blue c-{sid}">#{int(q["issue"])}</span>')
    return "".join(out)


def rail(live: dict, sel: str) -> str:
    """The same stations as a vertical list, for phones."""
    items = []
    for sid, title in RAIL:
        o = live[sid]
        name = title or STATIONS[sid][0]
        ns = o["ns"]
        cls = "fl-s run" if o["state"] == "run" else "fl-s"
        bar = ""
        if o["running"]:
            bar = f'<progress class="fl-bar" max="100" value="{progress(o["running"][0], None)}" aria-label="Progress of the run"></progress>'
        chip = badge("running") if o["state"] == "run" else (f'<span class="badge">{int(o["count"])}</span>' if o["count"] else "")
        items.append(f'<li><a class="{cls}" href="/?station={sid}"><span class="fl-ico">{icon(sid)}</span>'
                     f'<span class="fl-grow"><span class="fl-nt">{esc(name)}</span><span class="fl-ns">{esc(ns)}</span></span>{chip}{bar}</a></li>')
    return f'<ol class="fl-rail" aria-label="Factory stations">{"".join(items)}</ol>'


def inspector(sid: str, o: dict, d: dict, now: float) -> str:
    name, desc, link, link_text = STATIONS[sid]
    mine = [r for r in d.get("recent", []) if station_of(r["kind"], r["stage"]) == sid]
    today = [r for r in mine if r["started"] and now - r["started"] < DAY]
    done = sum(1 for r in today if r["status"] in views.GOOD)
    failed = sum(1 for r in today if r["status"] in views.BAD)
    spans = [r["finished"] - r["started"] for r in today if r["finished"] and r["started"]]
    avg = sum(spans) / len(spans) if spans else None
    head = (f'<div class="fl-ihead"><span class="fl-ico big{" run" if o["state"] == "run" else ""}">{icon(sid)}</span>'
            f'<h2>{esc(name)}</h2>{badge("running") if o["state"] == "run" else ""}</div><p class="muted">{esc(desc)}</p>')
    working = ""
    for r in o["running"]:
        working += ('<div class="card fl-work"><h3>Working on</h3>'
                    f'<p><a href="/runs/{int(r["id"])}">{esc(r["kind"])}{" " + esc(r["stage"]) if r["stage"] else ""} #{int(r["id"])}</a> · '
                    f'{short(r["repo"], r["issue"])}</p><p class="fl-t">{esc(r["title"])}</p>'
                    f'<p><span class="badge">{esc(r["model"])}</span> <span class="badge">{esc(r["effort"])} effort</span></p>'
                    f'<progress class="fl-bar wide" max="100" value="{progress(r, avg)}" aria-label="Progress of the run"></progress>'
                    f'<p class="muted fl-mono">running for {esc(dur(r["started"]))}{" · usually about " + esc(span(avg)) if avg else ""}</p></div>')
    for q in o["queued"]:
        working += (f'<div class="card fl-work"><h3>{"Blocked" if q.get("reason") else "Waiting for a slot"}</h3>'
                    f'<p>{short(q.get("repo", ""), q["issue"])}</p><p class="fl-t">{esc(q.get("title", ""))}</p>'
                    + (f'<p class="muted">{esc(q["reason"])}</p>' if q.get("reason") else "") + '</div>')
    stats = ""
    if sid in ("analyst", "designer", "architect", "build", "review", "ci", "conflicts"):
        stats = ('<div class="fl-stats">'
                 f'<div class="fl-kpi"><span class="lab">Done today</span><b>{done}</b></div>'
                 f'<div class="fl-kpi"><span class="lab">Failed</span><b>{failed}</b></div>'
                 f'<div class="fl-kpi"><span class="lab">Average</span><b>{esc(span(avg, True)) if avg else "—"}</b></div></div>')
    recent = ""
    if mine:
        recent = ('<h3>Recent</h3><ul class="fl-list">' + "".join(
            f'<li>{badge(r["status"])} <a href="/runs/{int(r["id"])}">{esc(r["repo"].split("/")[-1])}#{int(r["issue"])}</a> '
            f'<span class="muted">{esc(ago(r["started"]))}</span></li>' for r in mine[:3]) + "</ul>")
    status = "" if (mine or o["running"]) else f'<p class="fl-mono">{esc(o["ns"])}</p>'
    return (f'<aside class="fl-insp" aria-label="Selected station">{head}{working}{stats}{status}{recent}'
            f'<p class="fl-links"><a class="btn secondary" href="{esc(link)}">{esc(link_text)}</a> '
            '<a class="btn secondary" href="/settings">Settings</a></p>'
            '<p class="muted fl-fine">Stations are read-only for now. Pause and reroute controls may appear here later.</p></aside>')


def kpis(d: dict, needs) -> str:
    cfg = d["cfg"]
    cell = lambda label, value, extra="": f'<div class="fl-kpi"><span class="lab">{label}</span><b>{value}{extra}</b></div>'
    return ('<div class="fl-kpis">'
            + cell("Working now", len(d["running"]), f' <span class="muted">of {int(cfg["max_parallel"])} slots</span>')
            + cell("Needs you", "—" if needs is None else f'<span class="{"bad-text" if needs else ""}">{len(needs)}</span>')
            + cell("Queued", len(d["queued"]))
            + cell("Open PRs", len(d["prs"]), ' <span class="muted">watched</span>') + "</div>")


def status_row(d: dict, csrf: str, now: float) -> str:
    cfg, st = d["cfg"], d["status"]
    last_ok = float(st["last_poll_ok"]["value"]) if "last_poll_ok" in st else None
    stale = last_ok is None or now - last_ok > max(120, 3 * cfg["poll_seconds"])
    paused, err = d["paused"], (st.get("last_error") or {}).get("value", "")
    # Runs happen on worker threads and polling goes on during them, so a missed poll means trouble even while a run is going.
    health = badge("orchestrator not reporting" if stale else "orchestrator healthy", "bad" if stale else "good")
    mode = badge("LIVE" if cfg["live"] else "dry-run", "warn" if cfg["live"] else "")
    queue = badge("paused: " + paused, "warn") if paused else badge("running", "good")
    btn = (f'<form method="post" action="/action/{"resume" if paused else "pause"}" class="inline">{csrf_field(csrf)}'
           f'<button class="secondary">{"Resume" if paused else "Pause"}</button></form>')
    return (f'<div class="fl-status">{health}<span class="muted">last poll {esc(ago(last_ok))}</span>{mode}'
            f'<span class="muted">every {esc(cfg["poll_seconds"])}s · classifier {esc(cfg["classifier"])} · Telegram {esc(cfg["telegram"])} · CI {esc(cfg["ci"])}</span>'
            f'<span class="fl-grow"></span>{queue}{btn}</div>' + (f'<p class="bad-text">last error: {esc(err)}</p>' if err else ""))


def _meter(conf: float) -> str:
    return f'<meter class="nd-meter" min="0" max="1" value="{conf:.2f}" aria-label="Confidence {conf:.2f}"></meter>'


def _row(k: int, n: dict, csrf: str, back: str) -> str:
    """One ticket waiting for a person: who and why on the left, the recommendation and the buttons on the right. A ticket with
    questions opens its question form below (inline for one or two questions, in a popup for more)."""
    from . import labels as L
    ref = f'{esc(n["repo"].split("/")[-1])}#{int(n["issue"])}'
    gh_link = views.gh_link(f'https://github.com/{n["repo"]}/issues/{int(n["issue"])}', n["title"]) if views.REPO.match(str(n["repo"])) else esc(n["title"])
    st = n.get("st")
    title = f'<span class="badge warn">{ref}</span> <span class="nd-t">{gh_link}</span>'
    age = esc(ago(n.get("at")))
    if st:
        pend = st.pending()
        total = len(st.questions)
        meta = f'The {esc(st.stage)} asked {total} question{"s" if total != 1 else ""} · {age}'
        why = (f'<span class="badge bad">{len(pend)} need{"" if len(pend) != 1 else "s"} a person</span>'
               + (f' <span class="badge good">{total - len(pend)} safe default{"s" if total - len(pend) != 1 else ""}</span>' if total > len(pend) else ""))
        hidden = (f'{csrf_field(csrf)}<input type="hidden" name="repo" value="{esc(n["repo"])}"><input type="hidden" name="n" value="{int(n["issue"])}">'
                  f'<input type="hidden" name="back" value="{esc(back)}">')
        accept = (f'<form method="post" action="/tickets/answer" class="inline">{hidden}<button name="accept" value="1" '
                  f'aria-label="Accept recommendations for {ref}">Accept recommendations</button></form>')
        form = L.question_form(n["repo"], n["issue"], st, csrf, back)
        if total <= 2:
            toggle = f'<input type="checkbox" class="nd-tog" id="nd-{k}" aria-label="Show the questions for {ref}"><label class="btn secondary" for="nd-{k}">Answer</label>'
            return (f'<article class="card nd-row questions"><div class="nd-grid"><div class="nd-main">{title}<p class="muted">{meta}</p></div>'
                    f'<div class="nd-whyc">{why}</div><div class="nd-acts">{accept}{toggle}</div></div><div class="nd-body">{form}</div></article>')
        dialog = (f'<dialog id="nd-d{k}" class="nd-dialog" aria-label="Questions for {ref}"><div class="nd-dhead"><div>{title}<p class="muted">{meta}</p></div>'
                  f'<button type="button" class="secondary" data-close aria-label="Close">Close</button></div>{form}</dialog>')
        opener = f'<button type="button" class="secondary" data-dialog="nd-d{k}">Answer…</button><noscript><a class="btn secondary" href="/tickets?repo={esc(n["repo"])}&amp;q=%23{int(n["issue"])}">Answer</a></noscript>'
        return (f'<article class="card nd-row questions"><div class="nd-grid"><div class="nd-main">{title}<p class="muted">{meta}</p></div>'
                f'<div class="nd-whyc">{why}</div><div class="nd-acts">{accept}{opener}</div></div>{dialog}</article>')
    bits = [x for x in (n.get("kind"), (n.get("complexity") or "") + " complexity" if n.get("complexity") else "") if x]
    meta = " · ".join([*bits, age])
    why = f'<span class="badge bad">{esc(n["reason"])}</span>' + (f'<span class="nd-conf">{_meter(n["conf"])}<span class="muted fl-mono">confidence {n["conf"]:.2f}</span></span>' if "conf" in n else "")
    acts = n.get("acts") or []
    rec = f'<p class="nd-rec">Recommended: <b>{esc(acts[0][1].lower())}</b></p>' if acts else ""
    return (f'<article class="card nd-row"><div class="nd-grid"><div class="nd-main">{title}<p class="muted">{meta}</p></div>'
            f'<div class="nd-whyc">{why}</div><div class="nd-acts2">{rec}<div class="nd-acts">{L.action_forms(n["repo"], n["issue"], acts, csrf, back)}</div></div></div></article>')


def tray(needs, csrf: str, forms=None, back: str = "/", flt: str = "", heading: bool = True) -> str:
    head = '<h2 id="needs">Needs you</h2>' if heading else ""
    if needs is None:
        return f'<section class="fl-tray" aria-labelledby="needs">{head}<p class="muted">Save a GitHub token on the Credentials page to see the tickets waiting for you.</p></section>'
    if not needs:
        return (f'<section class="fl-tray" aria-labelledby="needs">{head}<div class="card nd-empty"><h3>Nothing needs you</h3>'
                '<p class="muted">The factory is working through its queue. It will ask here, and on Telegram, when it needs a decision.</p>'
                '<a class="btn secondary" href="/tickets">See the tickets</a></div></section>')
    qn = sum(1 for r in needs if r.get("questions"))
    base = back.split("&need=")[0].split("?need=")[0]
    sep = "&" if "?" in base else "?"
    chip = lambda label, key, count: (f'<a class="{"on" if flt == key else ""}" href="{esc(base + (sep + "need=" + key if key else ""))}">{label} <b>{count}</b></a>')
    seg = f'<nav class="nd-seg" aria-label="Filter">{chip("All", "", len(needs))}{chip("Questions", "questions", qn)}{chip("Decisions", "decisions", len(needs) - qn)}</nav>'
    shown = [r for r in needs if (flt == "questions" and r.get("questions")) or (flt == "decisions" and not r.get("questions")) or flt not in ("questions", "decisions")]
    bulk = ""
    asking = [r for r in shown if r.get("st")]
    if len(asking) >= 2:
        items = "".join(f'<li><b>{esc(r["repo"].split("/")[-1])}#{int(r["issue"])}</b> {esc(r["title"])}<ul>'
                        + "".join(f'<li>{esc(q.id)}: {esc(q.label(q.recommended))}</li>' for q in r["st"].pending()) + '</ul></li>' for r in asking[:20])
        refs = ",".join(f'{r["repo"]}#{int(r["issue"])}' for r in asking[:20])
        bulk = (f'<details class="nd-bulk"><summary class="btn">Accept recommendations on {len(asking[:20])} tickets</summary><div class="card"><p class="muted">'
                f'These are the answers that will be recorded. Each is a recommendation you can see, and it is your decision to accept it.</p><ul>{items}</ul>'
                f'<form method="post" action="/tickets/answer-all" class="inline">{csrf_field(csrf)}<input type="hidden" name="tickets" value="{esc(refs)}">'
                f'<input type="hidden" name="back" value="{esc(back)}"><button>Confirm: accept all</button></form></div></details>')
    rows = "".join(_row(k, r, csrf, back) for k, r in enumerate(shown[:12]))
    more = f'<p class="muted"><a href="/tickets">{len(shown) - 12} more in Tickets →</a></p>' if len(shown) > 12 else ""
    top = (f'<div class="nd-head"><div>{head}<p class="muted">Tickets the factory will not decide on its own. Newest first. Finish each one right here.</p></div>{seg}</div>')
    return f'<section class="fl-tray" aria-labelledby="needs">{top}{bulk}<div class="nd-list">{rows or "<p class=muted>Nothing in this filter.</p>"}</div>{more}</section>'


def render(d: dict, csrf: str, selected: str | None = None, needs=None, forms=None, flt: str = "") -> str:
    now = time.time()
    live = gather(d, needs, now)
    sel = selected if selected in STATIONS else default_station(live)
    nodes = "".join(node(sid, live[sid], sel) for sid in ORDER)
    floor = (f'<div class="fl-grid"><div><div class="fl-wrap"><div class="fl-map" role="group" aria-label="Factory floor, live">'
             f'{belts(live)}{crates(live)}{nodes}</div></div>{rail(live, sel)}</div>{inspector(sel, live[sel], d, now)}</div>')
    out = status_row(d, csrf, now) + kpis(d, needs) + floor + tray(needs, csrf, forms, f"/?station={sel}" + (f"&need={flt}" if flt else ""), flt)
    out += f"<h2>Recent runs</h2>{views.runs_table(d['runs'])}"
    if d["prs"]:
        out += f"<h2>PRs being watched</h2>{views.prs_table(d['prs'])}"
    return out + f'<h2>Timeline</h2>{views.events_table(d["events"])}<p><a href="/events">All events →</a></p>'
