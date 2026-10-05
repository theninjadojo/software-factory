"""The floor's edit page (/floor/edit): arrange the buildings and belts on a grid and keep the layout (floorplan.py). Behind the sign-in
like every page; saving and resetting are POSTs that arrive here CSRF-checked, like Settings. The page works without JavaScript as a
form of the layout's coordinates; static/floor-edit.js adds dragging, drawing belts, undo and redo. On phones the floor stays a
column of stations and there is nothing to edit."""
import json
import logging
import threading

from . import board, floorplan, plant, terrain, views
from .town_art import ART as TOWN
from .views import esc

log = logging.getLogger("factory.ui")
LOCK = threading.Lock()                    # the server is threaded: the revision check and the write happen as one step
FLASH = {"layout_reset": "Back to the default layout. The floor is drawn as it was before any layout was saved."}
STALE = ("The layout was saved by someone else since you opened it. Save again to replace it with yours, or open the page again to "
         "start from theirs.")
TOOLS = (("move", "Move"), ("belt", "Draw belt"), ("rail", "Draw track"), ("splitter", "Splitter"), ("merger", "Merger"), ("sideload", "Side-load"),
         ("underground", "Underground"), ("erase", "Erase"))
# the terrain tools (terrain.py), in the groups of the design's terrain sandbox
TERRAIN_TOOLS = (("Scenery", (("tree", "Tree"), ("pine", "Pine"), ("bush", "Bush"), ("rock", "Rock"))),
                 ("Water", (("pond", "Pond"), ("river", "River"))),
                 ("Ground", (("g-grass", "Grass"), ("g-dirt", "Dirt"), ("g-sand", "Sand"), ("g-concrete", "Concrete"), ("g-water", "Water"))),
                 ("Build", (("wall", "Wall"), ("fence", "Fence"), ("gate", "Gate"), ("road", "Road"), ("hazard", "Hazard"))),
                 ("Light", (("lamp", "Lamp"), ("fog", "Fog"), ("shade", "Shade"))),
                 ("Park", (("fetch", "Fetch"), ("playground", "Playground"), ("picnic", "Picnic"), ("bench", "Bench"), ("dogwalk", "Dog walker"))))
# the town (town_art.py, generated from the design): one group of tools per kind of piece, in the order the design shows them
TERRAIN_TOOLS += tuple((g, tuple((k, a["name"]) for k, a in TOWN.items() if a["group"] == g))
                       for g in dict.fromkeys(a["group"] for a in TOWN.values()))


def _ctx(h) -> "floorplan.Ctx":
    d = h.app.overview()
    return board.floor_ctx(d["cfg"], d.get("workers") or [])


