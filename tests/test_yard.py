import re
import sqlite3
import time
import unittest

from factory.ui import board, plant, yard

ROLES = {"roles": ["analyst", "designer", "architect"], "review": True, "ci": "on", "workers": True,
         "power": [{"name": "claude-code", "uses": ["analyst", "designer", "architect", "build"]}, {"name": "codex", "uses": ["review"]}]}


def floor(cfg=ROLES, workers=(), states=None, now=1000.0, extras=None):
    order = board.floor_order(cfg)
    fl = {sid: {"state": "none", "refs": [], "count": 0} for sid, _, _ in order}
    for sid, (state, refs) in (states or {}).items():
        fl[sid] = {"state": state, "refs": refs, "count": len(refs)}
    ex = {"power": [{**h, "on": False} for h in cfg.get("power", [])], **(extras or {})}
    return order, plant.floor_map(order, fl, list(workers), bool(cfg.get("workers")), now, board._floor_word, lambda sid: f"/tickets?at={sid}", ex)


def worker(name, online=True, job=None):
    return {"name": name, "online": online, "job": job}


def trips(svg):
    """Every crate that rides: (its path, where the path ends)."""
    out = []
    for d in re.findall(r'<g class="fn-crate">(?:(?!</g>).)*?<animateMotion path="([^"]+)"', svg, re.S):
        nums = re.findall(r"-?\d+(?:\.\d+)?", d)
        out.append((d, (float(nums[-2]), float(nums[-1]))))
    return out


class Districts(unittest.TestCase):
    def test_the_stations_follow_the_config(self):
        ids = lambda cfg: [sid for sid, _, _ in board.floor_order(cfg)]
        self.assertEqual(ids(ROLES), ["poll", "classify", "route", "analyst", "designer", "architect", "build", "review", "ci", "pr"])
        self.assertEqual(ids({**ROLES, "review": False, "ci": "off"}), ["poll", "classify", "route", "analyst", "designer", "architect", "build", "pr"])
        custom = board.floor_order({**ROLES, "roles": ["analyst", "security_review"]})
        self.assertIn(("security_review", "Security Review", yard.GENERIC_ICON), custom)

    def test_stations_stand_in_their_districts(self):
        order, svg = floor()
        for name in ("INTAKE", "PLANNING", "PRODUCTION", "QUALITY", "SHIPPING"):
            self.assertIn(f">{name}</text>", svg)
        self.assertEqual(svg.count('<a class="fm-m '), len(order))
        lay = plant.layout([sid for sid, _, _ in order])
        self.assertEqual({lay["pos"][s][1] for s in ("analyst", "designer", "architect")}, {plant.TOP})
        self.assertEqual(lay["pos"]["build"], plant.BUILD)
        self.assertEqual({lay["pos"][s][1] for s in ("review", "ci", "pr")}, {plant.BOT})
        wide = plant.layout(["poll", "classify", "route", "a", "b", "c", "d", "e", "build", "pr"])
        self.assertGreater(wide["width"], lay["width"])                                # more stages, a longer Planning loop
        _, svg = floor({**ROLES, "review": False, "ci": "off"})
        self.assertNotIn(">QUALITY</text>", svg)

    def test_a_crate_is_taken_by_the_station_it_is_for(self):
        order, svg = floor(states={"classify": ("run", ["#214"]), "analyst": ("wait", ["#208", "#211"]), "review": ("fail", ["#190"])})
        (d, end), = trips(svg)
        lay = plant.layout([sid for sid, _, _ in order])
        self.assertEqual(end, plant.pick(lay, "classify")[0])                          # it stops where Classify's inserter takes it
        self.assertIn("<animateTransform", svg)
        self.assertIn(">#208<", svg)
        self.assertIn(">#211<", svg)                                                   # waiting for a person: they queue at the door
        self.assertIn("fn-crate stuck", svg)

    def test_fix_rounds_bypass_and_queue(self):
        _, svg = floor(states={"build": ("run", ["#187"])}, extras={"fix": ["#187"]})
        (d, end), = trips(svg)
        self.assertEqual(end, (plant.BUILD[0] - 30, 380))                              # under the quality line, up into Build's side
        _, svg = floor(states={"build": ("run", ["#199"])}, extras={"bypass": ["#199"], "queued": ["#216", "#217"]})
        self.assertIn("QUEUE · 2", svg)
        ends = [e for _, e in trips(svg)]
        self.assertIn((plant.BUILD[0] + plant.MW / 2 - 28, plant.STREET), ends)        # down the bypass, onto the main street
        self.assertEqual(len(ends), 2)                                                 # and a queued ticket into the chest

    def test_undergrounds_splitters_and_the_steam_pipe(self):
        _, svg = floor()
        arrows = re.findall(r'class="fn-arrow" d="([^"]+)"', svg)
        self.assertEqual(arrows.count(yard.ARROW["e"]), arrows.count(yard.ARROW["w"]))   # in pairs, mouths facing
        self.assertEqual(arrows.count(yard.ARROW["s"]), arrows.count(yard.ARROW["n"]))
        self.assertGreaterEqual(svg.count('class="fn-ug"'), 10)
        self.assertIn('class="fn-ghost"', svg)
        self.assertGreaterEqual(svg.count("fn-split"), 3)
        self.assertIn('class="fn-pipe"', svg)                                          # Claude Code's plant pipes to Planning
        _, svg = floor({**ROLES, "power": [{"name": "codex", "uses": ["analyst", "designer", "architect"]}]})
        self.assertNotIn('class="fn-pipe"', svg)


