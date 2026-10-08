"""The Board view of Tickets: every ticket as a card in a column for its state, and a menu on each card to start or move it.

A card's column is its derived state (board.ticket_state), never stored, so a move is only ever a request that reuses a route the
factory already has: Start (/tickets/start, with Auto, a stage, Build, Review or Resolve conflicts), Close (/tickets/close) or
Reopen (/tickets/local/state). Those handlers re-read the ticket and check everything again; this page only decides what to offer.
Every choice opens a confirmation with its own button, so a drag only opens the card's menu and a stray click changes nothing.
Done cards leave the board [board] done_days after their last activity; the list still shows them. A card shows its priority
like the list does (pinned by a person, or set by the project manager). Every text is escaped."""
from . import board, labels as L
from .views import ago, csrf_field, esc, ref

COLUMNS = ("new", "working", "prs", "needs", "failed", "done")
PER_COLUMN = 30
NOT_AVAILABLE = "The factory has no action that does this. Nothing was changed."
NEEDS_TOKEN = "Save a GitHub token on the Credentials page to move GitHub tickets."
RUNNING = "The factory is working on it. Nothing can start until this run finishes or fails."
ASKING = "It is waiting for an answer. Answer it on the card or the ticket page, and the run carries on."
ICON = {**board.ICON, "auto": board.ICON["route"], "conflicts": "M6 3v18M18 3v6a6 6 0 0 1-6 6H6", "close": "M5 12l5 5 9-10",
        "reopen": "M4 12a8 8 0 1 0 3-6.2M4 4v5h5"}
CHEVRON = '<svg class="kb-chev" viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6l6 6-6 6"/></svg>'


def moves_for(row: dict, has_token: bool) -> dict[str, tuple[str, str]]:
    """{target column: (kind, reason)} for one card: kind is "start", "close", "reopen" or "" (not offered, with the reason).
    The current column is left out. Only a decision about what to offer: the handlers check every request again."""
    here, local = row["state"], is_local_row(row)
    out = {}
    for col in COLUMNS:
        if col == here:
            continue
        kind, why = "", NOT_AVAILABLE
        if col == "working" and here == "new":
            kind = "start"
        elif col == "done" and here != "done":
            kind = "close"
        elif col == "new" and here == "done" and local and row.get("closed") and not row["journey"]["steps"] and not row["prs"]:
            kind = "reopen"
        if kind and not local and not has_token:
            kind, why = "", NEEDS_TOKEN
        out[col] = (kind, why)
    if here == "done" and local and row.get("closed") and "new" not in {c for c, (k, _) in out.items() if k}:
        out["done"] = ("reopen", "")             # a closed ticket that has run reopens in place; its card stays in Done
    return out


def starts_for(row: dict, cfg, has_token: bool) -> tuple[str, list[tuple[str, str, str, str]]]:
    """(why nothing can start, or ""; [(action, word, what it does, why it is not offered or "")]): the choices /tickets/start
    accepts (labels.actions_for), read from the card's stations. A stage that has finished is shown, not offered. Build only runs
    a ticket again (failed, or with a pull request); a new ticket starts with Auto or a stage."""
    here = row["state"]
    if here == "done":
        return "", []
    if not is_local_row(row) and not has_token:
        return NEEDS_TOKEN, []
    if here == "working" or "run" in row["stations"].values():
        return RUNNING, []
    if here == "needs":
        return ASKING, []
    again = here in ("failed", "prs")
    out = [("auto", "Auto", "Runs it again. The factory picks where to start" if again else "The factory reads it and picks the route", "")]
    for r in cfg.roles:
        done = row["stations"].get(r.name) == "done"
        out.append((r.name, L.VERBS.get(r.name, r.name.capitalize()), f"Starts at the {r.name.capitalize()} stage", "already done" if done else ""))
    if again:
        out.append(("build", "Build", "Builds again without the earlier stages", ""))
    if here == "prs" and cfg.review.enabled:
        out.append(("review", "Review", "Reviews the open pull request", ""))
    if here == "prs" and cfg.conflicts.enabled:
        out.append(("conflicts", "Resolve conflicts", "Fixes merge conflicts on the pull request", ""))
    return "", out


