import html
import json
import os
import re
import stat
import tempfile
import unittest
from pathlib import Path

from factory.ui import board, floorplan as F, plant, server, terrain
from test_ui_admin import AdminCase
from test_yard import ROLES

SMALL = {"roles": [], "ci": "off", "review": False}


def ctx(cfg=ROLES, trains=0):
    return F.Ctx([s for s, _, _ in board.floor_order(cfg)], [h["name"] for h in cfg.get("power") or []], bool(cfg.get("workers")), trains)


def moved(doc, nid, dx, dy):
    """The layout with one building moved and its belts routed again round it."""
    d = json.loads(json.dumps(doc))
    d["nodes"][nid]["x"] += dx
    d["nodes"][nid]["y"] += dy
    B = F.boxes(d["nodes"], CTX)
    keep = {k: v for k, v in d["belts"].items() if nid not in k.split(">") and not F._belt_problem(k, v, B, d["nodes"])}
    d["belts"] = F._route_all(d["nodes"], keep, CTX)
    d["pieces"] = []
    if nid.startswith("station:"):                                   # its district is drawn round it again, as the editor would grow it
        d["districts"].pop(F.district_of(nid.split(":")[1]), None)
    return d


CTX = ctx(ROLES, 2)


class Validate(unittest.TestCase):
    def test_the_default_is_valid_for_every_config(self):
        for cfg in (SMALL, ROLES, {**ROLES, "review": False, "ci": "off", "workers": False}):
            for trains in (0, 1, 3, 5):
                c = ctx(cfg, trains)
                d = F.default_plan(c)
                self.assertEqual(F.validate(d, c), [], (cfg, trains))
                self.assertLess(len(json.dumps(d)), F.MAX_BYTES)

    def test_a_moved_station_with_its_belts_rerouted_is_valid(self):
        d = moved(F.default_plan(CTX), "station:build", 0, 3)
        self.assertEqual(F.validate(d, CTX), [])

    def test_bad_documents_are_refused(self):
        good = F.default_plan(CTX)

        def bad(change, needle):
            d = json.loads(json.dumps(good))
            change(d)
            errs = F.validate(d, CTX)
            self.assertTrue(errs, needle)
            self.assertTrue(any(needle in e for e in errs), (needle, errs))

        bad(lambda d: d.update(extra=1), "Unknown keys")
        bad(lambda d: d.update(version=2), "version")
        bad(lambda d: d["nodes"]["queue"].update(x=1.5), "whole number")
        bad(lambda d: d["nodes"]["queue"].update(x=-1), "whole number")
        bad(lambda d: d["nodes"]["queue"].update(x=F.W + 1), "whole number")
        bad(lambda d: d["nodes"].update({'"><script>': {"x": 1, "y": 1}}), "Unknown buildings")
        bad(lambda d: d["nodes"].pop("station:build"), "Missing buildings")
        bad(lambda d: d["nodes"]["station:review"].update(d["nodes"]["station:ci"]), "overlaps")
        bad(lambda d: d["nodes"]["airfield"].update(x=F.W - 2), "outside the floor")
        hop = F.hop_id("station:build", "station:review")
        bad(lambda d: d["belts"].pop(hop), "cannot be reached")
        bad(lambda d: d["belts"].update({hop: [[1, 1], [5, 5]]}), "straight")
        bad(lambda d: d["belts"].update({hop: [[0, 0], [0, 1]]}), "must start beside")
        bad(lambda d: d["belts"].update({"x>y": [[0, 0], [0, 1]]}), "no hop")
        bad(lambda d: d.update(pieces=[{"kind": "bomb", "at": [1, 1], "dir": "e"}]), "piece is")
        bad(lambda d: d.update(pieces=[{"kind": "splitter", "at": [0, 0], "dir": "e"}]), "not on a belt")
        bad(lambda d: d["belts"].update({hop: [[0, i % 2] for i in range(F.MAX_POINTS + 1)]}), "grid points")
        self.assertEqual(F.validate([], CTX), ["The layout must be a JSON object."])

    def test_a_belt_through_a_building_is_refused(self):
        d = json.loads(json.dumps(F.default_plan(CTX)))
        b = F.boxes(d["nodes"], CTX)
        x, y, w, h = b["station:poll"]
        hop = F.hop_id("station:poll", "station:classify")
        cx = b["station:classify"][0]
        d["belts"][hop] = [[x + 3, y - 1], [x + 3, y + h + 1], [cx + 3, y + h + 1]]   # from above Poll, down through it, to Classify
        self.assertIn("The belt from Poll to Classify runs through Poll.", F.validate(d, CTX))


class Districts(unittest.TestCase):
    def test_a_saved_district_must_hold_its_stations_and_exist(self):
        d = json.loads(json.dumps(F.default_plan(CTX)))
        self.assertEqual(set(d["districts"]), set(CTX.districts))
        d["districts"]["PRODUCTION"]["x"] += 20                          # the box moved away from Build
        self.assertIn("The Production district must hold Build.", F.validate(d, CTX))
        d = json.loads(json.dumps(F.default_plan(CTX)))
        d["districts"]["NOWHERE"] = {"x": 1, "y": 1, "w": 2, "h": 2}
        self.assertTrue(any("no district NOWHERE" in e for e in F.validate(d, CTX)))
        for box in ({"x": 1, "y": 1, "w": 0, "h": 2}, {"x": 1.5, "y": 1, "w": 2, "h": 2}, {"x": 1, "y": 1, "w": 2}, {"x": F.W, "y": 1, "w": 2, "h": 2}):
            d["districts"] = {"INTAKE": box}
            self.assertTrue(any("District INTAKE" in e for e in F.validate(d, CTX)), box)

    def test_a_district_moved_with_its_stations_and_resized_is_valid_and_drawn_there(self):
        d = json.loads(json.dumps(F.default_plan(CTX)))
        d["districts"]["QUALITY"]["w"] += 4
        d["districts"]["QUALITY"]["h"] += 2
        self.assertEqual(F.validate(d, CTX), [])
        x, y, w, h = (v * F.G for v in (d["districts"]["QUALITY"][k] for k in ("x", "y", "w", "h")))
        svg = Drawn().draw(d)
        self.assertIn(f'<rect class="f3-dist d-quality" x="{x}" y="{y}" width="{w}" height="{h}"', svg)

    def test_a_layout_without_districts_draws_them_round_the_stations(self):
        d = json.loads(json.dumps(F.default_plan(CTX)))
        del d["districts"]
        self.assertEqual(F.validate(d, CTX), [])
        self.assertEqual(F.compile_plan(d, CTX)["districts"], F.compile_plan(F.default_plan(CTX), CTX)["districts"])

    def test_merge_grows_a_district_round_a_new_station(self):
        small = ctx({**ROLES, "roles": ["analyst"]}, 2)
        big = ctx({**ROLES, "roles": ["analyst", "architect"]}, 2)
        m = F.merge(F.default_plan(small), big)
        self.assertEqual(F.validate(m, big), [])
        self.assertTrue(F.holds(m["districts"]["PLANNING"], F.boxes(m["nodes"], big)["station:architect"]))


