"""Shoot the floor's screens for the Screens feature, from a local environment of its own.

Starts the UI in-process against a temporary config and a seeded database (the browser tests' fake GitHub, three online workers, one
of them with a job), drives the Factory page and the floor layout editor with Playwright, checks what they show as it goes, and
saves each screen as a static page under screens/pages/ (scripts stripped, styles inlined with fonts linked relatively, every
animation held at one moment, CSRF tokens blanked). Then it renders those pages with the Screens feature's own sealed shooter
(factory.screens.capture, the factory-screens image) into screens/baselines/<page>-<viewport>.png and checks them with
factory.screens.verify, exactly as a build would. screens/README.md has the [screens] config that puts them on the Screens board.

    docker build -t factory-screens -f sandbox/screens/Dockerfile sandbox/screens     # once
    .venv/bin/python scripts/floor_screens.py [--engine docker|podman] [--image factory-screens:latest] [--no-baselines]
"""
import argparse
import json
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests" / "e2e"))

REPO = "theninjadojo/software-factory"
PAGES_DIR, BASE_DIR = ROOT / "screens" / "pages", ROOT / "screens" / "baselines"
DESKTOP, PHONE = {"width": 1440, "height": 900}, {"width": 390, "height": 844}
HOLD = 12.0                                                  # every SMIL animation is shown as it is this many seconds in
WORKERS = ("linux-box", "my-mac", "win-pc")
# (page name, address, viewports, what to do before the snapshot)
SHOTS = (("factory", "/", ("desktop", "mobile"), None), ("floor-editor", "/floor/edit", ("desktop", "mobile"), None),
         ("floor-editor-scratch", "/floor/edit", ("desktop",), "scratch"), ("floor-editor-belt", "/floor/edit", ("desktop",), "select-belt"),
         ("floor-editor-terrain", "/floor/edit", ("desktop",), "terrain"), ("factory-terrain", "/", ("desktop", "mobile"), "terrain-saved"))


def terrain_scene() -> dict:
    """A scene like the design's terrain sandbox, round the default floor: grass under the plant and a concrete pad, a road, a
    pond with its ducks in a patch of dirt, a river, a fence with a gate, trees, pines, bushes and rocks, lamps, fog, shade and a
    hazard zone. Fixed seeds, so the screens are the same every time."""
    from factory.ui import terrain as T
    cells = {k: set() for k in T.GROUNDS}
    for c in range(14, 63):
        for r in range(0, 32):
            cells["grass"].add((c, r))
    for c in range(18, 41):
        for r in range(0, 14):
            cells["grass"].discard((c, r))
            cells["concrete"].add((c, r))
    for c in range(15, 20):
        for r in range(23, 29):
            cells["grass"].discard((c, r))
            cells["dirt"].add((c, r))
    items = [["pond", 700, 1020, 100, 5]]
    items += [["tree", x, y, s, v] for x, y, s, v in ((620, 1280, 60, 11), (760, 1300, 48, 12), (900, 1260, 70, 13), (1500, 1300, 56, 14))]
    items += [["pine", x, y, s, v] for x, y, s, v in ((1100, 1280, 50, 21), (1180, 1330, 40, 22), (1320, 1260, 60, 23))]
    items += [["bush", x, y, s, v] for x, y, s, v in ((840, 1140, 24, 31), (980, 1160, 20, 32), (1240, 1150, 26, 33))]
    items += [["rock", x, y, s, v] for x, y, s, v in ((1050, 1150, 40, 41), (1400, 1180, 30, 42))]
    items += [["lamp", x, y, 130, 51] for x, y in ((1580, 560), (720, 560), (1300, 960), (1700, 960))]
    items += [["fog", 1500, 150, 160, 61], ["fog", 650, 1400, 140, 62], ["shade", 760, 1320, 100, 71]]
    return {"items": items, "rivers": [[[560, 1500], [700, 1540], [840, 1500], [980, 1560], [1120, 1520], [1260, 1580], [1400, 1540]]],
            "tiles": T.runs(cells), "fences": [[[30, 60], [42, 60]], [[44, 60], [56, 60]]], "gates": [[42, 60, "h"]],
            "roads": [[[28, 47], [62, 47]]], "hazards": [[63, 34, 4, 3]]}



def check(cond, what: str) -> None:
    if not cond:
        raise SystemExit(f"FAILED: {what}")
    print(f"  ok  {what}")


def env():
    """A server of our own: temp config and state, seeded tickets, three workers (linux-box has a job), GitHub faked."""
    import conftest as C
    from factory import github as ghm, jobs
    s = C.Server(Path(tempfile.mkdtemp(prefix="floor-screens-")))
    ghm.GitHub._req = lambda self, method, path, data=None: s.gh.req(self, method, path, data)
    now = time.time()
    for n in WORKERS:
        jobs.touch_worker(s.db, n, "linux", ["web"], 1, now + 10 ** 7)                # stays online for the whole run
    jobs.enqueue(s.db, C.REPO, 7, "0" * 40, "", "web", now=now)
    jobs.claim(s.db, "linux-box", "linux", ["web"], now, 10 ** 7, 10 ** 7, 3)            # linux-box has a job: its train runs
    s.db.commit()
    return s, C.PASSWORD


