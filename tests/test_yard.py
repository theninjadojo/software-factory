import re
import time
import unittest

from factory.ui import board, yard

ROLES = {"roles": ["analyst", "designer", "architect"], "review": True, "ci": "on", "workers": True}


def floor(cfg=ROLES, workers=(), states=None, now=1000.0):
    order = board.floor_order(cfg)
    fl = {sid: {"state": "none", "refs": [], "count": 0} for sid, _, _ in order}
    for sid, (state, refs) in (states or {}).items():
        fl[sid] = {"state": state, "refs": refs, "count": len(refs)}
    return order, yard.floor_map(order, fl, list(workers), bool(cfg.get("workers")), now, board._floor_word, lambda sid: f"/tickets?at={sid}")


def worker(name, online=True, job=None):
    return {"name": name, "online": online, "job": job}


def floor_x(extras, states, workers=(), cfg=ROLES, now=1000.0):
    order = board.floor_order(cfg)
    fl = {sid: {"state": "none", "refs": [], "count": 0} for sid, _, _ in order}
    for sid, (state, refs) in states.items():
        fl[sid] = {"state": state, "refs": refs, "count": len(refs)}
    return yard.floor_map(order, fl, list(workers), True, now, board._floor_word, lambda sid: f"/tickets?at={sid}", extras)


def trips(svg):
    """Every crate that rides: (its path, where the path ends)."""
    out = []
    for d in re.findall(r'<g class="fn-crate">(?:(?!</g>).)*?<animateMotion path="([^"]+)"', svg, re.S):
        nums = re.findall(r"-?\d+(?:\.\d+)?", d)
        out.append((d, (float(nums[-2]), float(nums[-1]))))
    return out


class Stations(unittest.TestCase):
    def test_the_stations_follow_the_config(self):
        ids = lambda cfg: [sid for sid, _, _ in board.floor_order(cfg)]
        self.assertEqual(ids(ROLES), ["poll", "classify", "route", "analyst", "designer", "architect", "build", "review", "ci", "pr"])
        self.assertEqual(ids({**ROLES, "review": False, "ci": "off"}), ["poll", "classify", "route", "analyst", "designer", "architect", "build", "pr"])
        custom = board.floor_order({**ROLES, "roles": ["analyst", "security_review"]})
        self.assertIn(("security_review", "Security Review", yard.GENERIC_ICON), custom)

    def test_two_rows_round_the_loop_each_station_its_own_building(self):
        order, svg = floor({**ROLES, "roles": ["analyst", "designer", "architect", "docs", "security"]})
        self.assertEqual(svg.count('<a class="fm-m '), len(order))
        self.assertIn('href="/tickets?at=docs"', svg)
        lay = yard.layout(12)
        self.assertEqual(lay["per"], 6)
        self.assertEqual({y for x, y, top in lay["pos"].values()}, {yard.TOP_Y, yard.BOT_Y})
        self.assertLess(lay["pos"][0][0], lay["pos"][5][0])                            # the top row runs left to right
        self.assertGreater(lay["pos"][6][0], lay["pos"][11][0])                        # the bottom row right to left
        self.assertEqual(svg.count('<svg class="ma '), len(order) + 1)                 # a building each, and GitHub's depot
        _, svg = floor(states={"build": ("run", ["#1"])})
        self.assertIn('<svg class="ma on"', svg)                                       # a station at work is powered
        self.assertIn('<svg class="ma idle"', svg)