def is_local_row(row: dict) -> bool:
    from ..tracker import is_local
    return is_local(row["issue"])


def _ico(key: str, tone: str) -> str:
    return f'<span class="kb-ico {tone}" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="{ICON.get(key, ICON["auto"])}"/></svg></span>'


def _choice(key: str, to: str, word: str, say: str, tone: str, ask: str, form: str) -> str:
    """One choice: its row opens the confirmation (what will happen and the one button that does it); the row again goes back."""
    return (f'<details class="kb-opt" data-to="{to}"><summary>{_ico(key, tone)}<span class="kb-ot"><b>{esc(word)}</b>'
            f'<span class="muted">{esc(say)}</span></span>{CHEVRON}</summary><div class="kb-conf"><p>{esc(ask)}</p>{form}</div></details>')


def _off(key: str, word: str, why: str) -> str:
    return (f'<div class="kb-opt off" aria-disabled="true">{_ico(key, "none")}<span class="kb-ot"><span>{esc(word)}</span>'
            f'<span class="muted">{esc(why)}</span></span></div>')


def _form(action: str, row: dict, csrf: str, back: str, fields: str, button: str, cls: str = "") -> str:
    return (f'<form method="post" action="{action}" class="kb-form">{csrf_field(csrf)}<input type="hidden" name="repo" value="{esc(row["repo"])}">'
            f'<input type="hidden" name="n" value="{int(row["issue"])}"><input type="hidden" name="back" value="{esc(back)}">{fields}'
            f'<button{f" class={cls}" if cls else ""}>{esc(button)}</button></form>')


BLOCKED = " While the project manager is on, it waits until the tickets blocking it are closed."


def _ask(action: str, word: str, n: str, pm: bool = False) -> tuple[str, str]:
    """(what will happen, the button) for a start. pm: the project manager is on, so a build (Auto or Build) with an open
    blocker waits for it (pm.py); the person is told before they press."""
    wait = BLOCKED if pm else ""
    if action == "auto":
        return f"Start {n} with Auto? This adds the auto label: the factory reads it and picks the route.{wait}", "Start with Auto"
    if action == "build":
        return f"Build {n} now? The earlier stages are not run again.{wait}", "Build now"
    if action == "review":
        return f"Review the pull request for {n}?", "Run review"
    if action == "conflicts":
        return f"Resolve the merge conflicts on the pull request for {n}?", "Resolve conflicts"
    return f"Start {n} with {word}? It starts at the {action.capitalize()} stage.", f"Start with {word}"


