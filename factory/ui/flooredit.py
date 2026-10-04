"""The floor's edit page (/floor/edit): arrange the buildings and belts on a grid and keep the layout (floorplan.py). Behind the sign-in
like every page; saving and resetting are POSTs that arrive here CSRF-checked, like Settings. The page works without JavaScript as a
form of the layout's coordinates; static/floor-edit.js adds dragging, drawing belts, undo and redo. On phones the floor stays a
column of stations and there is nothing to edit."""
import json
import logging
import threading

from . import board, floorplan, views
from .views import esc

log = logging.getLogger("factory.ui")
LOCK = threading.Lock()                    # the server is threaded: the revision check and the write happen as one step
FLASH = {"layout_reset": "Back to the default layout. The floor is drawn as it was before any layout was saved."}
STALE = ("The layout was saved by someone else since you opened it. Save again to replace it with yours, or open the page again to "
         "start from theirs.")
TOOLS = (("move", "Move"), ("belt", "Draw belt"), ("splitter", "Splitter"), ("merger", "Merger"), ("sideload", "Side-load"),
         ("underground", "Underground"), ("erase", "Erase"))


def _ctx(h) -> "floorplan.Ctx":
    d = h.app.overview()
    return board.floor_ctx(d["cfg"], d.get("workers") or [])


def _page(h, csrf: str, ctx, text: str, rev: str, status: int = 200, flash=None, kind: str = "ok", problems=()) -> None:
    _, why = floorplan.current(h.app.state_dir(), ctx)
    note = (f'<p class="flash bad">The saved layout is not used: {esc(why)}. The floor shows the default arrangement until a layout is '
            'saved again.</p>') if why else ""
    tools = "".join(f'<button type="button" class="secondary" data-tool="{t}" aria-pressed="{"true" if t == "move" else "false"}">{esc(n)}</button>'
                    for t, n in TOOLS)
    dirs = "".join(f'<option value="{d}">{n}</option>' for d, n in (("e", "Facing east"), ("w", "Facing west"), ("s", "Facing south"), ("n", "Facing north")))
    body = ('<p><a href="/">← Factory</a></p>'
            '<p class="muted">Move the buildings on the grid, draw the belt for each hop of the route and place splitters, mergers, '
            'side-loads and underground belts. Each belt starts beside the station a ticket leaves and ends beside the one it goes to, '
            'so every station stays reachable. Moving a station changes the picture, not the workflow. New stations, agents or workers '
            'get a default spot until you place them.</p>' + note
            + '<p class="fe-phone muted">Editing the layout is for wider screens. On a phone the floor is the column of its stations.</p>'
            f'<div class="fe" data-meta="{esc(json.dumps(floorplan.meta(ctx), separators=(",", ":")))}">'
            f'<div class="fe-tools" role="toolbar" aria-label="Layout tools">{tools}'
            '<select class="fe-hop" aria-label="The belt to draw"></select>'
            f'<select class="fe-dir" aria-label="Which way the piece faces">{dirs}</select>'
            '<button type="button" class="secondary" data-act="finish">Finish belt</button>'
            '<button type="button" class="secondary" data-act="undo">Undo</button><button type="button" class="secondary" data-act="redo">Redo</button>'
            '<button type="button" class="secondary" data-act="default">Start from the default</button></div>'
            '<div class="fe-canvas"><svg class="fe-svg" role="application" aria-label="The floor layout: drag a building, or focus one and use the arrow keys"></svg></div>'
            '<ul class="fe-problems" aria-live="polite">' + "".join(f"<li>{esc(p)}</li>" for p in problems) + '</ul></div>'
            f'<form method="post" action="/floor/layout/save" class="fe-save field">{views.csrf_field(csrf)}<input type="hidden" name="rev" value="{esc(rev)}">'
            '<details class="fe-data" open><summary>The layout as data</summary>'
            f'<p class="muted">Positions and belt points are grid cells of {floorplan.G}px. A belt is a list of points, each run straight across '
            'or down; pieces sit on a belt.</p>'
            f'<label>Layout (JSON)<textarea name="plan" rows="14" spellcheck="false">{esc(text)}</textarea></label></details>'
            '<button>Save layout</button></form>'
            f'<form method="post" action="/floor/layout/reset" class="fe-reset">{views.csrf_field(csrf)}<input type="hidden" name="rev" value="{esc(rev)}">'
            '<button class="secondary">Reset to the default layout</button></form>'
            '<script src="/static/floor-edit.js" defer></script>')
    h._send(status, views.page("Floor layout", body, "/", csrf, wide=True, flash=flash, flash_kind=kind))


def edit_get(h, q: dict, csrf: str) -> None:
    ctx, state = _ctx(h), h.app.state_dir()
    plan, _ = floorplan.current(state, ctx)
    doc = plan or floorplan.default_plan(ctx)
    _page(h, csrf, ctx, json.dumps(floorplan.canonical(doc), separators=(",", ":")), floorplan.rev(state), flash=FLASH.get(q.get("ok", "")))


def save(h, form, csrf: str) -> None:
    """Check the whole layout here (the editor's own check is only a convenience), refuse a save made over someone else's, then write
    it and log a line."""
    ctx, state = _ctx(h), h.app.state_dir()
    text = form.get("plan", "")
    doc = None
    if len(text.encode()) > floorplan.MAX_BYTES:
        errs = [f"The layout is too large (at most {floorplan.MAX_BYTES // 1024} KB)."]
    else:
        try:
            doc = json.loads(text)
            errs = floorplan.validate(doc, ctx)
        except (ValueError, RecursionError):
            errs = ["The layout is not valid JSON."]
    if errs:
        return _page(h, csrf, ctx, text, form.get("rev", ""), 422, "The layout was not saved. " + errs[0], "bad", errs)
    with LOCK:
        now = floorplan.rev(state)
        if form.get("rev", "") != now:
            return _page(h, csrf, ctx, text, now, 409, STALE, "bad")
        floorplan.save(state, doc)
    log.info("floor layout saved: %d buildings, %d belts, %d pieces", len(doc["nodes"]), len(doc["belts"]), len(doc["pieces"]))
    h._redirect("/?ok=layout_saved")


def reset(h, form, csrf: str) -> None:
    state = h.app.state_dir()
    with LOCK:
        now = floorplan.rev(state)
        if form.get("rev", "") != now:
            ctx = _ctx(h)
            plan, _ = floorplan.current(state, ctx)
            text = json.dumps(floorplan.canonical(plan or floorplan.default_plan(ctx)), separators=(",", ":"))
            return _page(h, csrf, ctx, text, now, 409, STALE, "bad")
        floorplan.reset(state)
    log.info("floor layout reset to the default")
    h._redirect("/floor/edit?ok=layout_reset")
