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


class Stations(unittest.TestCase):
    def test_the_stations_follow_the_config(self):
        ids = lambda cfg: [sid for sid, _, _ in board.floor_order(cfg)]
        self.assertEqual(ids(ROLES), ["poll", "classify", "route", "analyst", "designer", "architect", "build", "review", "ci", "pr"])
        self.assertEqual(ids({**ROLES, "review": False, "ci": "off"}), ["poll", "classify", "route", "analyst", "designer", "architect", "build", "pr"])
        custom = board.floor_order({**ROLES, "roles": ["analyst", "security_review"]})
        self.assertIn(("security_review", "Security Review", yard.GENERIC_ICON), custom)

    def test_every_station_is_a_link_and_the_snake_wraps_every_five(self):
        order, svg = floor({**ROLES, "roles": ["analyst", "designer", "architect", "docs", "security"]})
        self.assertEqual(svg.count('<a class="fm-m '), len(order))
        self.assertIn('href="/tickets?at=docs"', svg)
        spots = yard.place(12)
        self.assertEqual([round(y) for _, y in spots[::5]], [24, 268, 512])            # three rows
        self.assertLess(spots[5][0], spots[4][0] + 1)                                  # the second row runs right to left
        self.assertEqual(spots[4][0], spots[5][0])

    def test_belts_carry_crates_into_running_stations_and_back_up_where_a_person_is_needed(self):
        _, svg = floor(states={"build": ("run", ["#199"]), "analyst": ("wait", ["#208", "#211"]), "review": ("fail", ["#190"])})
        self.assertEqual(svg.count('class="fm-ride"'), 2)                              # two crates riding into Build
        self.assertIn(">#208<", svg)
        self.assertIn(">#211<", svg)                                                   # backed up at the analyst
        self.assertIn("fm-crate stuck", svg)
        self.assertIn('fm-flow run', svg)


class Yard(unittest.TestCase):
    def test_one_train_per_busy_worker_parked_when_idle_none_when_offline(self):
        ws = [worker("mac", job={"issue": 7, "recipe": "web"}), worker("idle"), worker("gone", online=False)]
        _, svg = floor(workers=ws)
        self.assertEqual(svg.count('<g class="fm-train">'), 1)                         # the busy one drives
        self.assertEqual(svg.count('<g class="fm-train" transform='), 1)              # the idle one waits at its platform
        self.assertEqual(svg.count('<a class="fm-w'), 3)
        self.assertIn('class="fm-w off"', svg)
        self.assertIn("Verify stop", svg)
        self.assertEqual(svg.count("Signal at platform"), 3)
        self.assertEqual(svg.count("Signal before the trunk"), 3)

    def test_no_workers_offers_to_connect_one_and_extra_workers_are_counted(self):
        _, svg = floor()
        self.assertIn("+ Connect a worker", svg)
        self.assertNotIn("fm-train", svg)
        _, svg = floor({**ROLES, "workers": False})
        self.assertNotIn("Connect a worker", svg)
        _, svg = floor(workers=[worker(f"w{i}") for i in range(7)])
        self.assertEqual(svg.count('<a class="fm-w'), yard.MAX_WORKERS)
        self.assertIn("and 2 more workers", svg)

    def test_trains_only_drive_forwards_and_never_share_the_trunk(self):
        g = yard._yard_geometry(4, 600)
        routes = [yard._route(g, k) for k in range(4)]
        T, plan = yard.timetable(routes, [True] * 4)
        windows = []
        for p, r in zip(routes, plan):
            self.assertTrue(r["depart"] < r["arrive"] < r["leave"] < r["at_signal"] <= r["go"] < r["home"] <= T)
            windows.append((r["depart"], r["depart"] + (r["arrive"] - r["depart"]) * yard._ease_time(p.marks["peel"] / p.marks["stop"])))
            windows.append((r["go"], r["go"] + (r["home"] - r["go"]) * yard._ease_time((p.marks["junction"] + 45 - p.marks["signal"]) / (p.len - p.marks["signal"]))))
            kt, kp, _ = yard._keys([(0, 0), (r["depart"], 0), (r["arrive"], p.marks["stop"]), (r["leave"], p.marks["stop"]),
                                    (r["at_signal"], p.marks["signal"]), (r["go"], p.marks["signal"]), (r["home"], p.len), (T, p.len)], T, p.len)
            points = [float(v) for v in kp.split(";")]
            self.assertEqual(points, sorted(points))                                   # always forwards along the loop
            self.assertEqual(len(kt.split(";")), len(points))
        windows.sort()
        for (a0, a1), (b0, b1) in zip(windows, windows[1:]):
            self.assertLessEqual(a1, b0 + 1e-6)                                        # one train on the shared track at a time

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
        generic = {"run", "wait", "fail", "done", "none", "ok", "dim", "off", "thin", "stuck", "in", "out", "r", "g"}
        self.assertEqual(sorted(c for c in used - generic if c not in defined), [])


if __name__ == "__main__":
    unittest.main()