def menu(row: dict, cfg, csrf: str, back: str, has_token: bool) -> str:
    """The card's menu: Start (Auto, each stage and the reruns /tickets/start accepts) and Move (Done, or Reopen)."""
    n = ref(row["repo"], row["issue"], short=True)
    note, starts = starts_for(row, cfg, has_token)
    rows = ""
    for action, word, say, why in starts:
        if why:
            rows += _off(action, word, why)
            continue
        ask, button = _ask(action, word, n, cfg.pm.enabled)
        form = _form("/tickets/start", row, csrf, back, f'<input type="hidden" name="action" value="{esc(action)}">', button)
        rows += _choice(action, "working", word, say, "wait" if action == "auto" else "run", ask, form)
    start = ""
    if row["state"] != "done":
        start = ('<section class="kb-sec"><p class="kb-grp">Start' + (" <span>· moves it to Working</span>" if rows else "") + "</p>"
                 + (f'<p class="kb-note">{esc(note)}</p>' if note else "") + rows + "</section>")
    moves = moves_for(row, has_token)
    move = ""
    kind, why = moves.get("done", ("", ""))
    if kind == "close":
        busy = " The factory stops its work on it." if row["state"] in ("working", "needs") else ""
        move = _choice("close", "done", "Done", "Closes it and stops the factory’s work" if busy else "Closes the ticket", "done",
                       f"Close {n}?{busy}", _form("/tickets/close", row, csrf, back, "", "Close ticket", "danger"))
    elif why:
        move = _off("close", "Done", why)
    reopen = next((c for c, (k, _) in moves.items() if k == "reopen"), None)
    if reopen:
        move += _choice("reopen", reopen, "Reopen", "Opens the ticket again", "none", f"Reopen {n}?",
                        _form("/tickets/local/state", row, csrf, back, '<input type="hidden" name="state" value="open">', "Reopen ticket"))
    if not start and not move:
        move = '<p class="kb-note">Closed. Its ticket page has everything it did.</p>'
    move = f'<section class="kb-sec"><p class="kb-grp">Move</p>{move}</section>'
    return (f'<details class="kb-menu"><summary aria-label="Start or move {esc(n)}"><svg viewBox="0 0 24 24" aria-hidden="true">'
            '<circle cx="5" cy="12" r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="19" cy="12" r="1.8"/></svg></summary>'
            f'<div class="kb-opts" role="group" aria-label="Start or move {esc(n)}"><p class="kb-opth">{esc(n)}: start or move</p>{start}{move}</div></details>')


def _at(row: dict) -> str:
    """Where the ticket is: its station, coloured like the station's state, and how far along the line; or Not started / Shipped."""
    if row["state"] == "done":
        return '<span class="kb-at done">Shipped</span>'
    if row["state"] == "new" or not row["at"]:
        return '<span class="kb-at none">Not started</span>'
    ids = [sid for sid, _ in board.STATIONS]
    tone = row["stations"][row["at"]] if row["stations"][row["at"]] in ("done", "run", "wait", "fail") else "none"
    return f'<span class="kb-at {tone}">{esc(board.SHORT[row["at"]])} · {ids.index(row["at"]) + 1}/{len(ids)}</span>'


def _card(row: dict, cfg, csrf: str, back: str, project: str, has_token: bool, now: float) -> str:
    n = esc(ref(row["repo"], row["issue"], short=True))
    local = ('<span class="kb-local">Local</span>' if is_local_row(row) else "") + board.prio_chip(row.get("priority", "normal"), row.get("prio_src", ""))
    link = (f'<a class="kb-link" href="{esc(board.ticket_url(row, "", "", "", project))}"><span class="kb-ref"><span class="mono muted">{n}</span>{local}</span>'
            f'<span class="sd-title">{esc(row["title"])}</span><span class="sd-why muted">{esc(row["why"])}</span>{board.progress(row["stations"])}'
            f'<span class="kb-foot">{_at(row)}<span class="mono muted">{esc(ago(row["when"], now) if row["when"] else "")}</span></span></a>')
    need = row.get("need") or {}
    acts = ""
    if need.get("acts") and not need.get("st") and csrf:
        acts = f'<div class="sd-pickacts">{L.action_forms(row["repo"], row["issue"], need["acts"], csrf, "/tickets")}</div>'
    return f'<li class="kb-card" data-card data-col="{row["state"]}" draggable="true">{link}{acts}{menu(row, cfg, csrf, back, has_token)}</li>'


def recent(rows: list[dict], days: int, now: float) -> tuple[list[dict], int]:
    """(the Done cards to show, how many are older): a card leaves the board `days` after its last activity. A ticket with no
    known activity time stays. Nothing on the ticket changes; the list shows them all."""
    cut = now - days * 86400
    keep = [r for r in rows if not r["when"] or r["when"] >= cut]
    return keep, len(rows) - len(keep)


def _days(n: int) -> str:
    return f'{n} day{"" if n == 1 else "s"}'