class Resized(unittest.TestCase):
    def test_a_resized_building_and_scenery_are_valid_and_kept(self):
        d = F.default_plan(CTX)
        d["nodes"]["sea"].update(w=10, h=40)
        d["nodes"]["power:claude-code"].update(w=12, h=6)
        d["walls"] = [[[150, 10], [170, 10], [170, 30]]]
        d["trees"] = [[150, 40], [152, 41]]
        self.assertEqual(F.validate(d, CTX), [])
        c = F.canonical(json.loads(json.dumps(d)))
        self.assertEqual((c["nodes"]["sea"], c["walls"], c["trees"]), ({"x": 12, "y": 0, "w": 10, "h": 40}, d["walls"], d["trees"]))
        self.assertNotIn("w", c["nodes"]["mainland"])
        m = F.merge(d, CTX)
        self.assertEqual((m["nodes"]["sea"]["w"], m["walls"], m["trees"]), (10, d["walls"], d["trees"]))
        C = F.compile_plan(d, CTX)
        self.assertEqual(C["box"]["sea"][2:], (200, 800))
        k = 40 / 66                                                  # the smaller stretch: the picture keeps its proportions
        self.assertEqual(C["scale"]["sea"], (k, (10 - 15 * k) * F.G / 2, 0))
        self.assertNotIn("mainland", C["scale"])

    def test_sizes_and_scenery_are_checked(self):
        good = F.default_plan(CTX)

        def bad(change, needle):
            d = json.loads(json.dumps(good))
            change(d)
            errs = F.validate(d, CTX)
            self.assertTrue(any(needle in e for e in errs), (needle, errs))

        bad(lambda d: d["nodes"]["sea"].update(w=2, h=40), "smaller than it may be")
        bad(lambda d: d["nodes"]["yard"].update(w=40, h=20), "cannot be resized")
        bad(lambda d: d["nodes"]["sea"].update(w=10), "a position is")
        bad(lambda d: d["nodes"]["power:claude-code"].update(w=40, h=40), "overlaps")
        bad(lambda d: d.update(walls=[[[1, 1], [2, 2]]]), "A wall is")
        bad(lambda d: d.update(walls=[[[1, 1]]]), "A wall is")
        bad(lambda d: d.update(trees=[[1, 1, 1]]), "A tree is")
        bad(lambda d: d.update(trees=[[1, 1]] * (F.MAX_TREES + 1)), "Too many trees")
        bad(lambda d: d.update(walls={}), "lists")


