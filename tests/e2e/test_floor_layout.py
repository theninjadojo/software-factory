"""The floor's layout editor (/floor/edit) in a real browser: drag buildings and districts, resize a district, undo and redo, a broken
belt refused, save, the floor drawn from the saved layout with its crates on the new belts, and reset. Wide screens only: on a phone
the floor is its column of stations and there is no editor."""
import json
import re

import pytest

from conftest import DESKTOP, PASSWORD
from factory.ui import board, floorplan as F


@pytest.fixture
def wide(browser, server):
    ctx = browser.new_context(viewport=DESKTOP)
    pg = ctx.new_page()
    pg.errors = []
    pg.on("pageerror", lambda e: pg.errors.append(str(e)))
    pg.on("console", lambda m: pg.errors.append(m.text) if m.type == "error" else None)    # a CSP violation is a console error
    pg.goto(server.url + "/login")
    pg.fill("input[name=password]", PASSWORD)
    pg.click("button")
    pg.wait_for_url(server.url + "/")
    yield pg
    ctx.close()


def plan(pg) -> dict:
    return json.loads(pg.input_value("textarea[name=plan]"))


def problems(pg) -> str:
    return " | ".join(pg.locator(".fe-problems li").all_inner_texts())


def cell(pg) -> float:
    """One grid cell in screen pixels: the editor opens zoomed to fit the floor, so a drag is measured in cells, not in the layout's pixels."""
    return pg.locator(".fe-svg").bounding_box()["width"] / F.W


def drag(pg, selector, dx, dy, at=(0.5, 0.5)):
    """Press on the element (at a fraction of its box), move by dx, dy pixels in steps, release."""
    el = pg.locator(selector).first
    el.scroll_into_view_if_needed()
    b = el.bounding_box()
    x, y = b["x"] + b["width"] * at[0], b["y"] + b["height"] * at[1]
    o, c = pg.locator(".fe-svg").bounding_box(), cell(pg)              # press on a grid point, so the move rounds to whole cells
    x, y = o["x"] + round((x - o["x"]) / c) * c, o["y"] + round((y - o["y"]) / c) * c
    pg.mouse.move(x, y)
    pg.mouse.down()
    pg.mouse.move(x + dx / 2, y + dy / 2, steps=4)
    pg.mouse.move(x + dx, y + dy, steps=4)
    pg.mouse.up()


def open_editor(pg, server):
    pg.click("a.fm-edit")
    pg.wait_for_url(server.url + "/floor/edit")
    pg.wait_for_selector(".fe-svg [data-node]")