class Crates(unittest.TestCase):
    def test_a_crate_is_taken_by_the_station_it_is_for(self):
        svg = floor_x({}, {"classify": ("run", ["#214"]), "analyst": ("wait", ["#208", "#211"]), "review": ("fail", ["#190"])})
        (d, end), = trips(svg)
        lay = yard.layout(10)
        (pick, _, _, _) = yard.pick(lay["pos"][1])
        self.assertEqual(end, pick)                                                    # it stops where Classify's inserter takes it
        self.assertIn("<animateTransform", svg)                                        # and the arms move
        self.assertIn(">#208<", svg)
        self.assertIn(">#211<", svg)                                                   # waiting for a person: they queue at the door
        self.assertIn("fn-crate stuck", svg)

    def test_a_fix_round_rides_the_whole_loop_back_to_build(self):
        svg = floor_x({"fix": ["#187"]}, {"build": ("run", ["#187"])})
        (d, end), = trips(svg)
        self.assertGreaterEqual(d.count(" A 60 60 "), 4)                               # round all four corners
        build = yard.pick(yard.layout(10)["pos"][6])[0]
        self.assertEqual(end, build)

    def test_skipping_the_stages_takes_the_bypass_and_queued_tickets_go_to_the_chest(self):
        svg = floor_x({"bypass": ["#199"], "queued": ["#216", "#217"]}, {"build": ("run", ["#199"])})
        self.assertIn("Splitter: stage work one way", svg)
        self.assertIn("Merger: the bypass joins the loop into Build", svg)
        self.assertIn("QUEUE · 2", svg)
        ends = [e for _, e in trips(svg)]
        self.assertEqual(len(ends), 2)                                                 # one to Build by the bypass, one to the queue
        self.assertIn("A 15 15", trips(svg)[0][0])

    def test_undergrounds_face_each_other_and_splitters_turn(self):
        svg = floor_x({}, {})
        arrows = re.findall(r'class="fn-arrow" d="([^"]+)"', svg)
        self.assertIn(yard.ARROW["e"], arrows)
        self.assertIn(yard.ARROW["w"], arrows)
        self.assertEqual(arrows.count(yard.ARROW["e"]), arrows.count(yard.ARROW["w"]))   # in pairs, mouths facing
        self.assertIn('class="fn-ghost"', svg)                                         # the belt shown running underneath
        self.assertIn("fn-cog", svg)
        self.assertIn('class="fn-wall"', svg)                                          # GitHub's belt goes under the compound wall
        self.assertIn('class="fn-pipe"', svg)                                          # the loop goes under the power plant's pipe


class Yard(unittest.TestCase):
    def test_one_train_per_busy_worker_parked_when_idle_none_when_offline(self):
        ws = [worker("mac", job={"issue": 7, "recipe": "web"}), worker("idle"), worker("gone", online=False)]
        _, svg = floor(workers=ws)
        moving = svg.count('<g class="fn-car"><')
        self.assertEqual(moving, yard.CARS[0])                                         # the busy one drives, every car on its own
        self.assertEqual(svg.count('<g class="fn-car" transform='), yard.CARS[1])      # the idle one waits at its platform
        self.assertEqual(svg.count('<a class="fm-w'), 3)
        self.assertIn('class="fm-w off"', svg)
        self.assertIn("Verify stop", svg)
        self.assertEqual(svg.count("Signal at platform"), 3)
        self.assertEqual(svg.count("Signal before the trunk"), 3)

    def test_no_workers_offers_to_connect_one_and_extra_workers_are_counted(self):
        _, svg = floor()
        self.assertIn("+ Connect a worker", svg)
        self.assertNotIn("fn-car", svg)
        _, svg = floor({**ROLES, "workers": False})
        self.assertNotIn("Connect a worker", svg)
        _, svg = floor(workers=[worker(f"w{i}") for i in range(7)])
        self.assertEqual(svg.count('<a class="fm-w'), yard.MAX_WORKERS)
        self.assertIn("and 2 more workers", svg)

    def test_trains_only_drive_forwards_and_never_share_the_trunk(self):
        g = yard.yard_geometry(4, 1000)
        routes = [yard.train_route(g, k) for k in range(4)]
        T, plan = yard.timetable(routes, [True] * 4)
        windows = []
        for p, r in zip(routes, plan):
            self.assertTrue(r["depart"] < r["arrive"] < r["leave"] < r["at_signal"] <= r["go"] < r["home"] <= T)
            windows.append((r["depart"], r["depart"] + (r["arrive"] - r["depart"]) * yard._ease_time(p.marks["peel"] / p.marks["stop"])))
            windows.append((r["go"], r["go"] + (r["home"] - r["go"]) * yard._ease_time((p.marks["junction"] + 45 - p.marks["signal"]) / (p.len - p.marks["signal"]))))
        windows.sort()
        for (a0, a1), (b0, b1) in zip(windows, windows[1:]):
            self.assertLessEqual(a1, b0 + 1e-6)                                        # one train on the shared track at a time
        svg = yard._train(routes[0], [(0, 0), (plan[0]["depart"], 0), (plan[0]["arrive"], routes[0].marks["stop"]), (T, routes[0].len)], T, "0s", 3)
        for kp in re.findall(r'keyPoints="([^"]+)"', svg):
            pts = [float(v) for v in kp.split(";")]
            self.assertEqual(pts, sorted(pts))                                         # every car always forwards

    def test_the_cycle_continues_across_refreshes(self):
        ws = [worker("mac", job={"issue": 7, "recipe": "web"})]
        _, a = floor(workers=ws, now=1000.0)
        _, b = floor(workers=ws, now=1005.0)
        begins = lambda s: re.findall(r'begin="(-?[\d.]+)s"', s)
        self.assertNotEqual(begins(a), begins(b))                                      # started at the clock's phase, not from zero
        self.assertTrue(all(float(x) <= 0 for x in begins(a)))