class Power(unittest.TestCase):
    def test_a_station_per_enabled_agent_lit_while_it_runs(self):
        _, svg = floor(extras={"power": [{"name": "claude-code", "uses": ["build"], "on": True}, {"name": "codex", "uses": ["review"], "on": False}]})
        self.assertEqual(svg.count('<g class="pw on"'), 1)
        self.assertEqual(svg.count('<g class="pw idle"'), 1)
        self.assertNotIn("Gemini", svg)                                                # not set up: not on the floor
        self.assertIn('href="/harnesses"', svg)
        self.assertIn("pw-wire", svg)

    def test_power_uses_and_schedule_trips_from_the_config(self):
        from types import SimpleNamespace as N
        from factory import schedules as sc
        from factory.ui.server import power_uses, schedule_trips
        cfg = N(harnesses={"claude-code": N(enabled=True), "codex": N(enabled=True), "gemini": N(enabled=False)}, runner=None,
                roles=(N(name="analyst", harness="claude-code"), N(name="architect", harness="codex")),
                routes={"medium": N(harness="claude-code")}, review=N(enabled=True, harness="codex"),
                schedules=(N(name="weekly", enabled=True, every="7d", cron=None, source={"type": "umami"}),))
        uses = {h["name"]: h["uses"] for h in power_uses(cfg)}
        self.assertEqual(uses, {"claude-code": ["analyst", "build"], "codex": ["architect", "review"]})   # Gemini is off: no station
        db = sqlite3.connect(":memory:")
        sc.ensure_tables(db)
        now = 1_000_000.0
        sc.set_state(db, "weekly", now - 5 * 86400, "ok")
        (t,) = schedule_trips(cfg, db, now)
        self.assertEqual((t["name"], t["source"], t["due"]), ("weekly", "umami", False))
        self.assertAlmostEqual(t["progress"], 5 / 7, places=3)
        self.assertIn("back in 2d", t["when"])


class Sea(unittest.TestCase):
    def test_a_ship_per_schedule_at_its_share_of_the_trip(self):
        sch = [{"name": "umami-weekly", "source": "umami", "when": "every 7d · back in 2d", "progress": 5 / 7, "due": False},
               {"name": "<b>x</b>", "source": "http", "when": "every 1d · due now", "progress": 1.0, "due": True}]
        _, svg = floor(extras={"schedules": sch})
        self.assertEqual(svg.count('<g class="sh-ship'), 2)
        self.assertIn('keyPoints="0.7143;0.7143"', svg)                               # placed by its progress
        self.assertIn("sh-cargo", svg)                                                 # the due one is being unloaded
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", svg)

    def test_drones_fly_at_poll_time_and_the_747_flies_tickets_you_add(self):
        _, a = floor(extras={"poll": {"every": 60, "last": 990.0}})
        _, b = floor(extras={"poll": {"every": 60, "last": 960.0}})
        self.assertNotEqual(re.findall(r'class="fm-drone" opacity="0">.*?begin="(-?[\d.]+)s"', a, re.S)[:1],
                            re.findall(r'class="fm-drone" opacity="0">.*?begin="(-?[\d.]+)s"', b, re.S)[:1])   # phased to the last poll
        _, svg = floor(extras={"flights": [{"label": "#217", "age": 3.0}, {"label": "#9", "age": 40.0}]})
        self.assertEqual(svg.count('<g class="ap-plane">'), 1)                        # only a recent one is still flying
        self.assertIn(">#217</text>", svg)
        self.assertIn("DEPARTURES", svg)
        self.assertIn("AIRFIELD · CARGO", svg)

    def test_tickets_created_here_are_flown_in(self):
        from factory.ui import labels as L
        with L._flights_lock:
            L._flights[:] = [(100.0, "#5"), (50.0, "#4")]
        self.assertEqual(L.flights(110.0), [{"label": "#5", "age": 10.0}])


