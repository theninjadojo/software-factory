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


def drag(pg, selector, dx, dy, at=(0.5, 0.5)):
    """Press on the element (at a fraction of its box), move by dx, dy pixels in steps, release."""
    el = pg.locator(selector).first
    el.scroll_into_view_if_needed()
    b = el.bounding_box()
    x, y = b["x"] + b["width"] * at[0], b["y"] + b["height"] * at[1]
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
    drag(pg, '[data-node="station:pr"]', 2 * F.G, 3 * F.G)
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
    drag(pg, '[data-district="PRODUCTION"] > rect', 0, 2 * F.G, at=(0.1, 0.05))
    moved = plan(pg)
    assert moved["districts"]["PRODUCTION"]["y"] == before["districts"]["PRODUCTION"]["y"] + 2
    assert moved["nodes"]["station:build"]["y"] == before["nodes"]["station:build"]["y"] + 2
    assert "Every station is reachable" in problems(pg), problems(pg)

    # and resized by its corner
    w0 = moved["districts"]["QUALITY"]["w"]
    drag(pg, '[data-resize="QUALITY"]', 3 * F.G, 0)
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
        pg.locator('.fe-svg').click(position={"x": x * F.G, "y": y * F.G})
    assert "must start beside Build" in problems(pg), problems(pg)
    assert pg.locator(".fe-belt.bad").count() == 1
    pg.click(".fe-save button")
    assert "The layout was not saved" in pg.inner_text(".flash")
    assert not (server.root / "state" / F.FILE).exists()
    assert "must start beside Build" in problems(pg)                      # the page comes back with the layout as it was sent
    pg.click('[data-act="default"]')
    assert "Every station is reachable" in problems(pg), problems(pg)
    assert [e for e in pg.errors if "status of 422" not in e] == []          # the refused save itself is a 422
