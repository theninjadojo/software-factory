"""The Board view of Tickets: every ticket as a card in a column for its state, and a Move to menu on each card.

A card's column is its derived state (board.ticket_state), never stored, so a move is only ever a request that reuses a route the
factory already has: Start (/tickets/start, Auto or a stage, never Build), Close (/tickets/close) or Reopen (/tickets/local/state).
Those handlers re-read the ticket and check everything again; this page only decides what to offer. Every move is a form with a
confirming button, so a drag only opens the card's menu at the column it was dropped on. Every text is escaped."""
from . import board, labels as L
from .views import csrf_field, esc, ref

COLUMNS = ("new", "working", "prs", "needs", "failed", "done")
PER_COLUMN = 30
NOT_AVAILABLE = "The factory has no action that does this. Nothing was changed."
NEEDS_TOKEN = "Save a GitHub token on the Credentials page to move GitHub tickets."


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


def is_local_row(row: dict) -> bool:
    from ..tracker import is_local
    return is_local(row["issue"])


def _form(action: str, row: dict, csrf: str, back: str, fields: str, button: str) -> str:
    return (f'<form method="post" action="{action}" class="kb-form">{csrf_field(csrf)}<input type="hidden" name="repo" value="{esc(row["repo"])}">'
            f'<input type="hidden" name="n" value="{int(row["issue"])}"><input type="hidden" name="back" value="{esc(back)}">{fields}'
            f'<button>{esc(button)}</button></form>')


def _option(col: str, kind: str, why: str, row: dict, cfg, csrf: str, back: str) -> str:
    word = board.TICKET_WORD[col]
    n = esc(ref(row["repo"], row["issue"], short=True))
    if not kind:
        return f'<div class="kb-opt off" data-to="{col}" aria-disabled="true"><b>{esc(word)}</b> (not available)<span class="muted">{esc(why)}</span></div>'
    if kind == "start":
        opts = "".join(f'<option value="{esc(k)}">{esc(t)}</option>' for k, t, _ in L.start_options(cfg)[1:])
        form = _form("/tickets/start", row, csrf, back, f'<label>Start with<select name="action">{opts}</select></label>', "Start ticket")
        say = f"Move {n} to {word}? This starts the ticket. Choose what starts it."
    elif kind == "close":
        busy = " The factory stops its work on it." if row["state"] in ("working", "needs") else ""
        form = _form("/tickets/close", row, csrf, back, "", "Close ticket")
        say = f"Move {n} to {word}? This closes the ticket.{busy}"
    else:
        form = _form("/tickets/local/state", row, csrf, back, '<input type="hidden" name="state" value="open">', "Reopen ticket")
        say = f"Move {n} to Not started? This reopens the ticket." if col == "new" else f"Reopen {n}? This reopens the ticket."
        word = "Reopen" if col == "done" else word
    return f'<div class="kb-opt" data-to="{col}"><b>{esc(word)}</b><span class="muted">{esc(say)}</span>{form}</div>'


def _card(row: dict, cfg, csrf: str, back: str, project: str, has_token: bool, now: float) -> str:
    n = esc(ref(row["repo"], row["issue"], short=True))
    moves = moves_for(row, has_token)
    items = "".join(_option(c, k, w, row, cfg, csrf, back) for c, (k, w) in moves.items())
    menu = (f'<details class="kb-menu"><summary aria-label="Move {n}">⋯</summary><div class="kb-opts"><p class="kb-opth">Move {n} to…</p>{items}</div></details>')
    link = (f'<a class="kb-link" href="{esc(board.ticket_url(row, "", "", "", project))}"><span class="mono muted">{n}</span>'
            f'<span class="sd-title">{esc(row["title"])}</span>'
            f'<span class="sd-word {board.TICKET_TONE[row["state"]]}"><span class="sd-dot {board.TICKET_TONE[row["state"]]}" aria-hidden="true"></span> {esc(board.TICKET_WORD[row["state"]])}</span>'
            f'<span class="sd-why muted">{esc(row["why"])}</span>{board.progress(row["stations"])}</a>')
    need = row.get("need") or {}
    acts = ""
    if need.get("acts") and not need.get("st") and csrf:
        acts = f'<div class="sd-pickacts">{L.action_forms(row["repo"], row["issue"], need["acts"], csrf, "/tickets")}</div>'
    return f'<li class="kb-card" data-card data-col="{row["state"]}" draggable="true">{menu}{link}{acts}</li>'


def _column(col: str, rows: list[dict], cfg, csrf: str, back: str, project: str, has_token: bool, now: float) -> str:
    mine = [r for r in rows if r["state"] == col]
    tone, word = board.TICKET_TONE[col], board.TICKET_WORD[col]
    cards = "".join(_card(r, cfg, csrf, back, project, has_token, now) for r in mine[:PER_COLUMN])
    more = f'<p class="muted kb-more">Showing {PER_COLUMN} of {len(mine)}. Open the list for the rest.</p>' if len(mine) > PER_COLUMN else ""
    body = f'<ul class="kb-cards">{cards}</ul>{more}' if mine else '<p class="muted kb-none">Nothing here.</p>'
    return (f'<section class="kb-col" data-col="{col}" aria-label="{esc(word)}, {len(mine)} ticket{"" if len(mine) == 1 else "s"}">'
            f'<h2><span class="sd-dot {tone}" aria-hidden="true"></span> {esc(word)} <b class="mono">{len(mine)}</b></h2>{body}</section>')


def switch(view: str, project: str, projects=()) -> str:
    """The Board | List switch (and the project filter on the board), keeping the project."""
    on = lambda v: ' class="sd-chip on" aria-current="page"' if view == v else ' class="sd-chip"'
    opt = lambda v, w: f'<option value="{esc(v)}"{" selected" if v == project else ""}>{esc(w)}</option>'
    pick = (f'<form method="get" action="/tickets" class="sd-form" data-autosubmit><input type="hidden" name="view" value="board">'
            f'<label class="sd-lab sd-project">Project <select name="project" aria-label="Filter tickets by project">{opt("", "All projects")}'
            f'{"".join(opt(v, w) for v, w in projects)}</select></label><button class="secondary sd-apply">Apply</button></form>') if projects and view == "board" else ""
    return (f'<div class="sd-filters kb-switch"><nav class="sd-chips" aria-label="Tickets view">'
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
