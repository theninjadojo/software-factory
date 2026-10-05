"""The floor editor's terrain in a real browser: every Scenery, Water, Ground, Structures and Light tool places what it should (clicks,
drags that paint, lines that lock straight, rectangles), Erase takes it away again and undo brings it back; the editor's terrain.js
draws exactly what the server's terrain.py draws; the terrain's rules are shown as they are broken and a save that breaks one is
refused; a saved terrain is drawn on the Factory floor."""
import json
import random
import time

import pytest

from conftest import DESKTOP, PASSWORD
from factory import jobs
from factory.ui import floorplan as F, terrain as T


@pytest.fixture
def wide(browser, server):
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.on("console", lambda m: pg.errors.append(m.text) if m.type == "error" else None)
    pg.goto(server.url + "/login")
    pg.fill("input[name=password]", PASSWORD)
    pg.click("button")
    pg.wait_for_url(server.url + "/")
    yield pg
    ctx.close()


def editor(pg, server):
    """The editor on a bare floor: the default layout comes with scenery of its own, so these tests save it without first."""
    pg.goto(server.url + "/floor/edit")
    pg.wait_for_selector(".fe-svg [data-node]")
    doc = json.loads(pg.input_value("textarea[name=plan]"))
    if doc.get("terrain"):
        doc.pop("terrain")
        (server.root / "state" / F.FILE).write_text(json.dumps(doc))
        pg.goto(server.url + "/floor/edit")
        pg.wait_for_selector(".fe-svg [data-node]")


def plan(pg) -> dict:
    return json.loads(pg.input_value("textarea[name=plan]"))


def problems(pg) -> str:
    return " | ".join(pg.locator(".fe-problems li").all_inner_texts())


def zoom(pg) -> float:
    return pg.locator(".fe-svg").bounding_box()["width"] / (F.W * F.G)


def show(pg, px, py):
    """Scroll the page and the canvas so floor pixel (px, py) is in the middle of the view; its point on the screen."""
    pg.locator(".fe-canvas").evaluate("c => c.scrollIntoView({block: 'center'})")
    pg.locator(".fe-canvas").evaluate("(c, p) => { const z = p[2]; c.scrollLeft = p[0] * z - c.clientWidth / 2; c.scrollTop = p[1] * z - c.clientHeight / 2; }",
                                      [px, py, zoom(pg)])
    x, y = at(pg, px, py)                                               # the canvas stops at its edge: bring the point into the window too
    pg.evaluate("d => window.scrollBy(d[0], d[1])", [0, y - pg.viewport_size["height"] / 2])
    return at(pg, px, py)


def at(pg, px, py):
    b, z = pg.locator(".fe-svg").bounding_box(), zoom(pg)
    return b["x"] + px * z, b["y"] + py * z


def tool(pg, name):
    b = pg.locator(f'[data-tool="{name}"]')
    if not b.is_visible():                                       # the terrain categories start collapsed in the build panel
        pg.locator(f'.fe-cat:has([data-tool="{name}"]) .fe-cat-toggle').click()
    b.click()


def into_view(pg, px, py):
    """The point on the screen, the window scrolled first if it is off it (clicking a tool scrolls the page to the toolbar)."""
    x, y = at(pg, px, py)
    h = pg.viewport_size["height"]
    if not 40 < y < h - 40:
        pg.evaluate("d => window.scrollBy(0, d)", y - h / 2)
        x, y = at(pg, px, py)
    return x, y


def click_px(pg, px, py):
    pg.mouse.click(*into_view(pg, px, py))


def drag_px(pg, pts, steps=6):
    """Press at the first floor point, move through the others in steps, let go at the last."""
    ys = [p[1] for p in pts]
    into_view(pg, pts[0][0], (min(ys) + max(ys)) / 2)
    x, y = at(pg, *pts[0])
    pg.mouse.move(x, y)
    pg.mouse.down()
    for p in pts[1:]:
        pg.mouse.move(*at(pg, *p), steps=steps)
    pg.mouse.up()


def terrain(pg) -> dict:
    return plan(pg).get("terrain") or {}


def kinds(pg) -> list:
    return [it[0] for it in terrain(pg).get("items") or []]