def _column(col: str, rows: list[dict], cfg, csrf: str, back: str, project: str, has_token: bool, now: float) -> str:
    mine = [r for r in rows if r["state"] == col]
    tone, word = board.TICKET_TONE[col], board.TICKET_WORD[col]
    old = 0
    if col == "done":
        mine, old = recent(sorted(mine, key=lambda r: -(r["when"] or 0)), cfg.board_done_days, now)
        word = f"Done · last {_days(cfg.board_done_days)}"
    cards = "".join(_card(r, cfg, csrf, back, project, has_token, now) for r in mine[:PER_COLUMN])
    more = f'<p class="muted kb-more">Showing {PER_COLUMN} of {len(mine)}. Open the list for the rest.</p>' if len(mine) > PER_COLUMN else ""
    body = f'<ul class="kb-cards">{cards}</ul>{more}' if mine else '<p class="muted kb-none">Nothing here.</p>'
    if old:
        body += (f'<div class="kb-archived"><p>{old} older ticket{"" if old == 1 else "s"} archived</p>'
                 f'<p class="muted">Done cards leave the board {_days(cfg.board_done_days)} after their last change. Nothing on the ticket changes.</p>'
                 f'<a href="/tickets?{esc(board._qs(stage="done", project=project))}">See them in the list</a></div>')
    return (f'<section class="kb-col" data-col="{col}" aria-label="{esc(board.TICKET_WORD[col])}, {len(mine)} ticket{"" if len(mine) == 1 else "s"}">'
            f'<h2><span class="sd-dot {tone}" aria-hidden="true"></span> {esc(word)} <b class="mono kb-count {tone}">{len(mine)}</b></h2>{body}</section>')


def switch(view: str, project: str, projects=()) -> str:
    """The Board | List switch (and the project filter on the board), keeping the project."""
    on = lambda v: ' class="sd-chip on" aria-current="page"' if view == v else ' class="sd-chip"'
    opt = lambda v, w: f'<option value="{esc(v)}"{" selected" if v == project else ""}>{esc(w)}</option>'
    pick = (f'<form method="get" action="/tickets" class="sd-form" data-autosubmit><input type="hidden" name="view" value="board">'
            f'<label class="sd-lab sd-project">Project <select name="project" aria-label="Filter tickets by project">{opt("", "All projects")}'
            f'{"".join(opt(v, w) for v, w in projects)}</select></label><button class="secondary sd-apply">Apply</button></form>') if projects and view == "board" else ""
    return (f'<div class="sd-filters"><nav class="sd-chips" aria-label="Tickets view">'
            f'<a{on("board")} href="/tickets?{esc(board._qs(view="board", project=project))}">Board</a>'
            f'<a{on("list")} href="/tickets?{esc(board._qs(project=project))}">List</a></nav>{pick}</div>')


def board_page(rows: list[dict], cfg, csrf: str, project: str, projects, has_token: bool, now: float, github: bool) -> str:
    """The Board view's whole page body. rows are already narrowed to the project."""
    back = "/tickets?" + board._qs(view="board", project=project)
    note = ""
    if github and not has_token and any(not is_local_row(r) for r in rows):
        note = f'<p class="muted sd-scope" role="status">GitHub tickets cannot be moved until a GitHub token is saved on the Credentials page. Local tickets work.</p>'
    if not rows:
        grid = ('<div class="sd-empty sd-emptybox"><p><strong>No tickets yet.</strong></p>'
                '<p class="muted">Add a ticket and it appears here as a card.</p></div>')
    else:
        grid = '<div class="kb-board">' + "".join(_column(c, rows, cfg, csrf, back, project, has_token, now) for c in COLUMNS) + "</div>"
    return ('<div class="sd-page kb-page"><div class="sd-pagehead"><div class="sd-h1row"><h1>Tickets</h1></div></div>'
            + switch("board", project, projects) + note
            + '<p class="kb-alert" role="alert" data-kb-alert hidden></p>' + grid + "</div>")