def snapshot(pg) -> str:
    """The page as it is now, as a static file the sealed shooter can serve from the checkout."""
    pg.evaluate("t => document.querySelectorAll('svg').forEach(s => { try { s.pauseAnimations(); s.setCurrentTime(t); } catch (e) {} })", HOLD)
    html = pg.evaluate("() => '<!doctype html>' + document.documentElement.outerHTML")
    css = (ROOT / "factory/ui/static/style.css").read_text().replace("url(/static/fonts/", "url(../../factory/ui/static/fonts/")
    html = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.S | re.I)
    css += (ROOT / "factory/ui/static/town.css").read_text()
    html = re.sub(r'<link rel="stylesheet" href="/static/(?:style|town)\.css[^"]*">', lambda m: "", html)
    html = html.replace("</head>", "<style>" + css + "</style></head>", 1)
    html = re.sub(r' data-tod="\w+"', "", html)                                    # the screens show the floor by day, not wherever Auto has got to
    html = re.sub(r'(name="csrf" value=")[^"]*', r"\1x", html)
    hold = (f"<script>addEventListener('load', () => document.querySelectorAll('svg').forEach(s => {{ try {{ s.pauseAnimations(); "
            f"s.setCurrentTime({HOLD}); }} catch (e) {{}} }}))</script>")
    return html.replace("</head>", hold + "</head>", 1)


def shoot(server, password: str) -> list:
    """Each screen, checked and saved as screens/pages/<page>.html. One snapshot serves every viewport: the phone layout is CSS."""
    from playwright.sync_api import sync_playwright
    PAGES_DIR.mkdir(parents=True, exist_ok=True)
    pages = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport=DESKTOP)
        pg = ctx.new_page()
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.goto(server.url + "/login")
        pg.fill("input[name=password]", password)
        pg.click("button")
        pg.wait_for_url(server.url + "/")
        for name, path, views, act in SHOTS:
            print(name)
            pg.goto(server.url + path)
            pg.wait_for_load_state("networkidle")
            if path == "/floor/edit":
                pg.wait_for_selector(".fe-svg [data-node]")
                verify_editor(pg, act)
            else:
                verify_factory(pg)
            if act == "terrain-saved":
                verify_terrain_floor(pg)
            check(not errors, f"no script or CSP errors on {name} ({errors[:2]})")
            out = PAGES_DIR / f"{name}.html"
            out.write_text(snapshot(pg))
            pages.append((name, out.relative_to(ROOT).as_posix(), views))
            if act == "terrain":                                        # keep it: the next screen is the floor drawn from it
                pg.click(".fe-save button")
                pg.wait_for_url(server.url + "/?ok=layout_saved")
        ctx.close()
        b.close()
    return pages