def test_arrange_save_and_reset_the_floor(wide, server):
    pg = wide
    open_editor(pg, server)
    assert "Every station is reachable" in problems(pg)
    start = plan(pg)

    # a station: dragged two cells right and three down, its belts follow and every station stays reachable
    drag(pg, '[data-node="station:pr"]', 2 * cell(pg), 3 * cell(pg))
    after = plan(pg)
    assert (after["nodes"]["station:pr"]["x"] - start["nodes"]["station:pr"]["x"], after["nodes"]["station:pr"]["y"] - start["nodes"]["station:pr"]["y"]) == (2, 3)
    assert "Every station is reachable" in problems(pg), problems(pg)
    sd = after["districts"]["SHIPPING"]                                    # its district grew round it
    pr = after["nodes"]["station:pr"]
    assert sd["x"] <= pr["x"] and pr["x"] + 7 <= sd["x"] + sd["w"] and pr["y"] + 5 <= sd["y"] + sd["h"]

    # undo and redo
    pg.keyboard.press("Control+z")
    assert plan(pg)["nodes"]["station:pr"] == start["nodes"]["station:pr"]
    pg.keyboard.press("Control+Shift+z")
    assert plan(pg)["nodes"]["station:pr"] == after["nodes"]["station:pr"]

    # a district: dragged by its name, its stations go with it
    before = plan(pg)
    drag(pg, '[data-district="PRODUCTION"] > rect', 0, 2 * cell(pg), at=(0.1, 0.05))
    moved = plan(pg)
    assert moved["districts"]["PRODUCTION"]["y"] == before["districts"]["PRODUCTION"]["y"] + 2
    assert moved["nodes"]["station:build"]["y"] == before["nodes"]["station:build"]["y"] + 2
    assert "Every station is reachable" in problems(pg), problems(pg)

    # and resized by its corner
    w0 = moved["districts"]["QUALITY"]["w"]
    drag(pg, '[data-resize="QUALITY"]', 3 * cell(pg), 0)
    assert plan(pg)["districts"]["QUALITY"]["w"] == w0 + 3

    # save: the floor is drawn from the layout
    want = plan(pg)
    pg.click(".fe-save button")
    pg.wait_for_url(server.url + "/?ok=layout_saved")
    saved = json.loads((server.root / "state" / F.FILE).read_text())
    assert saved["nodes"] == want["nodes"] and saved["districts"] == want["districts"]
    assert pg.locator("svg.fm").count() == 1

    # the crate of the build that is running rides the saved belt into Build
    cfg = server.app.overview()["cfg"]
    ctx = board.floor_ctx(cfg, [])
    hops = F.compile_plan(F.merge(saved, ctx), ctx)["hops"]
    ends = {tuple(float(c) for c in h["pts"][-1]) for h in hops.values() if h["dst"] == "station:build"}
    paths = pg.eval_on_selector_all("svg.fm g.fn-crate animateMotion", "els => els.map(e => e.getAttribute('path'))")
    rides = {tuple(float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", p)[-2:]) for p in paths}
    assert ends & rides, (ends, rides)

    # the editor opens on the saved layout
    open_editor(pg, server)
    assert plan(pg)["nodes"] == want["nodes"]

    # reset
    pg.click(".fe-reset button")
    pg.wait_for_url(server.url + "/floor/edit?ok=layout_reset")
    assert not (server.root / "state" / F.FILE).exists()
    assert pg.errors == []


def test_a_broken_route_is_shown_and_refused_on_save(wide, server):
    pg = wide
    open_editor(pg, server)
    pg.click('[data-tool="belt"]')
    hop = pg.eval_on_selector(".fe-hop", "s => [...s.options].map(o => o.value).find(v => v.startsWith('station:build>'))")
    pg.select_option(".fe-hop", hop)
    svg = pg.locator(".fe-svg")
    svg.scroll_into_view_if_needed()
    for x, y in ((2, 2), (2, 6), (2, 6)):                                 # a belt far from both stations; the repeated point finishes it
        pg.locator('.fe-svg').click(position={"x": x * cell(pg), "y": y * cell(pg)})
    assert "must start beside Build" in problems(pg), problems(pg)
    assert pg.locator(".fe-belt.bad").count() == 1
    pg.click(".fe-save button")
    assert "The layout was not saved" in pg.inner_text(".flash")
    assert not (server.root / "state" / F.FILE).exists()
    assert "must start beside Build" in problems(pg)                      # the page comes back with the layout as it was sent
    pg.click('[data-act="default"]')
    assert "Every station is reachable" in problems(pg), problems(pg)
    assert [e for e in pg.errors if "status of 422" not in e] == []          # the refused save itself is a 422


def grid_to_client(pg, gx, gy):
    """The screen point of a grid point on the editor's canvas (at its current zoom and scroll)."""
    b = pg.locator(".fe-svg").bounding_box()
    zoom = float(pg.inner_text(".fe-zoomval").rstrip("%")) / 100
    return b["x"] + gx * F.G * zoom, b["y"] + gy * F.G * zoom


def drag_to(pg, start, end):
    pg.mouse.move(*start)
    pg.mouse.down()
    pg.mouse.move((start[0] + end[0]) / 2, (start[1] + end[1]) / 2, steps=5)
    pg.mouse.move(*end, steps=5)
    pg.mouse.up()


def centre(pg, selector):
    el = pg.locator(selector).first
    el.scroll_into_view_if_needed()
    b = el.bounding_box()
    return b["x"] + b["width"] / 2, b["y"] + b["height"] / 2


def drag_between(pg, a, b):
    """Drag from one element onto another, both brought into view first."""
    centre(pg, a)
    end = centre(pg, b)
    drag_to(pg, centre(pg, a), end)


def test_start_from_scratch_with_the_parts_tray_and_lay_belts_by_dragging(wide, server):
    pg = wide
    open_editor(pg, server)

    # an empty floor: every building waits in the parts tray
    pg.click('[data-act="scratch"]')
    assert plan(pg)["nodes"] == {} and plan(pg)["belts"] == {}
    assert "Missing" in problems(pg)
    meta = json.loads(pg.get_attribute(".fe", "data-meta"))
    assert pg.locator(".fe-part[data-part]:not(.placed)").count() == len(meta["nodes"])                # all of them listed
    assert pg.locator('.fe-part[data-part="harbor"]').count() == 1 and pg.locator('.fe-part[data-part="notify:telegram"]').count() == 1
    assert "More channels later" in pg.inner_text(".fe-tray")

    # Receiving dragged from the tray lands centred where it is let go
    pg.locator(".fe-canvas").evaluate("c => { c.scrollLeft = 0; c.scrollTop = 0; }")
    drag_to(pg, centre(pg, '.fe-part[data-part="receiving"]'), grid_to_client(pg, 20, 14))
    r = plan(pg)["nodes"]["receiving"]
    assert (r["x"], r["y"]) == (20 - 7 // 2, 14 - 5 // 2), r
    assert pg.locator('.fe-part[data-part="receiving"].placed').count() == 1

    # the harbor, clicked in, then dragged off somewhere else entirely: it can stand anywhere
    pg.click('.fe-part[data-part="harbor"]')
    assert "harbor" in plan(pg)["nodes"]
    pg.click('[data-tool="move"]')
    h0 = plan(pg)["nodes"]["harbor"]
    sx, sy = centre(pg, '[data-node="harbor"]')
    hx, hy = grid_to_client(pg, 40, 30)
    drag_to(pg, (sx, sy), (hx, hy))
    h1 = plan(pg)["nodes"]["harbor"]
    assert h1 != h0

    # Draw belt: drag from the harbor onto Receiving and the belt for that hop is laid, beside both, through nothing
    pg.click('[data-tool="belt"]')
    drag_between(pg, '[data-node="harbor"]', '[data-node="receiving"]')
    belt = plan(pg)["belts"].get("harbor>receiving")
    assert belt and len(belt) >= 2, plan(pg)["belts"]
    assert "The belt from The harbor to Receiving" not in problems(pg), problems(pg)
    assert "No belt from The harbor to Receiving" not in problems(pg)

    # the other way there is no hop: nothing is laid and the editor says why
    drag_between(pg, '[data-node="receiving"]', '[data-node="harbor"]')
    assert "No hop from Receiving to The harbor" in problems(pg)
    assert list(plan(pg)["belts"]) == ["harbor>receiving"]

    # Move: a belt dragged sideways slides; its ends stay beside the buildings
    pg.click('[data-tool="move"]')
    pts = plan(pg)["belts"]["harbor>receiving"]
    i = max(range(len(pts) - 1), key=lambda k: abs(pts[k + 1][0] - pts[k][0]) + abs(pts[k + 1][1] - pts[k][1]))
    (ax, ay), (bx, by) = pts[i], pts[i + 1]
    mid = grid_to_client(pg, (ax + bx) / 2, (ay + by) / 2)
    across = ay == by
    zoom = float(pg.inner_text(".fe-zoomval").rstrip("%")) / 100
    step = 3 * F.G * zoom
    drag_to(pg, mid, (mid[0], mid[1] + step) if across else (mid[0] + step, mid[1]))
    slid = plan(pg)["belts"]["harbor>receiving"]
    assert slid != pts and slid[0] == pts[0] and slid[-1] == pts[-1], (pts, slid)
    assert "The belt from The harbor to Receiving" not in problems(pg), problems(pg)
    pg.keyboard.press("Control+z")
    assert plan(pg)["belts"]["harbor>receiving"] == pts

    # Erase: the harbor goes back to the tray and takes its belt with it
    pg.click('[data-tool="erase"]')
    pg.locator('[data-node="harbor"] > rect.fe-hit').click(force=True)
    assert "harbor" not in plan(pg)["nodes"] and "harbor>receiving" not in plan(pg)["belts"]
    assert pg.locator('.fe-part[data-part="harbor"]:not(.placed)').count() == 1

    # the default has everything; saved, the floor draws the harbor and the notifiers where they stand
    pg.click('[data-act="default"]')
    assert "Every station is reachable" in problems(pg), problems(pg)
    assert pg.locator(".fe-part.placed").count() == pg.locator(".fe-part[data-part]").count()
    pg.click(".fe-save button")
    pg.wait_for_url(server.url + "/?ok=layout_saved")
    assert pg.locator("svg.fm .hb-box").count() == 1
    assert pg.locator("svg.fm g.nt").count() == 2
    assert pg.errors == []


def test_workers_by_rail_lay_track_to_a_worker_and_back(wide, server):
    """With workers, the editor lays track: a worker without its loop is named in the checklist and refused; drawn again by dragging
    from the junction onto it, the loop is whole; saved, the floor draws the yard, the Train station, the workers and their track."""
    import time as _t
    from factory import jobs
    for name in ("linux-box", "my-mac"):
        jobs.touch_worker(server.db, name, "linux", ["web"], 1, _t.time())
    server.db.commit()
    pg = wide
    open_editor(pg, server)
    assert pg.locator('[data-tool="rail"]').count() == 1
    assert "Every station is reachable" in problems(pg), problems(pg)
    assert "Train loop: the yard → my-mac → the Train station" in pg.inner_text(".fe-check")
    assert '"junction:a>worker:my-mac"' in pg.input_value("textarea[name=plan]")

    # Erase the branch to my-mac: its loop is broken and the checklist says so
    pg.click('[data-tool="erase"]')
    pts = plan(pg)["tracks"]["junction:a>worker:my-mac"]
    (ax, ay), (bx, by) = max(zip(pts, pts[1:]), key=lambda s: abs(s[1][0] - s[0][0]) + abs(s[1][1] - s[0][1]))
    pg.locator('[data-track="junction:a>worker:my-mac"]').scroll_into_view_if_needed()
    pg.mouse.click(*grid_to_client(pg, (ax + bx) / 2, (ay + by) / 2))
    assert "junction:a>worker:my-mac" not in plan(pg)["tracks"]
    assert "No track loop for my-mac" in problems(pg)
    assert "✗ Train loop: the yard → my-mac" in pg.inner_text(".fe-check")

    # Draw track: drag from junction A onto my-mac and the loop is whole again
    pg.click('[data-tool="rail"]')
    drag_between(pg, '[data-node="junction:a"]', '[data-node="worker:my-mac"]')
    assert "junction:a>worker:my-mac" in plan(pg)["tracks"], plan(pg)["tracks"]
    assert "No track loop" not in problems(pg), problems(pg)
    # the wrong way round is refused, with the reason
    drag_between(pg, '[data-node="worker:my-mac"]', '[data-node="yard"]')
    assert "Track cannot run from my-mac to The Verify yard" in problems(pg)

    # Move: a track dragged sideways slides, its ends stay put
    pg.click('[data-tool="move"]')
    before = plan(pg)["tracks"]["depot>yard"]
    (ax, ay), (bx, by) = max(zip(before, before[1:]), key=lambda s: abs(s[1][0] - s[0][0]) + abs(s[1][1] - s[0][1]))
    pg.locator('[data-track="depot>yard"]').scroll_into_view_if_needed()
    mid = grid_to_client(pg, (ax + bx) / 2, (ay + by) / 2)
    step = 2 * cell(pg)
    drag_to(pg, mid, (mid[0], mid[1] + step) if ay == by else (mid[0] + step, mid[1]))
    after = plan(pg)["tracks"]["depot>yard"]
    assert after != before and after[0] == before[0] and after[-1] == before[-1]

    pg.click(".fe-save button")
    pg.wait_for_url(server.url + "/?ok=layout_saved")
    assert pg.locator("svg.fm .ry-yard").count() == 1 and pg.locator("svg.fm .ry-depot").count() == 1
    assert pg.locator('svg.fm a.fm-w').count() == 2
    assert pg.locator("svg.fm .ry-junction").count() == 2
    assert pg.errors == []