class Yard(unittest.TestCase):
    def test_one_train_per_busy_worker_parked_when_idle_none_when_offline(self):
        ws = [worker("mac", job={"issue": 7, "recipe": "web"}), worker("idle"), worker("gone", online=False)]
        _, svg = floor(workers=ws)
        self.assertEqual(svg.count('<g class="fn-car"><'), yard.CARS[0])
        self.assertEqual(svg.count('<g class="fn-car" transform='), yard.CARS[1])
        self.assertEqual(svg.count('<a class="fm-w'), 3)
        self.assertIn('class="fm-w off"', svg)
        self.assertEqual(svg.count("Signal at platform"), 3)

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
        g = yard.yard_geometry(4, 1036)
        routes = [yard.train_route(g, k) for k in range(4)]
        T, plan = yard.timetable(routes, [True] * 4)
        windows = []
        for p, r in zip(routes, plan):
            self.assertTrue(r["depart"] < r["arrive"] < r["leave"] < r["at_signal"] <= r["go"] < r["home"] <= T)
            windows.append((r["depart"], r["depart"] + (r["arrive"] - r["depart"]) * yard._ease_time(p.marks["peel"] / p.marks["stop"])))
            windows.append((r["go"], r["go"] + (r["home"] - r["go"]) * yard._ease_time((p.marks["junction"] + 45 - p.marks["signal"]) / (p.len - p.marks["signal"]))))
        windows.sort()
        for (a0, a1), (b0, b1) in zip(windows, windows[1:]):
            self.assertLessEqual(a1, b0 + 1e-6)

    def test_the_cycle_continues_across_refreshes(self):
        ws = [worker("mac", job={"issue": 7, "recipe": "web"})]
        _, a = floor(workers=ws, now=1000.0)
        _, b = floor(workers=ws, now=1005.0)
        begins = lambda s: re.findall(r'begin="(-?[\d.]+)s"', s)
        self.assertNotEqual(begins(a), begins(b))


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
        _, svg = floor(workers=[worker(evil, job={"issue": 3, "recipe": evil})], states={"build": ("run", [evil])},
                       extras={"schedules": [{"name": evil, "source": evil, "when": evil, "progress": .3, "due": False}],
                               "flights": [{"label": evil, "age": 1.0}]})
        self.assertNotIn("<img", svg)
        self.assertNotIn("style=", svg)

    def test_every_map_class_is_styled(self):
        from pathlib import Path
        css = (Path(__file__).resolve().parent.parent / "factory" / "ui" / "static" / "style.css").read_text()
        defined = set(re.findall(r"\.([a-zA-Z][\w-]*)", re.sub(r"\{[^}]*\}", "", css)))
        _, svg = floor(workers=[worker("a", job={"issue": 1, "recipe": "web"}), worker("b", online=False)],
                       states={"build": ("run", ["#1"]), "review": ("fail", ["#2"]), "analyst": ("wait", ["#3"])},
                       extras={"power": [{"name": n, "uses": [], "on": True} for n in plant.NAMES], "queued": ["#4"],
                               "schedules": [{"name": "s", "source": "http", "when": "w", "progress": 1, "due": True}], "flights": [{"label": "#5", "age": 2}]})
        used = {c for attr in re.findall(r'class="([^"]+)"', svg) for c in attr.split()}
        generic = {"run", "wait", "fail", "done", "none", "ok", "dim", "off", "thin", "stuck", "in", "out", "r", "g", "on", "idle", "hold",
                   "fault", "bus", "a", "b", "c", "ccw", "blink", "p1", "s0", "s1", "s2", "c0", "c1", "c2", "c3", "c4", "l0", "l1", "l2",
                   "g0", "g1", "g2", "w0", "w1", "w2", "due", "v", "big"}
        self.assertEqual(sorted(c for c in used - generic if c not in defined), [])


if __name__ == "__main__":
    unittest.main()