def verify_editor(pg, act) -> None:
    meta = json.loads(pg.get_attribute(".fe", "data-meta"))
    plan = lambda: json.loads(pg.input_value("textarea[name=plan]"))
    check("Every station is reachable" in pg.inner_text(".fe-problems"), "the default layout is whole")
    check(pg.locator(".fe-svg .sh-water").count() == 1, "the sea's water is drawn, with its coast")
    hb, sea = plan()["nodes"]["harbor"], plan()["nodes"]["sea"]
    check(sea["x"] <= hb["x"] < sea["x"] + 15, "the harbor stands in the sea, at the shore")
    check(pg.locator(".fe-belt.in").count() == 2, "the airfield's and the harbor's belts are drawn as deliveries in")
    check(pg.locator(".fe-train .fm-loco").count() == len(meta["workers"]) == len(WORKERS), "a train runs each worker's loop in the editor")
    check(pg.locator(".fe-trains .fm-sig").count() >= len(WORKERS) + 1, "every track into a junction has a signal")
    check(pg.locator(".fe-ring").count() == len(WORKERS) + 2, "the yard, the Train station and each worker have a turnaround loop")
    check(len(pg.locator(".fe-part[data-part] .fe-part-ico").all()) == len(meta["nodes"]), "every part in the tray has its icon")
    check(pg.locator(".fe-legend li").count() == 4, "the legend names belts, deliveries, rail and notifiers")
    check("20 of 20 laid" in pg.inner_text(".fe-check") or "of" in pg.inner_text(".fe-check"), "the checklist counts what is laid")
    check("Move:" in pg.inner_text(".fe-msg"), "the status line says what the tool does")
    check(pg.locator(".fe-dist.d-intake").count() == 1, "the districts have their colours")
    if act == "scratch":
        pg.click('[data-act="scratch"]')
        check(plan()["nodes"] == {}, "Start from scratch empties the floor")
        check("An empty floor" in pg.text_content(".fe-svg"), "an empty floor says how to start")
    if act == "terrain":
        doc = plan()
        doc["terrain"] = terrain_scene()
        pg.locator("textarea[name=plan]").evaluate("(a, v) => { a.value = v; a.dispatchEvent(new Event('change')); }", json.dumps(doc))   # as typed in "The layout as data"
        check(plan().get("terrain") == doc["terrain"], "the terrain scene is in the editor")
        check("Every station is reachable" in pg.inner_text(".fe-problems"), "the terrain breaks no rule")
        for cls in ("gd-grass", "gd-concrete", "gd-dirt", "tr-pond", "tr-river", "tr-tree", "tr-pine", "tr-bush", "tr-rock", "fc-gate", "rd-road",
                    "hz-zone", "lt-pool", "lt-fog", "lt-shade", "dk-duck", "bd-bird"):
            check(pg.locator(f".fe-svg .{cls}").count() >= 1, f"the editor draws {cls}")
        check(pg.evaluate("t => window.FT.svg(t, 20, 0, 0, [], [], 'under')", doc["terrain"]) == __import__("factory.ui.terrain", fromlist=["svg"]).svg(doc["terrain"], 20, 0, 0, [], [], "under"),
              "the editor's terrain is the server's, to the character")
    if act == "select-belt":
        pts = plan()["belts"]["harbor>receiving"]
        (ax, ay), (bx, by) = max(zip(pts, pts[1:]), key=lambda s: abs(s[1][0] - s[0][0]) + abs(s[1][1] - s[0][1]))
        pg.locator('[data-hop="harbor>receiving"]').scroll_into_view_if_needed()
        o = pg.locator(".fe-svg").bounding_box()
        cell = o["width"] / meta["w"]
        pg.mouse.click(o["x"] + (ax + bx) / 2 * cell, o["y"] + (ay + by) / 2 * cell)
        check("Belt The harbor → Receiving" in pg.inner_text(".fe-msg"), "selecting a belt names it in the status line")
        check(pg.locator(".fe-remove").is_visible() and pg.inner_text(".fe-remove") == "Remove belt", "with a Remove belt button")
        check(pg.locator(".fe-belt.sel").count() == 1 and pg.locator(".fe-handle").count() == 1, "the belt turns orange, with its handle")


def verify_terrain_floor(pg) -> None:
    for cls in ("gd-grass", "tr-pond", "tr-river", "tr-tree", "fc-gate", "rd-road", "hz-zone", "lt-pool", "dk-duck", "bd-bird"):
        check(pg.locator(f"svg.fm .{cls}").count() >= 1, f"the floor draws {cls}")


def verify_factory(pg) -> None:
    check(pg.locator("svg.fm .sh-water").count() == 1, "the floor draws the sea with its coast")
    check(pg.locator("svg.fm g.nt").count() == 2, "Telegram and Slack stand on the floor")


def baselines(pages: list, engine: str, image: str) -> None:
    from factory import screens
    from factory.config import RunnerCfg, ScreenPage, ScreensCfg, Viewport
    sp = tuple(ScreenPage(REPO, name, path, tuple(views), wait_for="body", journey="floor", step=k + 1) for k, (name, path, views) in enumerate(pages))
    sc = ScreensCfg(image=image, viewports=(Viewport("desktop", DESKTOP["width"], DESKTOP["height"]), Viewport("mobile", PHONE["width"], PHONE["height"])),
                    pages=sp)
    with tempfile.TemporaryDirectory(prefix="floor-screens-work-") as work:
        rn = RunnerCfg(engine=engine, work_dir=work)
        print("rendering with the sealed shooter:", ", ".join(f"{p.name} ({'/'.join(p.viewports)})" for p in sp))
        shots, problem = screens.capture(rn, sc, REPO, ROOT)
        check(not problem, f"the shooter rendered every screen ({problem})")
        BASE_DIR.mkdir(parents=True, exist_ok=True)
        for sid, png in shots.items():
            (BASE_DIR / f"{sid}.png").write_bytes(png)
            print(f"  wrote screens/baselines/{sid}.png")
        rep = screens.verify(rn, sc, REPO, ROOT)
        print("\n".join("  " + line for line in rep.lines))
        check(rep.ok, "screens.verify passes against the new baselines, as a build would check them")
    (ROOT / "screens" / "pages.json").write_text(json.dumps([{"name": p.name, "path": p.path, "viewports": list(p.viewports)} for p in sp], indent=1) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", default="docker")
    ap.add_argument("--image", default="factory-screens:latest")
    ap.add_argument("--no-baselines", action="store_true")
    a = ap.parse_args()
    server, password = env()
    try:
        pages = shoot(server, password)
    finally:
        server.srv.shutdown()
    if not a.no_baselines:
        baselines(pages, a.engine, a.image)
    print("done")


if __name__ == "__main__":
    main()