class WorkersSeen(unittest.TestCase):
    def test_online_offline_and_the_job_each_holds(self):
        import sqlite3
        from factory import jobs
        from factory.ui.server import workers_seen
        db = sqlite3.connect(":memory:")
        jobs.ensure_tables(db)
        now = time.time()
        jobs.touch_worker(db, "mac", "macos", ["web"], 1, now - 5)
        jobs.touch_worker(db, "old", "linux", ["web"], 1, now - 3600)
        db.execute("INSERT INTO verify_jobs (repo, issue, base_sha, patch, recipe, status, worker, created, claimed) VALUES (?,?,?,?,?,?,?,?,?)",
                   ("o/r", 42, "abc", "", "web", "claimed", "mac", now, now))
        self.assertEqual(workers_seen(db, now), [{"name": "mac", "online": True, "job": {"issue": 42, "recipe": "web"}},
                                                 {"name": "old", "online": False, "job": None}])


class Safety(unittest.TestCase):
    def test_no_inline_styles_and_hostile_names_are_escaped(self):
        evil = '<img src=x onerror=alert(1)>'
        _, svg = floor(workers=[worker(evil, job={"issue": 3, "recipe": evil})], states={"build": ("run", [evil])})
        self.assertNotIn("<img", svg)
        self.assertNotIn("style=", svg)

    def test_every_map_class_is_styled(self):
        from pathlib import Path
        css = (Path(__file__).resolve().parent.parent / "factory" / "ui" / "static" / "style.css").read_text()
        defined = set(re.findall(r"\.([a-zA-Z][\w-]*)", re.sub(r"\{[^}]*\}", "", css)))
        _, svg = floor(workers=[worker("a", job={"issue": 1, "recipe": "web"}), worker("b", online=False)],
                       states={"build": ("run", ["#1"]), "review": ("fail", ["#2"]), "analyst": ("wait", ["#3"])})
        used = {c for attr in re.findall(r'class="([^"]+)"', svg) for c in attr.split()}
        generic = {"run", "wait", "fail", "done", "none", "ok", "dim", "off", "thin", "stuck", "in", "out", "r", "g", "on", "idle", "hold",
                   "fault", "bus", "a", "b", "c", "ccw", "blink", "p1", "s0", "s1", "s2", "c0", "c1", "c2", "c3", "c4"}
        self.assertEqual(sorted(c for c in used - generic if c not in defined), [])


if __name__ == "__main__":
    unittest.main()