def _page(h, csrf: str, ctx, text: str, rev: str, status: int = 200, flash=None, kind: str = "ok", problems=()) -> None:
    _, why = floorplan.current(h.app.state_dir(), ctx)
    note = (f'<p class="flash bad">The saved layout is not used: {esc(why)}. The floor shows the default arrangement until a layout is '
            'saved again.</p>') if why else ""
    shown = [(t, n) for t, n in TOOLS if t != "rail" or ctx.yard_on]
    tools = "".join(f'<button type="button" class="secondary" data-tool="{t}" aria-pressed="{"true" if t == "move" else "false"}">{esc(n)}</button>'
                    for t, n in shown)
    dirs = "".join(f'<option value="{d}">{n}</option>' for d, n in (("e", "Facing east"), ("w", "Facing west"), ("s", "Facing south"), ("n", "Facing north")))
    art = "".join(f'<g data-art="{esc(nid)}">{plant.node_art(nid, m["label"], ctx.trains)}</g>'
                  for nid, m in floorplan.meta(ctx)["nodes"].items())
    body = ('<p><a href="/">← Factory</a></p>'
            '<p class="muted">Start from the default or from an empty floor, and bring buildings in from the parts tray: drag one onto the '
            'floor, or click it. Move the buildings on the grid and drag a corner to resize one. With Draw belt, drag from the building a '
            'ticket leaves onto the one it goes to (or click the belt\'s points); with Move, drag a belt sideways to slide it. Place '
            'splitters, mergers, side-loads and underground belts. Paint the land round it with the terrain tools: trees, bushes and rocks, '
            'ponds and rivers (belts and track cannot cross water), ground tiles, walls (a belt goes under one with an underground belt), '
            'fences and gates (trains pass only at a gate), roads, hazard zones (nothing may be built in one), lamps, fog and shade; birds '
            'and ducks move in by themselves. Cars drive on every road, and where track crosses a road it goes over a bridge. Add a park: '
            'fetch with a dog, a playground, a picnic, a bench whose old man walks to the nearest water to feed the ducks, and a dog walker. A town goes in the same way: zones, shops, civic, leisure and utility buildings, landmarks, nature, vehicles and small moments. '
            'Buildings, the park and the trees, bushes, rocks and ponds have hitboxes, so nothing is placed on top of another (Show '
            'hitboxes draws them); cars, birds and trains pass over and under. The harbor goes anywhere: its belt takes the ships\' '
            'tickets to Receiving. Notifiers are wireless: put them anywhere, nothing to connect. Workers are train stops outside the factory: '
            'with Draw track, lay track from the yard to each worker, on to the Train station and back to the yard; tracks can join at a '
            'junction, whose signals let one train onto the shared track at a time. Drag the ground to pan; zoom with the '
            'buttons or Ctrl and the mouse wheel. Every hop of the route needs its belt, so every station stays reachable. Moving a station '
            'changes the picture, not the workflow. New stations, agents or workers get a default spot until you place them.</p>' + note
            + '<p class="fe-phone muted">Editing the layout is for wider screens. On a phone the floor is the column of its stations.</p>'
            f'<div class="fe" data-meta="{esc(json.dumps(floorplan.meta(ctx), separators=(",", ":")))}">'
            f'<div class="fe-tools" role="toolbar" aria-label="Layout tools">{tools}'
            '<select class="fe-hop" aria-label="The belt to draw"></select>'
            f'<select class="fe-dir" aria-label="Which way the piece faces">{dirs}</select>'
            '<button type="button" class="secondary" data-act="finish">Finish belt</button>'
            '<button type="button" class="secondary" data-act="undo">Undo</button><button type="button" class="secondary" data-act="redo">Redo</button>'
            '<button type="button" class="secondary" data-act="scratch">Start from scratch</button>'
            '<button type="button" class="secondary" data-act="default">Start from the default</button>'
            '<button type="button" class="secondary" data-act="hitboxes" aria-pressed="false">Show hitboxes</button>'
            '<span class="fe-zoom"><button type="button" class="secondary" data-act="zoomout" aria-label="Zoom out">−</button>'
            '<output class="fe-zoomval" aria-live="polite">100%</output>'
            '<button type="button" class="secondary" data-act="zoomin" aria-label="Zoom in">+</button>'
            '<button type="button" class="secondary" data-act="fit">Fit</button>'
            '<button type="button" class="secondary" data-act="full">Full screen</button></span></div>'
            '<div class="fe-tools fe-terrain-tools" role="toolbar" aria-label="Terrain tools">' + "".join(
                f'<span class="fe-group"><span class="lab">{esc(g)}</span>' + "".join(
                    f'<button type="button" class="secondary" data-tool="{t}" aria-pressed="false">'
                    + (f'<i class="sw sw-{t[2:]}" aria-hidden="true"></i>' if t.startswith("g-") else "") + f'{esc(n)}</button>' for t, n in tools) + '</span>'
                for g, tools in TERRAIN_TOOLS) + '</div>'
            f'<svg class="fm fe-art" aria-hidden="true" width="0" height="0" focusable="false">{terrain.defs()}{art}</svg>'
            '<div class="fe-status"><p class="fe-msg" aria-live="polite"></p><button type="button" class="secondary fe-remove" hidden>Remove</button>'
            '<ul class="fe-legend"><li><i class="lg-belt"></i>Belt</li><li><i class="lg-in"></i>Deliveries in</li>'
            + ('<li><i class="lg-rail"></i>Rail</li>' if ctx.yard_on else '') + '<li><i class="lg-radio"></i>Notifier, wireless</li></ul></div>'
            '<div class="fe-work"><aside class="fe-tray" aria-label="Parts"><h2>Parts <span class="fe-tray-left muted"></span></h2>'
            '<p class="muted">Drag onto the floor, or click to drop in a free spot. Erase puts a building back.</p><div class="fe-tray-list"></div></aside>'
            '<div class="fe-canvas"><svg class="fe-svg" role="application" aria-label="The floor layout: drag a building, or focus one and use the arrow keys"></svg></div></div>'
            '<div class="fe-check" aria-live="polite"></div><ul class="fe-problems" aria-live="polite">' + "".join(f"<li>{esc(p)}</li>" for p in problems) + '</ul></div>'
            f'<form method="post" action="/floor/layout/save" class="fe-save field">{views.csrf_field(csrf)}<input type="hidden" name="rev" value="{esc(rev)}">'
            '<details class="fe-data" open><summary>The layout as data</summary>'
            f'<p class="muted">Positions and belt points are grid cells of {floorplan.G}px. A belt is a list of points, each run straight across '
            'or down; pieces sit on a belt.</p>'
            f'<label>Layout (JSON)<textarea name="plan" rows="14" spellcheck="false">{esc(text)}</textarea></label></details>'
            '<button>Save layout</button></form>'
            f'<form method="post" action="/floor/layout/reset" class="fe-reset">{views.csrf_field(csrf)}<input type="hidden" name="rev" value="{esc(rev)}">'
            '<button class="secondary">Reset to the default layout</button></form>'
            '<script src="/static/town.js" defer></script><script src="/static/terrain.js" defer></script><script src="/static/floor-edit.js" defer></script>')
    h._send(status, views.page("Floor layout", body, "/", csrf, wide=True, full=True, flash=flash, flash_kind=kind))


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