def scene(seed: int) -> dict:
    rnd = random.Random(seed)
    items = [[k, rnd.randint(0, 3900), rnd.randint(0, 2300), T.size_for(k, rnd.random()), rnd.randint(0, 2 ** 32 - 1)]
             for k in (rnd.choice(T.KINDS) for _ in range(80))]
    rivers = [[[rnd.randint(0, 3999), rnd.randint(0, 2399)] for _ in range(rnd.randint(2, 40))] for _ in range(3)]
    cells = {k: set() for k in T.GROUNDS}
    for _ in range(200):
        c = (rnd.randint(0, 99), rnd.randint(0, 59))
        for k in cells:
            cells[k].discard(c)
        cells[rnd.choice(T.GROUNDS)].add(c)
    return {"items": items, "rivers": rivers, "tiles": T.runs(cells), "fences": [[[10, 100], [30, 100], [30, 110]]], "gates": [[31, 100, "h"]],
            "roads": [[[40, 100], [80, 100]], [[60, 80], [60, 120]]], "hazards": [[90, 100, 5, 4]]}


TRACKS = [[[1000, 1800], [1000, 2200]], [[700, 2400], [1300, 2400], [1300, 2600]]]       # pixels: one crosses each road


def test_the_editor_draws_the_terrain_exactly_as_the_floor_does(wide, server):
    pg = wide
    editor(pg, server)
    for seed in (1, 2, 3):
        t = scene(seed)
        walls, trees = [[[5, 5], [5, 20]]], [[7, 7], [70, 30]]
        js = pg.evaluate("a => window.FT.svg(a[0], 20, 4000, 2400, a[1], a[2], 'all', a[3])", [t, walls, trees, TRACKS])
        assert js == T.svg(t, 20, 4000, 2400, walls, trees, "all", TRACKS), seed
        for layer in ("under", "bridge", "over", "top"):
            assert pg.evaluate("a => window.FT.svg(a[0], 20, 4000, 2400, a[1], a[2], a[3], a[4])", [t, walls, trees, layer, TRACKS]) == T.svg(t, 20, 4000, 2400, walls, trees, layer, TRACKS)
        assert "tf-bridge" in js and "tf-car" in js
        for kind in T.PARK:                                               # each hitbox the same on both sides
            it = [kind, 500, 600, T.SIZES[kind][0], 7]
            assert pg.evaluate("it => window.FT.footprint(it)", it) == list(T.footprint(it))
    assert pg.errors == []


