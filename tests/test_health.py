import tempfile
import time
import unittest
from pathlib import Path

from factory import db as dbm, health
from factory.config import HealthCfg, load
from factory.health import GB, Result, decide, disk_level

orig_checks = health.CHECKS
HC = HealthCfg()
ROUTES = "".join(f'[routing.{t}]\nharness = "claude-code"\nmodel = "m"\neffort = "{t}"\n' for t in ("low", "medium", "high"))
GITHUB = '[github]\nrepos = []\ntrigger_label = "factory:ready"\ntrusted_permissions = ["write"]\n'
BASE = "[general]\n{general}poll_seconds = 60\ndry_run = true\nconfidence_threshold = 0.6\n" + GITHUB + ROUTES


def cfg_in(d: str):
    p = Path(d) / "config.toml"
    p.write_text(BASE.format(general=f'db_path = "{d}/factory.db"\n')
                 + f'[runner]\nwork_dir = "{d}/work"\nproxy_socket = "{d}/p.sock"\nclaude_env_file = "{d}/claude.env"\n')
    return load(str(p))



class Levels(unittest.TestCase):
    def test_disk_thresholds(self):
        self.assertEqual(disk_level(50 * GB, 100 * GB, HC), "ok")
        self.assertEqual(disk_level(10 * GB, 100 * GB, HC), "warn")     # under 15 %
        self.assertEqual(disk_level(4 * GB, 20 * GB, HC), "warn")       # 20 % free, but under 5 GB
        self.assertEqual(disk_level(3 * GB, 100 * GB, HC), "crit")      # under 5 %
        self.assertEqual(disk_level(1 * GB, 1000 * GB, HC), "crit")     # under 2 GB
        self.assertEqual(disk_level(0, 0, HC), "crit")                  # a filesystem that reports no size is not healthy


class Decide(unittest.TestCase):
    def test_alert_repeat_and_recovery(self):
        bad, good = [Result("disk:/", "crit", "1 GB free")], [Result("disk:/", "ok", "50 GB free")]
        m, st = decide({}, good, 0, 3600)
        self.assertEqual(m, [])                                          # healthy and unknown: silence
        m, st = decide(st, bad, 100, 3600)
        self.assertEqual(len(m), 1)
        self.assertIn("disk:/", m[0])
        m, st = decide(st, bad, 200, 3600)
        self.assertEqual(m, [])                                          # not repeated before repeat_seconds
        m, st = decide(st, bad, 4000, 3600)
        self.assertEqual(len(m), 1)                                      # reminder
        m, st = decide(st, good, 4100, 3600)
        self.assertEqual(len(m), 1)
        self.assertIn("Recovered", m[0])
        m, st = decide(st, good, 4200, 3600)
        self.assertEqual(m, [])

    def test_escalation_alerts_immediately(self):
        _, st = decide({}, [Result("x", "warn", "low")], 0, 3600)
        m, st = decide(st, [Result("x", "crit", "very low")], 10, 3600)
        self.assertEqual(len(m), 1)

    def test_recovery_without_prior_alert_is_silent(self):
        m, _ = decide({"x": {"level": "warn", "since": 0, "alerted": 0}}, [Result("x", "ok", "")], 10, 3600)
        self.assertEqual(m, [])


class Run(unittest.TestCase):
    def test_stale_orchestrator_and_state_files(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_in(d)
            db = dbm.connect(cfg.db_path)
            dbm.set_status(db, "last_poll_ok", str(time.time() - 3600))
            dbm.set_status(db, "last_error", "boom")
            db.close()
            sent = []
            health.CHECKS = tuple(c for c in health.CHECKS if c is not health.check_engine)    # tests never run a container engine
            self.addCleanup(setattr, health, "CHECKS", orig_checks)
            results = health.run(cfg, send=sent.append)
            by = {r.name: r for r in results}
            self.assertEqual(by["orchestrator"].level, "crit")
            self.assertIn("boom", by["orchestrator"].detail)
            self.assertEqual(by["proxy"].level, "crit")                  # nothing listens on the socket
            self.assertEqual(by["secrets"].level, "crit")                # claude.env does not exist
            self.assertEqual(len(sent), 1)
            self.assertIn("orchestrator", sent[0])
            self.assertTrue((Path(d) / "health.json").exists())
            health.run(cfg, send=sent.append)
            self.assertEqual(len(sent), 1)                               # same problems: not repeated at once

    def test_fresh_heartbeat_is_ok_and_failure_streak_warns(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = cfg_in(d)
            db = dbm.connect(cfg.db_path)
            dbm.set_status(db, "last_poll_ok", str(time.time()))
            for i in range(5):
                dbm.finish_run(db, dbm.start_run(db, "build", "o/r", i, "t", "claude-code", "sonnet", "low", ""), "failed", "x")
            db.close()
            by = {r.name: r for r in health.check_orchestrator(cfg, cfg.health)}
            self.assertEqual(by["orchestrator"].level, "ok")
            self.assertEqual(by["run-failures"].level, "warn")

    def test_crashing_check_is_reported_not_raised(self):
        def boom(cfg, hc):
            raise RuntimeError("x")
        orig = health.CHECKS
        health.CHECKS = (boom,)
        try:
            out = health.run_checks(cfg_in(tempfile.mkdtemp()))
        finally:
            health.CHECKS = orig
        self.assertEqual(out[0].level, "warn")

    def test_config_validation(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.toml"
            p.write_text(BASE.format(general="db_path = \"x\"\n") + "[health]\ndisk_warn_percent = 3\ndisk_crit_percent = 5\n")
            with self.assertRaises(ValueError):
                load(str(p))
            p.write_text(BASE.format(general="db_path = \"x\"\n") + '[health]\nheartbeat_url = "http://x"\n')
            with self.assertRaises(ValueError):
                load(str(p))


if __name__ == "__main__":
    unittest.main()
