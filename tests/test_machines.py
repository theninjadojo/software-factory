import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from factory import jobs, machines
from factory.config import HealthCfg
from factory.ui import floorplan as F, machinepages as MP, plant
from test_floorplan import CTX, Drawn
from test_health import cfg_in

GB = 1024 ** 3


class Stats(unittest.TestCase):
    def test_a_workers_report_is_cleaned(self):
        self.assertEqual(machines.clean_stats({"disk_free": 5, "disk_total": 10, "load": 1.5, "evil": 9, "mem_avail": "x", "cpus": True}),
                         {"disk_free": 5, "disk_total": 10, "load": 1.5})
        self.assertIsNone(machines.clean_stats({"disk_free": -1}))
        self.assertIsNone(machines.clean_stats("nope"))

    def test_a_worker_is_rated_by_the_health_thresholds(self):
        hc = HealthCfg()
        self.assertEqual(machines.worker_level({"disk_free": 100 * GB, "disk_total": 200 * GB}, hc), "ok")
        self.assertEqual(machines.worker_level({"disk_free": 20 * GB, "disk_total": 228 * GB}, hc), "warn")
        self.assertEqual(machines.worker_level({"disk_free": 1 * GB, "disk_total": 228 * GB, "mem_avail": 10 ** 12}, hc), "crit")
        self.assertEqual(machines.worker_level(None, hc), "ok")

    def test_the_server_reads_its_disks(self):
        s = machines.server_stats(cfg_in(tempfile.mkdtemp()))
        self.assertTrue(s["disks"])
        self.assertIn(s["level"], ("ok", "warn", "crit"))


class Reports(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        jobs.ensure_tables(self.db)

    def test_the_last_report_is_kept_when_a_poll_has_none(self):
        jobs.touch_worker(self.db, "mac", "macos", ["web"], 1, 1.0, stats={"disk_free": 5, "disk_total": 10})
        jobs.touch_worker(self.db, "mac", "macos", ["web"], 1, 2.0)
        row = jobs.online_workers(self.db, 0, 1e12)[0]
        self.assertEqual(json.loads(row["stats"]), {"disk_free": 5, "disk_total": 10})
        jobs.touch_worker(self.db, "mac", "macos", ["web"], 1, 3.0, stats={"disk_free": 4, "disk_total": 10})
        self.assertEqual(json.loads(jobs.online_workers(self.db, 0, 1e12)[0]["stats"])["disk_free"], 4)

    def test_the_worker_page_shows_the_disk_and_an_unknown_worker_is_none(self):
        cfg = cfg_in(tempfile.mkdtemp())
        jobs.touch_worker(self.db, "mac", "macos", ["web"], 1, 1000.0, stats={"disk_free": 20 * GB, "disk_total": 228 * GB, "mem_avail": 5 * GB, "load": 3.2, "cpus": 8})
        html = MP.worker_page(cfg, self.db, "mac", 1010.0)
        self.assertIn("Work disk free", html)
        self.assertIn("Low", html)
        self.assertIn("20.0 GB of 228.0 GB", html)
        self.assertIsNone(MP.worker_page(cfg, self.db, "nope", 1010.0))

    def test_a_worker_that_never_reported_says_so(self):
        jobs.touch_worker(self.db, "old", "linux", ["web"], 1, 1000.0)
        self.assertIn("has not reported", MP.worker_page(cfg_in(tempfile.mkdtemp()), self.db, "old", 1010.0))

    def test_the_server_page_lists_disks_and_the_workers(self):
        jobs.touch_worker(self.db, "mac", "macos", ["web"], 1, 1000.0, stats={"disk_free": 10 * GB, "disk_total": 228 * GB})
        html = MP.server_page(cfg_in(tempfile.mkdtemp()), self.db, 1010.0)
        self.assertIn("<h2>Disks</h2>", html)
        self.assertIn('href="/workers/machine?name=mac"', html)
        self.assertIn("has not run yet", html)


class OnTheFloor(unittest.TestCase):
    def test_the_server_is_a_building_without_a_belt(self):
        d = F.default_plan(CTX)
        self.assertIn("server", d["nodes"])
        self.assertFalse(any("server" in a + b for a, b in CTX.hops()))
        self.assertEqual(F.validate(d, CTX), [])

    def test_an_older_layout_gains_the_server(self):
        d = json.loads(json.dumps(F.default_plan(CTX)))
        del d["nodes"]["server"]
        m = F.merge(d, CTX)
        self.assertIn("server", m["nodes"])
        self.assertEqual(F.validate(m, CTX), [])

    def test_the_floor_draws_it_with_its_rating_and_workers_with_a_disk_bar(self):
        svg = Drawn().draw(F.default_plan(CTX), extras={"server": {"level": "warn", "free": 38 * GB, "total": 250 * GB}})
        self.assertIn('class="sv warn" href="/machines/server"', svg)
        self.assertIn("38 GB", svg)
        self.assertNotIn("style=", svg)
        w = {"name": "mac mini", "online": True, "job": None, "level": "crit", "stats": {"disk_free": 14 * GB, "disk_total": 228 * GB}}
        stop = plant.worker_stop(0, 0, w)
        self.assertIn('href="/workers/machine?name=mac%20mini"', stop)
        self.assertIn("fm-w crit", stop)
        self.assertIn("fm-dku", stop)
        self.assertIn("disk 14 GB free", stop)
        self.assertNotIn("fm-dku", plant.worker_stop(0, 0, {"name": "x", "online": True}))


if __name__ == "__main__":
    unittest.main()