def test_every_terrain_tool_places_paints_and_erases(wide, server):
    pg = wide
    editor(pg, server)
    Y = 2100                                                            # a strip of empty floor under the default layout
    show(pg, 700, Y)

    # Scenery: a click plants one, a drag plants a row; each rolls its own size in its range
    for k, x in (("tree", 200), ("pine", 300), ("bush", 400), ("rock", 500)):
        tool(pg, k)
        click_px(pg, x, Y)
    assert kinds(pg) == ["tree", "pine", "bush", "rock"]
    for it in terrain(pg)["items"]:
        lo, hi = T.SIZES[it[0]]
        assert lo <= it[3] <= hi and abs(it[1] - {"tree": 200, "pine": 300, "bush": 400, "rock": 500}[it[0]]) <= 2
    tool(pg, "tree")
    drag_px(pg, [(600, Y), (900, Y)])
    assert kinds(pg).count("tree") >= 4                                 # the click's tree and a row of at least three
    assert "objects" in pg.inner_text(".fe-msg") and "tiles" in pg.inner_text(".fe-msg")
    assert pg.locator(".fe-svg .bd-bird").count() == 3                  # birds land by the trees and bushes, three at most

    # Water: a pond (and its ducks), a river that follows the drag
    tool(pg, "pond")
    click_px(pg, 1100, Y)
    assert kinds(pg).count("pond") == 1 and pg.locator(".fe-svg .tr-pond").count() == 1
    assert pg.locator(".fe-svg .dk-duck").count() >= 1
    show(pg, 1500, Y)
    tool(pg, "river")
    drag_px(pg, [(1300, Y - 40), (1400, Y + 10), (1500, Y - 20), (1600, Y + 30)])
    rv = terrain(pg)["rivers"]
    assert len(rv) == 1 and len(rv[0]) >= 4 and pg.locator(".fe-svg .tr-river .wt-shimmer").count() == 1

    # Ground: a drag paints tiles; painting over a tile changes its kind; water tiles bring ducks
    show(pg, 2100, Y)
    tool(pg, "g-grass")
    drag_px(pg, [(1820, Y), (2180, Y)])
    grass = T.cells_of(terrain(pg)["tiles"]).get("grass", set())
    assert len(grass) >= 8 and all(r == Y // T.TILE for c, r in grass)
    tool(pg, "g-concrete")
    click_px(pg, 1900, Y)
    cells = T.cells_of(terrain(pg)["tiles"])
    assert (1900 // T.TILE, Y // T.TILE) in cells["concrete"] and (1900 // T.TILE, Y // T.TILE) not in cells["grass"]
    for k in ("dirt", "sand"):
        tool(pg, f"g-{k}")
        click_px(pg, {"dirt": 2020, "sand": 2060}[k], Y + 80)
    ducks = pg.locator(".fe-svg .dk-duck").count()
    tool(pg, "g-water")
    drag_px(pg, [(2220, Y + 80), (2380, Y + 80)])
    assert len(T.cells_of(terrain(pg)["tiles"])["water"]) >= 4
    assert pg.locator(".fe-svg .dk-duck").count() > ducks

    # Build: walls, fences and roads lock to a straight run; a gate goes in a fence; a hazard zone is a dragged rectangle
    show(pg, 2900, Y)
    tool(pg, "wall")
    drag_px(pg, [(2600, Y), (2800, Y + 30)])                             # mostly across: a straight run across
    w = plan(pg)["walls"][-1]
    assert w[0][1] == w[1][1] and w[1][0] - w[0][0] == 10
    tool(pg, "fence")
    drag_px(pg, [(2600, Y + 100), (2610, Y + 300)])                      # mostly down
    f = terrain(pg)["fences"][-1]
    assert f[0][0] == f[1][0] and abs(f[1][1] - f[0][1]) == 10
    tool(pg, "gate")
    click_px(pg, 2600, Y + 200)
    assert terrain(pg)["gates"][-1] == [130, (Y + 200) // F.G, "v"]       # on a fence running down: it opens across it
    tool(pg, "road")
    drag_px(pg, [(2700, Y + 100), (3000, Y + 100)])
    assert terrain(pg)["roads"][-1] == [[135, (Y + 100) // F.G], [150, (Y + 100) // F.G]]
    tool(pg, "hazard")
    drag_px(pg, [(3100, Y), (3200, Y + 60)])
    assert terrain(pg)["hazards"][-1] == [155, Y // F.G, 5, 3]

    # Light
    show(pg, 3400, Y)
    for k, x in (("lamp", 3300), ("fog", 3450), ("shade", 3600)):
        tool(pg, k)
        click_px(pg, x, Y)
    assert kinds(pg)[-3:] == ["lamp", "fog", "shade"]
    assert pg.locator(".fe-svg .lt-pool").count() == 1 and pg.locator(".fe-svg .lt-fog").count() == 1 and pg.locator(".fe-svg .lt-shade").count() == 1
    assert "Every station is reachable" in problems(pg), problems(pg)

    # Erase: what is under the pointer goes, and undo brings it back; a drag erases ground tiles as it goes
    tool(pg, "erase")
    n = len(kinds(pg))
    click_px(pg, 3450, Y)
    assert len(kinds(pg)) == n - 1 and "fog" not in kinds(pg)
    pg.keyboard.press("Control+z")
    assert "fog" in kinds(pg)
    show(pg, 2000, Y)
    before = len(T.cells_of(terrain(pg)["tiles"]).get("grass", set()))
    drag_px(pg, [(1940, Y), (2180, Y)])
    assert len(T.cells_of(terrain(pg)["tiles"]).get("grass", set())) < before
    show(pg, 2700, Y)
    click_px(pg, 2700, Y)                                               # the wall
    assert not plan(pg)["walls"]

    # saved, the floor draws every kind exactly as the server draws the saved terrain
    want = terrain(pg)
    pg.click(".fe-save button")
    pg.wait_for_url(server.url + "/?ok=layout_saved")
    saved = json.loads((server.root / "state" / F.FILE).read_text())
    assert saved["terrain"] == want
    html = pg.evaluate("() => fetch('/').then(r => r.text())")             # the page as the server sends it
    for cls in ("tr-tree", "tr-pine", "tr-bush", "tr-rock", "tr-pond", "tr-river", "gd-grass", "gd-concrete", "gd-dirt", "gd-sand", "fc-rail", "fc-gate",
                "rd-road", "hz-zone", "lt-pool", "lt-fog", "lt-shade", "dk-duck", "bd-bird"):
        assert pg.locator(f"svg.fm .{cls}").count() >= 1, cls
    assert T.svg(saved["terrain"], F.G, 0, 0, saved.get("walls") or [], saved.get("trees") or [], "under") in html
    assert T.svg(saved["terrain"], F.G, 0, 0, saved.get("walls") or [], saved.get("trees") or [], "over") in html
    assert pg.errors == []


def test_the_terrains_rules_are_shown_and_a_save_that_breaks_one_is_refused(wide, server):
    for name in ("linux-box", "my-mac"):
        jobs.touch_worker(server.db, name, "linux", ["web"], 1, time.time())
    server.db.commit()
    pg = wide
    editor(pg, server)
    d = plan(pg)

    # a pond on a belt: belts cannot cross water. A pond has a hitbox, so it lands only clear of the buildings: try along the
    # longest belts until one does
    tool(pg, "pond")
    hop = None
    for hid in sorted(d["belts"], key=lambda k: -len(F.cells(d["belts"][k]))):
        cs = F.cells(d["belts"][hid])
        for c in cs[len(cs) // 3: 2 * len(cs) // 3]:
            show(pg, c[0] * F.G, c[1] * F.G)
            click_px(pg, c[0] * F.G, c[1] * F.G)
            if "pond" in kinds(pg):
                hop = hid
                break
            assert "No room there: A pond would stand on" in problems(pg)
        if hop:
            break
    a_, _, b_ = hop.partition(">")
    assert f"The belt from {F.label(a_)} to {F.label(b_)} crosses water" in problems(pg)
    pg.click(".fe-save button")
    assert "The layout was not saved" in pg.inner_text(".flash")
    assert "crosses water" in problems(pg)
    pond = next(it for it in terrain(pg)["items"] if it[0] == "pond")   # the page came back with the refused layout, pond and all
    tool(pg, "erase")
    show(pg, pond[1], pond[2])
    click_px(pg, pond[1] + pond[3] * 0.25, pond[2] + pond[3] * 0.25)   # a part of the pond the belt does not run over
    assert "pond" not in kinds(pg) and hop in plan(pg)["belts"]
    assert "crosses water" not in problems(pg), problems(pg)

    # a wall across a belt: only under it, with an underground belt
    p = plan(pg)["belts"]["station:poll>station:classify"]
    (x1, y1), (x2, y2) = p[0], p[-1]
    show(pg, (x1 + 1) * F.G, y1 * F.G)
    tool(pg, "wall")
    drag_px(pg, [((x1 + 1) * F.G, (y1 - 1) * F.G), ((x1 + 1) * F.G, (y1 + 1) * F.G)])
    assert "The belt from Poll to Classify crosses a wall: take it under with an underground belt" in problems(pg)
    tool(pg, "underground")
    click_px(pg, x1 * F.G, y1 * F.G)
    click_px(pg, (x1 + 2) * F.G, y1 * F.G)
    assert "crosses a wall" not in problems(pg), problems(pg)

    # a fence across the track home: only through a gate
    tp = plan(pg)["tracks"]["depot>yard"]
    a, b = tp[0], tp[1]
    c = ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2)
    across = a[1] == b[1]
    show(pg, c[0] * F.G, c[1] * F.G)
    tool(pg, "fence")
    drag_px(pg, [(c[0] * F.G, (c[1] - 2) * F.G), (c[0] * F.G, (c[1] + 2) * F.G)] if across else [((c[0] - 2) * F.G, c[1] * F.G), ((c[0] + 2) * F.G, c[1] * F.G)])
    assert "The track from The Train station to The Verify yard runs into a fence: put a gate where it crosses" in problems(pg)
    tool(pg, "gate")
    click_px(pg, c[0] * F.G, c[1] * F.G)
    assert "runs into a fence" not in problems(pg), problems(pg)

    # a hazard zone over a building
    n = plan(pg)["nodes"]["station:build"]
    show(pg, (n["x"] + 2) * F.G, (n["y"] + 2) * F.G)
    tool(pg, "hazard")
    drag_px(pg, [((n["x"] + 1) * F.G, (n["y"] + 1) * F.G), ((n["x"] + 3) * F.G, (n["y"] + 3) * F.G)])
    assert "Build stands in a hazard zone" in problems(pg)
    pg.keyboard.press("Control+z")
    assert "hazard zone" not in problems(pg)
    assert "Every station is reachable" in problems(pg), problems(pg)
    pg.click(".fe-save button")
    pg.wait_for_url(server.url + "/?ok=layout_saved")
    saved = json.loads((server.root / "state" / F.FILE).read_text())
    assert saved["terrain"]["fences"] and saved["terrain"]["gates"] and saved["walls"]
    assert [e for e in pg.errors if "status of 422" not in e] == []


def test_the_park_goes_where_there_is_room_and_hitboxes_show_on_request(wide, server):
    pg = wide
    editor(pg, server)
    d = plan(pg)
    n = d["nodes"]["station:build"]
    # on a building: refused, and the status line says why
    show(pg, (n["x"] + 2) * F.G, (n["y"] + 2) * F.G)
    tool(pg, "picnic")
    click_px(pg, (n["x"] + 2) * F.G, (n["y"] + 2) * F.G)
    assert "picnic" not in kinds(pg)
    assert "No room there: The picnic would stand on Build" in pg.inner_text(".fe-msg")
    # out on the open floor, below everything: placed, and drawn side-on with its people
    free = (300, F.H * F.G - 200)
    show(pg, *free)
    tool(pg, "bench")
    click_px(pg, *free)
    assert kinds(pg).count("bench") == 1
    assert pg.locator(".fe-svg .pk-bench").count() == 1
    click_px(pg, free[0] + 10, free[1])                                 # a second bench on top of the first: no room
    assert kinds(pg).count("bench") == 1
    # every hitbox, on request
    assert pg.locator(".fe-svg .fe-hitbox").count() == 0
    pg.click('[data-act="hitboxes"]')
    assert pg.get_attribute('[data-act="hitboxes"]', "aria-pressed") == "true"
    assert pg.locator(".fe-svg .fe-hitbox.park").count() == 1 and pg.locator(".fe-svg .fe-hitbox").count() > 5
    # Erase takes the bench away again
    tool(pg, "erase")
    click_px(pg, *free)
    assert "bench" not in kinds(pg)
    assert pg.errors == []


def test_the_town_is_placed_like_the_park_and_the_saved_floor_draws_it(wide, server):
    pg = wide
    editor(pg, server)
    y = F.H * F.G - 200
    xs = {"t-house": 300, "t-coffee": 520, "t-firetruck": 740, "t-lighthouse": 960}
    show(pg, 700, y)
    for k, x in xs.items():
        tool(pg, k)
        click_px(pg, x, y)
    assert [k for k in kinds(pg) if k.startswith("t-")] == list(xs)
    assert pg.locator(".fe-svg .tw-art").count() == len(xs)
    assert pg.locator(".fe-svg .tw-art [style]").count() == 0
    tool(pg, "t-bank")
    click_px(pg, xs["t-house"] + 20, y)                                 # on the house: no room
    assert "t-bank" not in kinds(pg) and "would stand on the house" in pg.inner_text(".fe-msg")
    tool(pg, "erase")
    click_px(pg, xs["t-lighthouse"], y)
    assert "t-lighthouse" not in kinds(pg)
    pg.click("text=Save layout")
    pg.wait_for_load_state("networkidle")
    pg.goto(server.url + "/")
    assert pg.locator("svg .tw-art").count() == 3
    assert pg.errors == []


def test_the_floor_has_a_time_of_day_that_is_kept(wide, server):
    pg = wide
    pg.goto(server.url + "/")
    pg.wait_for_load_state("networkidle")
    sky = lambda: pg.evaluate("() => [getComputedStyle(document.querySelector('.fm-tod')).fill, getComputedStyle(document.querySelector('.fm-nglow')).opacity]")
    pg.click('.fm-time [data-tod="day"]')
    pg.wait_for_timeout(800)
    assert sky() == ["rgba(6, 10, 34, 0)", "0"]
    pg.click('.fm-time [data-tod="night"]')
    pg.wait_for_timeout(800)
    assert sky() == ["rgba(6, 10, 34, 0.46)", "1"]
    assert pg.get_attribute('.fm-time [data-tod="night"]', "aria-pressed") == "true"
    pg.reload()
    pg.wait_for_load_state("networkidle")
    assert pg.get_attribute("html", "data-tod") == "night" and pg.get_attribute('.fm-time [data-tod="night"]', "aria-pressed") == "true"
    pg.wait_for_timeout(5600)                                           # the live refresh redraws the floor; the choice stays
    assert pg.get_attribute('.fm-time [data-tod="night"]', "aria-pressed") == "true" and sky()[1] == "1"
    assert pg.errors == []