class Merge(unittest.TestCase):
    def test_a_new_station_gets_a_spot_and_a_belt(self):
        small = ctx({**ROLES, "roles": ["analyst"]}, 2)
        d = F.default_plan(small)
        big = ctx({**ROLES, "roles": ["analyst", "architect"]}, 2)
        m = F.merge(d, big)
        self.assertIsNotNone(m)
        self.assertIn("station:architect", m["nodes"])
        self.assertEqual(F.validate(m, big), [])

    def test_an_older_layout_gains_the_harbor_and_the_notifiers(self):
        old = json.loads(json.dumps(F.default_plan(CTX)))
        for k in ("harbor", "notify:telegram", "notify:slack"):
            del old["nodes"][k]
        del old["belts"]["harbor>receiving"]
        m = F.merge(old, CTX)
        self.assertIsNotNone(m)
        self.assertTrue({"harbor", "notify:telegram", "notify:slack"} <= set(m["nodes"]))
        self.assertIn("harbor>receiving", m["belts"])
        self.assertEqual(F.validate(m, CTX), [])

    def test_an_empty_floor_is_not_saved(self):
        empty = {"version": F.VERSION, "grid": F.G, "nodes": {}, "belts": {}, "pieces": [], "districts": {}, "walls": [], "trees": []}
        errs = F.validate(empty, CTX)
        self.assertTrue(any(e.startswith("Missing buildings") for e in errs), errs)

    def test_a_removed_station_is_dropped(self):
        m = F.merge(F.default_plan(CTX), ctx({**ROLES, "review": False}, 2))
        self.assertNotIn("station:review", m["nodes"])
        self.assertFalse(any("station:review" in k for k in m["belts"]))

    def test_a_yard_that_grows_into_a_neighbour_falls_back(self):
        d = json.loads(json.dumps(F.default_plan(ctx(ROLES, 0))))
        yx, yy = d["nodes"]["yard"]["x"], d["nodes"]["yard"]["y"]
        d["nodes"]["airfield"] = {"x": yx + 6, "y": yy + 1}           # beside the small yard, under where five trains would go
        big = ctx(ROLES, 5)
        self.assertIsNone(F.merge(d, big))

    def test_a_missing_or_broken_file_draws_the_default(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertEqual(F.current(t, CTX), (None, ""))
            Path(t, F.FILE).write_text("{not json")
            plan, why = F.current(t, CTX)
            self.assertIsNone(plan)
            self.assertIn("could not be read", why)
            Path(t, F.FILE).write_text(json.dumps({**F.default_plan(CTX), "version": 9}))
            self.assertIn("version", F.current(t, CTX)[1])
            F.save(t, F.default_plan(CTX))
            plan, why = F.current(t, CTX)
            self.assertEqual(why, "")
            self.assertIs(F.current(t, CTX)[0], plan)                 # merged once per file and config

    def test_save_is_0600_even_over_a_leftover_tmp(self):
        with tempfile.TemporaryDirectory() as t:
            tmp = Path(t, F.FILE).with_suffix(".tmp")
            tmp.write_text("x")
            os.chmod(tmp, 0o644)
            F.save(t, F.default_plan(CTX))
            self.assertEqual(stat.S_IMODE(Path(t, F.FILE).stat().st_mode), 0o600)


class Drawn(unittest.TestCase):
    def draw(self, doc, states=None, extras=None):
        cfg, workers = ROLES, [{"name": "w1", "online": True, "job": {"issue": 7, "recipe": "web"}}, {"name": "w2", "online": True, "job": None}]
        order = board.floor_order(cfg)
        fl = {sid: {"state": "none", "refs": [], "count": 0} for sid, _, _ in order}
        for sid, (state, refs) in (states or {}).items():
            fl[sid] = {"state": state, "refs": refs, "count": len(refs)}
        ex = {"power": [{**h, "on": True} for h in cfg["power"]], **(extras or {})}
        return plant.floor_map(order, fl, workers, True, 1000.0, board._floor_word, lambda s: "#", ex, F.compile_plan(doc, CTX))

    def test_crates_ride_the_saved_belts(self):
        d = moved(F.default_plan(CTX), "station:build", 0, 3)
        svg = self.draw(d, {"build": ("run", ['<b>"x"</b>']), "classify": ("run", ["acme/a#1"])})
        self.assertNotIn("style=", svg)
        self.assertNotIn('<b>"x"</b>', svg)
        C = F.compile_plan(d, CTX)
        hop = C["hops"][F.hop_id("station:architect", "station:build")]
        crates = re.findall(r'<g class="fn-crate">(?:(?!</g>).)*?<animateMotion path="([^"]+)"', svg, re.S)
        ends = [tuple(float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", p)[-2:]) for p in crates]
        starts = [tuple(float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", p)[:2]) for p in crates]
        self.assertIn(tuple(map(float, hop["pts"][-1])), ends)
        self.assertIn(tuple(map(float, hop["pts"][0])), starts)

    def test_the_harbor_stands_anywhere_with_its_belt_to_receiving_and_the_dock_follows_it(self):
        d = moved(F.default_plan(CTX), "harbor", 0, 32)                 # far out, under the airfield; its belt is routed again
        self.assertEqual(F.validate(d, CTX), [])
        trips = [{"name": "n", "source": "gh", "when": "soon", "progress": 0.2, "due": True}]
        svg = self.draw(d, extras={"schedules": trips})
        C = F.compile_plan(d, CTX)
        x, y, w, h = C["box"]["harbor"]
        self.assertIn(f'<rect class="hb-box" x="{plant._f(x)}" y="{plant._f(y)}"', svg)
        self.assertIn('class="hb due"', svg)                               # a run is due: a ship lies at the quay
        dock = min(900.0, max(100.0, y + h / 2 - C["at"]["sea"][1]))
        wall = plant.coast_x(plant.SEA_W, 1320, dock)                       # the crane stands on the coast at the harbor's height
        self.assertIn(f'<circle class="sh-base" cx="{plant._f(wall)}" cy="{plant._f(dock)}"', svg)
        hop = C["hops"]["harbor>receiving"]
        self.assertIn(plant.belt(plant.trace(hop["pts"])[0].d, "fn-belt thin"), svg)
        self.assertNotIn("H " + plant._f(C["at"]["receiving"][0]) + '"', svg)    # no quay drawn by itself any more

    def test_a_layout_without_the_harbor_belt_is_refused(self):
        d = json.loads(json.dumps(F.default_plan(CTX)))
        del d["belts"]["harbor>receiving"]
        self.assertTrue(any("No belt from The harbor to Receiving" in e for e in F.validate(d, CTX)))

    def test_notifiers_are_wireless_machines_lit_when_set_up(self):
        d = F.default_plan(CTX)
        self.assertEqual({k for k in d["nodes"] if k.startswith("notify:")}, {"notify:telegram", "notify:slack"})
        self.assertFalse(any("notify:" in k for k in d["belts"]))         # no belt and no hop: they reach a person wherever they stand
        self.assertFalse(any("notify:" in a + b for a, b in CTX.hops()))
        svg = self.draw(d, extras={"notify": [{"name": "telegram", "on": True}, {"name": "slack", "on": False}]})
        self.assertIn('aria-label="Notifier Telegram: set up, wireless"', svg)
        self.assertIn('aria-label="Notifier Slack: not set up"', svg)
        self.assertEqual(svg.count('class="nt-wave'), 6)
        moved_d = json.loads(json.dumps(d))
        moved_d["nodes"]["notify:slack"] = {"x": 3, "y": 70}              # anywhere free
        self.assertEqual(F.validate(moved_d, CTX), [])

    def test_the_default_floor_shows_the_notifiers_under_power(self):
        order = board.floor_order(ROLES)
        fl = {sid: {"state": "none", "refs": [], "count": 0} for sid, _, _ in order}
        svg = plant.floor_map(order, fl, [], True, 1000.0, extras={"notify": [{"name": "telegram", "on": True}, {"name": "slack", "on": False}]})
        self.assertIn("NOTIFIERS · WIRELESS", svg)
        self.assertIn('class="nt on"', svg)

    def test_a_resized_station_is_stretched_and_scenery_is_drawn(self):
        d = F.default_plan(CTX)
        d["nodes"]["power:claude-code"].update(w=12, h=6)
        d["walls"] = [[[150, 10], [170, 10], [170, 30]]]
        d["trees"] = [[150, 40]]
        svg = self.draw(d)
        x, y = d["nodes"]["power:claude-code"]["x"] * F.G, d["nodes"]["power:claude-code"]["y"] * F.G
        self.assertIn(f'transform="translate({x} {y}) scale(1.2) translate(0 0)"', svg)
        self.assertIn('<path class="fp-wall" d="M 3000 200 L 3400 200 L 3400 600"/>', svg)
        self.assertIn(terrain.tree(3000, 800, 36, terrain.seed_of(150, 40)), svg)            # an older single tree, in the new tree's look
        self.assertGreaterEqual(float(re.search(r'<svg class="fm" viewBox="0 0 ([\d.]+)', svg).group(1)), 3400)
        self.assertNotIn("style=", svg)

    def test_a_stretched_building_keeps_its_proportions_and_a_resized_sea_fills_with_water(self):
        d = F.default_plan(CTX)
        d["nodes"]["power:claude-code"].update(w=20, h=5)                # twice as wide: drawn at its own size, centred
        d["nodes"]["sea"].update(w=15, h=33)                             # half as tall: half the size, water either side
        self.assertEqual(F.validate(d, CTX), [])
        svg = self.draw(d)
        p = d["nodes"]["power:claude-code"]
        self.assertIn(f'transform="translate({p["x"] * F.G + 100} {p["y"] * F.G}) scale(1) translate(0 0)"', svg)
        self.assertIn('<g transform="translate(240 0)"><defs><linearGradient id="sh-depth"', svg)    # the water fills the whole box
        self.assertIn(plant.sea_water(300, 660), svg)
        self.assertIn('transform="translate(315 0) scale(0.5) translate(0 0)"', svg)
        C = F.compile_plan(d, CTX)
        x, y, w, h = C["box"]["harbor"]
        dock = max(100.0, min(900.0, (y + h / 2) / 0.5))                    # the crane at the harbor's height, in the sea's own units,
        wall = (240 + plant.coast_x(300, 660, dock * 0.5) - 315) / 0.5      # on the coast of the water drawn under it
        self.assertIn(f'<circle class="sh-base" cx="{plant._f(wall)}" cy="{plant._f(dock)}"', svg)
        self.assertNotRegex(svg, r'scale\([\d.]+ [\d.]+\)')                 # never a stretch that squashes the text

    def test_inserters_stand_between_the_building_and_its_belt_and_have_hands(self):
        svg = self.draw(F.default_plan(CTX))
        self.assertIn('class="fn-ins-h" d="M ', svg)
        self.assertEqual(plant._pivot((100, 100, 140, 100), (120, 80)), (120, 100 - plant.REACH, -90))
        self.assertGreater(svg.rindex('class="fn-ins"'), svg.rindex('class="fm-m '))            # drawn over the stations

    def test_every_node_has_a_picture_for_the_editor(self):
        for nid in CTX.nodes():
            art = plant.node_art(nid, F.label(nid), CTX.trains)
            self.assertTrue(art, nid)
            self.assertNotIn("style=", art)
        self.assertIn('class="ry-yard"', plant.node_art("yard", "The Verify yard", 0))    # a building of its own on a layout

    def test_the_default_floor_is_unchanged_without_a_layout(self):
        order = board.floor_order(ROLES)
        fl = {sid: {"state": "none", "refs": [], "count": 0} for sid, _, _ in order}
        a = plant.floor_map(order, fl, [], True, 1000.0)
        b = plant.floor_map(order, fl, [], True, 1000.0, plan=None)
        self.assertEqual(a, b)
        self.assertNotIn("fe-", a)


class Editing(AdminCase):
    def doc(self, cookie):
        s, _, page = self.req("GET", "/floor/edit", cookie=cookie)
        self.assertEqual(s, 200)
        text = html.unescape(re.search(r'<textarea name="plan"[^>]*>(.*?)</textarea>', page, re.S).group(1))
        rev = re.search(r'name="rev" value="([^"]*)"', page).group(1)
        return json.loads(text), rev, page

    def test_it_needs_a_session_and_csrf(self):
        self.assertEqual(self.req("GET", "/floor/edit")[0], 303)
        cookie, _ = self.session()
        for path in ("/floor/layout/save", "/floor/layout/reset"):
            self.assertIn(self.req("POST", path, "plan=x")[0], (303, 403), path)          # signed out
            self.assertEqual(self.req("POST", path, "plan=x", cookie=cookie)[0], 403, path)
        self.assertFalse((self.state / F.FILE).exists())

    def test_the_page_and_the_script(self):
        cookie, _ = self.session()
        _, _, page = self.doc(cookie)
        self.assertNotIn("style=", page)
        self.assertIn('<script src="/static/floor-edit.js"', page)
        self.assertIn('<g data-art="station:build">', page)
        self.assertIn('data-tool="wall"', page)
        self.assertIn('data-act="zoomin"', page)
        self.assertIn('data-act="scratch"', page)                       # start from an empty floor, with the parts tray to fill it
        self.assertIn('class="fe-tray-list"', page)
        meta = json.loads(html.unescape(re.search(r'data-meta="([^"]*)"', page).group(1)))
        self.assertEqual(meta["nodes"]["harbor"]["group"], "Arrivals")
        self.assertEqual(meta["nodes"]["notify:telegram"]["group"], "Notifiers")
        self.assertIn(["harbor", "receiving", "The harbor → Receiving"], meta["hops"])
        self.assertIn("floor-edit.js", server.STATIC)
        s, h, js = self.req("GET", "/static/floor-edit.js")
        self.assertEqual(s, 200)
        self.assertIn("javascript", h["Content-Type"])
        self.assertNotIn(".style", js)                                   # attributes only: the CSP forbids inline styles
        self.assertIn("Edit layout", self.req("GET", "/", cookie=cookie)[2])

    def test_a_layout_with_a_big_terrain_saves_past_the_usual_body_limit(self):
        cookie, csrf = self.session()
        doc, rev, _ = self.doc(cookie)
        doc["terrain"] = {"items": [[["tree", "pine", "bush", "rock"][k % 4], 40 + (k * 37) % 3900, 2000 + (k * 53) % 350,
                                     40 if k % 4 < 2 else 24 if k % 4 == 2 else 30, 1000000 + k] for k in range(600)]}
        body = json.dumps(doc)
        self.assertGreater(len(body), 20000)
        s, h, _ = self.post(cookie, csrf, "/floor/layout/save", {"plan": body, "rev": rev})
        self.assertEqual((s, h.get("Location")), (303, "/?ok=layout_saved"))
        self.assertEqual(len(json.loads((self.state / F.FILE).read_text())["terrain"]["items"]), 600)
        s, _, _ = self.post(cookie, csrf, "/floor/layout/save", {"plan": "x" * (300 * 1024), "rev": F.rev(self.state)})
        self.assertEqual(s, 413)                                         # the layout's own limit, still bounded

    def test_save_reload_and_reset(self):
        cookie, csrf = self.session()
        doc, rev, _ = self.doc(cookie)
        self.assertEqual(rev, "")
        s, h, _ = self.post(cookie, csrf, "/floor/layout/save", {"plan": json.dumps(doc), "rev": rev})
        self.assertEqual((s, h.get("Location")), (303, "/?ok=layout_saved"))
        f = self.state / F.FILE
        self.assertEqual(stat.S_IMODE(f.stat().st_mode), 0o600)
        again, rev2, _ = self.doc(cookie)
        self.assertEqual(again["nodes"], doc["nodes"])
        self.assertTrue(rev2)
        self.assertEqual(self.post(cookie, csrf, "/floor/layout/save", {"plan": json.dumps(doc), "rev": ""})[0], 409)   # a stale tab
        self.assertEqual(self.post(cookie, csrf, "/floor/layout/reset", {"rev": ""})[0], 409)
        s, h, _ = self.post(cookie, csrf, "/floor/layout/reset", {"rev": rev2})
        self.assertEqual((s, h.get("Location")), (303, "/floor/edit?ok=layout_reset"))
        self.assertFalse(f.exists())

    def test_invalid_layouts_are_refused_and_escaped(self):
        cookie, csrf = self.session()
        doc, rev, _ = self.doc(cookie)
        doc["nodes"]['"><script>alert(1)</script>'] = {"x": 1, "y": 1}
        s, _, page = self.post(cookie, csrf, "/floor/layout/save", {"plan": json.dumps(doc), "rev": rev})
        self.assertEqual(s, 422)
        self.assertNotIn("<script>alert(1)", page)
        self.assertEqual(self.post(cookie, csrf, "/floor/layout/save", {"plan": "{nope", "rev": rev})[0], 422)
        self.assertEqual(self.post(cookie, csrf, "/floor/layout/save", {"plan": " " * (F.MAX_BYTES + 1), "rev": rev})[0], 422)
        self.assertFalse((self.state / F.FILE).exists())

    def test_a_broken_saved_file_falls_back_and_says_so(self):
        cookie, _ = self.session()
        (self.state / F.FILE).write_text('{"version": 1, "grid": 20, "nodes": {}, "belts": {}, "pieces": [], "x": 1}')
        s, _, home = self.req("GET", "/", cookie=cookie)
        self.assertEqual(s, 200)
        self.assertIn('class="fm"', home)
        self.assertIn("saved layout is not used", self.doc(cookie)[2])


if __name__ == "__main__":
    unittest.main()


WORKERS = ["linux-box", "my-mac", "win-pc"]
RAIL = F.Ctx([s for s, _, _ in board.floor_order(ROLES)], [h["name"] for h in ROLES.get("power") or []], True, 3, workers=WORKERS)


class Railway(unittest.TestCase):
    def test_the_default_lays_a_loop_for_every_worker_through_two_junctions(self):
        d = F.default_plan(RAIL)
        self.assertEqual(F.validate(d, RAIL), [])
        self.assertTrue({"depot", "junction:a", "junction:b", "worker:my-mac"} <= set(d["nodes"]))
        self.assertEqual(F.loops(d["tracks"], RAIL), {f"worker:{w}": True for w in WORKERS})
        self.assertIn("depot>station:ci", d["belts"])                    # the Train station hands the checked builds to CI

    def test_track_follows_the_rules_and_every_worker_needs_its_loop(self):
        good = F.default_plan(RAIL)

        def errs(change):
            d = json.loads(json.dumps(good))
            change(d)
            return " | ".join(F.validate(d, RAIL))
        self.assertIn("No track loop for my-mac", errs(lambda d: d["tracks"].pop("junction:a>worker:my-mac")))
        self.assertIn("No track loop for linux-box", errs(lambda d: d["tracks"].pop("depot>yard")))
        self.assertIn("Track cannot run from The Verify yard to The Train station", errs(lambda d: d["tracks"].update({"yard>depot": [[1, 1], [1, 3]]})))
        self.assertIn("must start beside", errs(lambda d: d["tracks"].update({"depot>yard": [[1, 1], [1, 3]]})))
        self.assertIn("Missing buildings: The Train station", errs(lambda d: d["nodes"].pop("depot")))
        self.assertNotIn("Missing", errs(lambda d: d["nodes"].pop("outside")))    # the outside area and unused junctions are optional
        self.assertIn("Unknown keys", errs(lambda d: d.update(rails={})))

    def test_track_through_a_building_is_refused(self):
        d = json.loads(json.dumps(F.default_plan(RAIL)))
        b = F.boxes(d["nodes"], RAIL)
        x, y, w, h = b["station:build"]
        d["nodes"]["depot"] = {"x": x + w + 3, "y": y}
        db = F.boxes(d["nodes"], RAIL)["depot"]
        yx, yy, yw, yh = b["yard"]
        d["tracks"]["depot>yard"] = [[db[0] - 1, y + 1], [x + 2, y + 1], [x + 2, yy - 2], [yx - 1, yy - 2], [yx - 1, yy + 2]]
        self.assertTrue(any("runs through Build" in e for e in F.validate(d, RAIL)), F.validate(d, RAIL))

    def test_a_new_worker_gets_a_stop_and_its_loop(self):
        small = F.Ctx(RAIL.ids, RAIL.harnesses, True, 2, workers=WORKERS[:2])
        m = F.merge(F.default_plan(small), RAIL)
        self.assertIsNotNone(m)
        self.assertIn("worker:win-pc", m["nodes"])
        self.assertTrue(F.loops(m["tracks"], RAIL)["worker:win-pc"])
        self.assertEqual(F.validate(m, RAIL), [])

    def test_an_older_layout_without_the_railway_gains_it(self):
        old = json.loads(json.dumps(F.default_plan(RAIL)))
        for k in [k for k in old["nodes"] if k.split(":")[0] in ("depot", "worker", "junction", "outside")]:
            del old["nodes"][k]
        old.pop("tracks")
        old["belts"].pop("depot>station:ci")
        m = F.merge(old, RAIL)
        self.assertIsNotNone(m)
        self.assertEqual(F.validate(m, RAIL), [])

    def test_trains_run_their_loops_while_their_worker_has_a_job_and_junctions_have_signals(self):
        d = F.default_plan(RAIL)
        order = board.floor_order(ROLES)
        fl = {sid: {"state": "none", "refs": [], "count": 0} for sid, _, _ in order}
        ws = [{"name": "linux-box", "online": True, "job": {"issue": 7, "recipe": "web"}}, {"name": "my-mac", "online": True, "job": None},
              {"name": "win-pc", "online": False}]
        svg = plant.floor_map(order, fl, ws, True, 1000.0, board._floor_word, lambda s: "#", {"power": []}, F.compile_plan(d, RAIL))
        self.assertIn('class="ry-yard"', svg)
        self.assertIn('class="ry-depot"', svg)
        self.assertEqual(svg.count('class="ry-junction"'), 2)
        self.assertIn('aria-label="Worker linux-box: Online · web check"', svg)
        self.assertEqual(svg.count('class="ry-stop"'), 3)                # each worker's stop plate and loader, on its loop
        self.assertIn('Verify stop', svg)
        moving = re.findall(r'<g class="fn-car">', svg)
        self.assertEqual(len(moving), plant.yard.CARS[0])                # only linux-box's train drives; the others wait at the yard
        self.assertEqual(svg.count('class="fm-sig"'), 3 + 1 + 3)         # one at each platform, one into junction A, three into B
        self.assertNotIn("style=", svg)

    def test_the_editor_offers_track_only_with_the_workers_on(self):
        self.assertIn(["yard", "junction:a"], F.meta(RAIL)["tracks"])
        self.assertEqual(F.meta(CTX if not CTX.yard_on else F.Ctx(RAIL.ids))["tracks"], [])
        self.assertTrue(F.meta(RAIL)["nodes"]["junction:c"]["optional"])
        self.assertEqual(F.meta(RAIL)["nodes"]["worker:win-pc"]["group"], "Workers and rail")

    def test_the_timetable_keeps_the_shared_track_to_one_train_and_signals_go_green_for_them(self):
        for n in (2, 3, 5):
            names = [f"w{k}" for k in range(n)]
            ctx = F.Ctx(RAIL.ids, RAIL.harnesses, True, n, workers=names)
            C = F.compile_plan(F.default_plan(ctx), ctx)
            run = sorted(C["circuits"])
            T, motion = plant.timetable(C, run)
            marks = {w: plant.circuit_marks(C, w)[1] for w in run}
            uses = {}
            for w in run:
                for tid in marks[w]["tracks"]:
                    uses[tid] = uses.get(tid, 0) + 1
            shared = {t for t, k in uses.items() if k > 1}

            def where(pts, t):
                for (t0, d0), (t1, d1) in zip(pts, pts[1:]):
                    if t0 <= t <= t1:
                        return d0 if t1 == t0 else d0 + (d1 - d0) * (t - t0) / (t1 - t0)
                return pts[-1][1]

            def on_block(w, d):
                L = marks[w]["len"]
                if d <= 0.5 or d >= L - 0.5:                         # in the yard
                    return False
                for car in range(4):
                    x = (d - car * plant.yard.GAP) % L
                    if any(marks[w]["tracks"][t][0] + 1 < x < marks[w]["tracks"][t][1] for t in shared):
                        return True
                return False
            for i in range(int(T * 10)):
                on = [w for w in run if on_block(w, where(motion[w], i / 10))]
                self.assertLessEqual(len(on), 1, (n, i / 10, on))
            self.assertTrue(all(motion[w][-1] == (T, marks[w]["len"]) for w in run))
        self.assertIn('values="0.15;1;0.15"', plant.lamps([5.0], 20.0, "0s"))     # green for a moment as its train goes by
        self.assertIn('class="g off"', plant.lamps([], 20.0, "0s"))


def scene(ctx=CTX):
    """The default layout with a patch of every kind of terrain, away from the buildings and belts."""
    d = json.loads(json.dumps(F.default_plan(ctx)))
    d["terrain"] = {"items": [["tree", 600, 2200, 60, 7], ["pine", 700, 2200, 50, 3], ["bush", 800, 2200, 24, 9], ["rock", 900, 2200, 40, 11],
                              ["pond", 1100, 2250, 100, 5], ["lamp", 1300, 2200, 130, 1], ["fog", 1500, 2200, 140, 2], ["shade", 1700, 2200, 90, 3]],
                    "rivers": [[[2000, 2150], [2060, 2180], [2120, 2160], [2180, 2200], [2240, 2190]]],
                    "tiles": terrain.runs({"grass": {(10, 57), (11, 57), (12, 57)}, "water": {(20, 57), (21, 57), (22, 57), (20, 58)}}),
                    "fences": [[[120, 112], [130, 112]]], "gates": [[131, 112, "h"]], "roads": [[[120, 116], [140, 116]]], "hazards": [[150, 110, 4, 3]]}
    return d


class Terrain(unittest.TestCase):
    def test_a_scene_of_every_kind_is_valid_kept_and_drawn(self):
        d = scene()
        self.assertEqual(F.validate(d, CTX), [])
        self.assertEqual(F.canonical(d)["terrain"], d["terrain"])
        m = F.merge(d, CTX)
        self.assertEqual(m["terrain"], d["terrain"])
        svg = Drawn().draw(d)
        for cls in ("tr-tree", "tr-pine", "tr-bush", "tr-rock", "tr-pond", "tr-river", "gd-grass", "fc-rail", "fc-gate", "rd-road", "hz-zone",
                    "lt-pool", "lt-fog", "lt-shade", "dk-duck", "bd-bird", 'id="tl-grass"'):
            self.assertIn(cls, svg, cls)
        self.assertIn(terrain.svg(d["terrain"], F.G, 0, 0, d["walls"], d["trees"], "under")[:400], svg)
        self.assertNotIn("style=", svg)

    def test_the_terrain_shape_is_checked(self):
        good = scene()

        def bad(change, needle):
            d = json.loads(json.dumps(good))
            change(d["terrain"])
            errs = F.validate(d, CTX)
            self.assertTrue(any(needle in e for e in errs), (needle, errs))
        bad(lambda t: t.update(lava=[]), "Unknown terrain")
        bad(lambda t: t["items"].append(["castle", 1, 1, 40, 1]), "A terrain item is")
        bad(lambda t: t["items"].append(["tree", 1, 1, 400, 1]), "its size in range")
        bad(lambda t: t["items"].append(["tree", 99999, 1, 40, 1]), "inside the floor")
        bad(lambda t: t.update(items=[["rock", 1, 1, 30, 1]] * (terrain.LIMITS["items"] + 1)), "at most 600")
        bad(lambda t: t["rivers"].append([[1, 1]]), "A river is 2 to")
        bad(lambda t: t["tiles"].update(lava=[[0, 0, 1]]), "grass, dirt, sand, concrete or water")
        bad(lambda t: t["tiles"]["grass"].append([20, 57, 1]), "only one kind")             # already water
        bad(lambda t: t["tiles"]["grass"].append([0, 0, 999]), "inside the floor")
        bad(lambda t: t["fences"].append([[1, 1], [2, 2]]), "straight across or down")
        bad(lambda t: t["gates"].append([1, 1, "x"]), "A gate is")
        bad(lambda t: t["hazards"].append([199, 1, 5, 5]), "A hazard zone is")

    def test_belts_and_track_do_not_cross_water(self):
        d = scene()
        a, b = d["belts"]["receiving>station:poll"][0], d["belts"]["receiving>station:poll"][-1]
        mid = d["belts"]["receiving>station:poll"][1]
        d["terrain"]["items"].append(["pond", mid[0] * F.G, mid[1] * F.G, 80, 4])
        self.assertTrue(any("The belt from Receiving to Poll crosses water" in e for e in F.validate(d, CTX)))
        d = scene()
        p = d["belts"]["receiving>station:poll"][0]
        d["terrain"]["tiles"] = terrain.runs({"water": {(p[0] * F.G // terrain.TILE, p[1] * F.G // terrain.TILE)}})
        self.assertTrue(any("crosses water" in e for e in F.validate(d, CTX)))
        d = scene()
        q = d["belts"]["receiving>station:poll"][-1]
        d["terrain"]["rivers"].append([[q[0] * F.G - 60, q[1] * F.G], [q[0] * F.G + 60, q[1] * F.G]])
        self.assertTrue(any("crosses water" in e for e in F.validate(d, CTX)))
        d = scene(RAIL)
        tid, pts = next(iter(d["tracks"].items()))
        d["terrain"]["items"].append(["pond", pts[0][0] * F.G, pts[0][1] * F.G, 70, 4])
        self.assertTrue(any("The track from" in e and "crosses water" in e for e in F.validate(d, RAIL)))

    def test_a_belt_crosses_a_wall_only_underground_and_track_never(self):
        d = scene()
        pts = d["belts"]["station:poll>station:classify"]
        (x1, y1), (x2, y2) = pts[0], pts[-1]
        assert y1 == y2 and x2 - x1 >= 3, pts                           # a straight run along the intake row
        wx = x1 + 1
        d["walls"] = [[[wx, y1 - 1], [wx, y1 + 1]]]
        self.assertTrue(any("crosses a wall: take it under with an underground belt" in e for e in F.validate(d, CTX)))
        d["pieces"].append({"kind": "underground", "from": [x1, y1], "to": [x1 + 2, y1]})
        self.assertFalse(any("crosses a wall" in e for e in F.validate(d, CTX)), F.validate(d, CTX))
        r = scene(RAIL)
        tid, tp = next((k, v) for k, v in r["tracks"].items() if k == "depot>yard")
        (a, b) = tp[0], tp[1]
        c = ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2)
        r["walls"] = [[[c[0], c[1] - 1], [c[0], c[1] + 1]]] if a[1] == b[1] else [[[c[0] - 1, c[1]], [c[0] + 1, c[1]]]]
        self.assertTrue(any("The track from The Train station to The Verify yard runs into a wall" in e for e in F.validate(r, RAIL)))

    def test_track_passes_a_fence_only_at_a_gate(self):
        r = scene(RAIL)
        tp = r["tracks"]["depot>yard"]
        a, b = tp[0], tp[1]
        c = ((a[0] + b[0]) // 2, (a[1] + b[1]) // 2)
        across = a[1] == b[1]
        r["terrain"]["fences"] = [[[c[0], c[1] - 2], [c[0], c[1] + 2]]] if across else [[[c[0] - 2, c[1]], [c[0] + 2, c[1]]]]
        self.assertTrue(any("runs into a fence: put a gate where it crosses" in e for e in F.validate(r, RAIL)))
        r["terrain"]["gates"] = [[c[0], c[1], "v" if across else "h"]]
        self.assertFalse(any("runs into a fence" in e for e in F.validate(r, RAIL)), F.validate(r, RAIL))
        d = scene()                                                       # belts carry crates through a fence
        pts = d["belts"]["station:poll>station:classify"]
        wx = (pts[0][0] + pts[-1][0]) // 2
        d["terrain"]["fences"] = [[[wx, pts[0][1] - 1], [wx, pts[0][1] + 1]]]
        self.assertFalse(any("fence" in e for e in F.validate(d, CTX)))

    def test_nothing_is_built_in_a_hazard_zone(self):
        d = scene()
        n = d["nodes"]["station:build"]
        d["terrain"]["hazards"].append([n["x"] + 1, n["y"] + 1, 2, 2])
        self.assertTrue(any("Build stands in a hazard zone" in e for e in F.validate(d, CTX)))

    def test_ground_runs_and_cells_round_trip(self):
        cells = {"grass": {(0, 0), (1, 0), (2, 0), (5, 0), (0, 1)}, "water": {(3, 3)}}
        r = terrain.runs(cells)
        self.assertEqual(r["grass"], [[0, 0, 3], [5, 0, 1], [0, 1, 1]])
        self.assertEqual(terrain.cells_of(r)["grass"], cells["grass"])

    def test_ducks_live_on_each_body_of_water_two_at_most_and_birds_land_by_trees(self):
        t = {"items": [["pond", 100, 100, 70, 1], ["pond", 400, 100, 100, 2], ["tree", 50, 50, 40, 1], ["bush", 80, 80, 20, 1], ["tree", 90, 90, 40, 1],
                       ["tree", 120, 90, 40, 1]],
             "rivers": [[[0, 300], [100, 300], [200, 300], [300, 300], [400, 300], [500, 300], [600, 300], [700, 300], [800, 300], [900, 300], [1000, 300]]],
             "tiles": terrain.runs({"water": {(30, 30), (31, 30)}})}                         # two tiles: too few for a duck yet
        self.assertEqual([b[0] for b in terrain.bodies(t)], ["pond", "pond", "river"])
        self.assertEqual(terrain.ducks(t).count('class="dk-duck"'), 1 + 2 + 2)              # a small pond 1, a big one 2, a long river 2
        self.assertEqual(terrain.birds(t, 2000, 2000).count('class="bd-bird"'), 3)          # one per tree or bush, three at most
        t["tiles"] = terrain.runs({"water": {(30, 30), (31, 30), (32, 30), (30, 31), (31, 31)}})
        self.assertEqual(terrain.ducks(t).count('class="dk-duck"'), 1 + 2 + 2 + 2)

    def test_terrain_is_the_same_every_time_and_each_item_its_own_shape(self):
        a = terrain.svg(scene()["terrain"], F.G, 4000, 2400)
        self.assertEqual(a, terrain.svg(scene()["terrain"], F.G, 4000, 2400))
        self.assertNotEqual(terrain.tree(0, 0, 60, 1), terrain.tree(0, 0, 60, 2))
        self.assertEqual(terrain.f(0.25), "0.3")                         # half up, as terrain.js rounds
        self.assertEqual(terrain.f(-0.04), "0")
        r = terrain.rng(42)
        self.assertAlmostEqual(r(), 0.6011037519201636)                 # mulberry32's first number for 42

    def test_a_big_terrain_fits_the_document_and_the_save(self):
        d = scene()
        d["terrain"]["items"] = [[["tree", "pine", "bush", "rock"][k % 4], 40 + (k * 37) % 3900, 2000 + (k * 53) % 350, 40 if k % 4 < 2 else 24 if k % 4 == 2 else 30, k] for k in range(600)]
        self.assertEqual(F.validate(d, CTX), [])
        self.assertLess(len(json.dumps(F.canonical(d), separators=(",", ":"))), F.MAX_BYTES)


def park_scene(ctx=CTX):
    """The default layout with a park beside a pond and a road, away from the buildings and belts."""
    d = scene(ctx)
    d["terrain"]["items"] += [["fetch", 600, 2400, 200, 2], ["playground", 850, 2400, 180, 3], ["picnic", 1050, 2400, 110, 4],
                              ["bench", 1250, 2250, 70, 6], ["dogwalk", 1450, 2400, 180, 8]]
    return d


class Park(unittest.TestCase):
    def test_the_park_is_valid_kept_and_drawn_side_on_and_an_odd_seed_mirrors_a_piece(self):
        d = park_scene()
        self.assertEqual(F.validate(d, CTX), [])
        self.assertEqual(F.merge(d, CTX)["terrain"], d["terrain"])
        svg = Drawn().draw(d)
        for cls in ("pk-fetch", "pk-playground", "pk-picnic", "pk-bench", "pk-dogwalk", 'id="pk-check"'):
            self.assertIn(cls, svg, cls)
        self.assertNotIn("style=", svg)
        self.assertIn('<g class="pk-playground" transform="translate(940 2335) scale(-1 1)">', svg)       # seed 3: mirrored
        self.assertIn('<g class="pk-fetch" transform="translate(500 2340)">', svg)

    def test_the_old_man_walks_to_the_nearest_water_and_back_or_stays_on_his_bench(self):
        t = {"items": [["bench", 400, 300, 70, 2], ["pond", 250, 320, 90, 5]]}
        svg = terrain.svg(t, F.G, 2000, 1000)
        self.assertIn('<g class="pk-man"><animateMotion path="M 35 37 L -62 38.9"', svg)            # to the pond's near shore
        self.assertIn('keyPoints="0;0;1;1;0;0"', svg)                                              # sits, walks, feeds, walks back
        self.assertIn('<g transform="scale(-1 1)">', svg)                                          # facing the pond, on his left
        alone = terrain.svg({"items": [["bench", 400, 300, 70, 2]]}, F.G, 2000, 1000)
        self.assertNotIn("pk-man", alone)
        self.assertIn('<g transform="translate(35 37)">', alone)
        river = terrain.svg({"items": [["bench", 400, 300, 70, 2]], "rivers": [[[600, 280], [700, 300]]]}, F.G, 2000, 1000)
        self.assertIn("pk-man", river)                                                             # a river is water too

    def test_cars_drive_both_ways_on_every_road(self):
        t = {"roads": [[[10, 30], [60, 30]], [[70, 10], [70, 40], [90, 40]]]}
        svg = terrain.svg(t, F.G, 2000, 1000, layer="under")
        paths = re.findall(r'<g class="tf-car">.*?<animateMotion path="([^"]+)"', svg)
        self.assertGreaterEqual(len(paths), 4)
        self.assertTrue(any(p.startswith("M 200 604.5") for p in paths) and any(p.startswith("M 1200 595.5") for p in paths), paths)
        self.assertIn("M 1395.5 200 L 1395.5 804.5 L 1800 804.5", paths)                                  # round a corner on its own lane
        self.assertIn("M 1800 795.5 L 1404.5 795.5 L 1404.5 200", paths)
        self.assertEqual(svg, terrain.svg(t, F.G, 2000, 1000, layer="under"))                      # the same cars every time
        self.assertNotIn("tf-car", terrain.svg({"roads": [[[1, 1], [3, 1]]]}, F.G, 2000, 1000))    # a stub of a road has none

    def test_track_crossing_a_road_goes_over_a_bridge_and_only_where_it_runs_straight(self):
        t = {"roads": [[[10, 30], [60, 30]]]}
        tracks = [[(400, 200), (400, 900)], [(800, 590), (800, 900)], [(100, 100), (300, 100)]]
        svg = terrain.svg(t, F.G, 2000, 1000, layer="bridge", tracks=tracks)
        self.assertEqual(svg.count('class="tf-bridge"'), 1)
        self.assertIn('<g class="tf-bridge" transform="translate(400 600) rotate(90)">', svg)       # the second track bends too close
        self.assertEqual(terrain.svg(t, F.G, 2000, 1000, layer="bridge"), "")
        all_ = terrain.svg(t, F.G, 2000, 1000, tracks=tracks)
        self.assertLess(all_.index("tf-car"), all_.index("tf-bridge"))                             # cars under the deck

    def test_the_floor_bridges_its_own_track_over_a_road_across_it(self):
        d = scene()
        tid, pts = next((k, p) for k, p in d["tracks"].items() if any(a[0] == b[0] and abs(a[1] - b[1]) >= 6 for a, b in zip(p, p[1:])))
        a, b = next((a, b) for a, b in zip(pts, pts[1:]) if a[0] == b[0] and abs(a[1] - b[1]) >= 6)
        y = (a[1] + b[1]) // 2
        d["terrain"]["roads"].append([[a[0] - 4, y], [a[0] + 4, y]])
        svg = Drawn().draw(d)
        self.assertIn(f'<g class="tf-bridge" transform="translate({a[0] * F.G} {y * F.G}) rotate(90)">', svg)
        self.assertLess(svg.index("tf-bridge"), svg.index("fm-train") if "fm-train" in svg else len(svg))

    def test_hitboxes_keep_the_park_off_the_buildings_and_off_each_other(self):
        d = park_scene()
        x, y, w, h = F.boxes(d["nodes"], CTX)["station:build"]
        d["terrain"]["items"].append(["picnic", int((x + w / 2) * F.G), int((y + h / 2) * F.G), 110, 1])
        errs = F.validate(d, CTX)
        self.assertIn("The picnic overlaps Build.", errs)
        d = park_scene()
        d["terrain"]["items"] += [["bench", 610, 2400, 70, 2], ["tree", 860, 2400, 40, 1]]
        errs = F.validate(d, CTX)
        self.assertIn("The fetch with a dog overlaps the bench.", errs)
        self.assertIn("The playground overlaps a tree.", errs)
        self.assertEqual(sum("overlaps the bench" in e or "overlaps the fetch" in e for e in errs), 1)  # each pair once
        d = park_scene()
        d["terrain"]["items"].append(["lamp", 600, 2400, 130, 1])                                   # light lies over anything
        self.assertEqual(F.validate(d, CTX), [])

    def test_the_editor_draws_the_park_traffic_and_bridges_from_the_same_strings(self):
        js = (Path(terrain.__file__).parent / "static" / "terrain.js").read_text()
        for name in ("PK_FETCH", "PK_PLAYGROUND", "PK_PICNIC", "PK_BENCH", "PK_DOGWALK", "OM_FRONT", "OM_SIT", "OM_GO", "OM_FEED", "OM_BACK",
                     "OM_KEYS", "CAR_A", "CAR_B", "TRUCK_A", "TRUCK_B", "BRIDGE", "PK_CHECK"):
            self.assertIn(f"var {name} = {json.dumps(getattr(terrain, name))};", js, name)
        for kind, (w, h) in terrain.PARK.items():
            self.assertIn(f"{kind}: [{w}, {h}]", js)
