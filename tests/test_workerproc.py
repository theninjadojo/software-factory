import socket
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from factory import workerapi
from factory.config import WorkersCfg
from factory.ui.workerproc import RETRY_SECONDS, WorkerApiProcess
from factory.ui.workers import api_up
from test_workerapi import cfg_for


class FakeProc:
    def __init__(self, args):
        self.args, self.returncode, self.terminated = args, None, False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated, self.returncode = True, -15

    def wait(self, timeout=None):
        return self.returncode


class Supervise(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.cfg = cfg_for(self.tmp)
        self.now, self.up, self.started = 0.0, False, []
        self.sup = WorkerApiProcess(str(self.tmp / "config.toml"), popen=self.popen, clock=lambda: self.now, probe=lambda listen: self.up)

    def popen(self, args):
        self.started.append(FakeProc(args))
        return self.started[-1]

    def test_starts_when_enabled_with_the_configured_address(self):
        self.sup.sync(self.cfg)
        self.assertEqual(len(self.started), 1)
        args = self.started[0].args
        self.assertEqual(args[1:5], ["-m", "factory.workerapi", "--config", str(self.tmp / "config.toml")])
        self.assertEqual(args[args.index("--listen") + 1], "127.0.0.1:8788")
        self.assertIn("--parent-pid", args)
        self.sup.sync(self.cfg)
        self.assertEqual(len(self.started), 1)                       # already running: nothing new

    def test_off_by_default_and_stops_when_turned_off(self):
        off = replace(self.cfg, workers=WorkersCfg())
        self.sup.sync(off)
        self.assertEqual(self.started, [])
        self.sup.sync(self.cfg)
        self.sup.sync(off)
        self.assertTrue(self.started[0].terminated)
        self.assertIsNone(self.sup.proc)

    def test_a_new_address_restarts_it_and_the_override_wins(self):
        self.sup.sync(self.cfg)
        self.sup.sync(replace(self.cfg, workers=replace(self.cfg.workers, listen="127.0.0.1:9999")))
        self.assertTrue(self.started[0].terminated)
        self.assertIn("127.0.0.1:9999", self.started[1].args)
        docker = WorkerApiProcess("c.toml", "0.0.0.0:8788", popen=self.popen, probe=lambda listen: False)
        docker.sync(self.cfg)
        self.assertIn("0.0.0.0:8788", self.started[-1].args)

    def test_leaves_an_address_something_else_already_serves(self):
        self.up = True
        self.sup.sync(self.cfg)
        self.assertEqual(self.started, [])

    def test_restarts_a_crashed_child_but_not_in_a_tight_loop(self):
        self.sup.sync(self.cfg)
        self.started[0].returncode = 1
        self.sup.sync(self.cfg)
        self.assertEqual(len(self.started), 1)                       # too soon after the last start
        self.now += RETRY_SECONDS
        self.sup.sync(self.cfg)
        self.assertEqual(len(self.started), 2)

    def test_nothing_starts_after_stop(self):
        self.sup.sync(self.cfg)
        self.sup.stop()
        self.assertTrue(self.started[0].terminated)
        self.sup.sync(self.cfg)
        self.assertEqual(len(self.started), 1)


class ExitWithParent(unittest.TestCase):
    def test_exits_once_the_parent_changes(self):
        with mock.patch("os.getppid", side_effect=[42, 42, 1]), mock.patch("time.sleep"), mock.patch("os._exit", side_effect=SystemExit) as ex:
            with self.assertRaises(SystemExit):
                workerapi.exit_with_parent(42)
        ex.assert_called_once_with(0)


class RealChild(unittest.TestCase):
    """The real factory.workerapi as a child process: it comes up on the configured address and goes away on stop."""
    def test_starts_and_stops(self):
        tmp = Path(tempfile.mkdtemp())
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        cfg = cfg_for(tmp)
        cfg = replace(cfg, workers=replace(cfg.workers, listen=f"127.0.0.1:{port}"))
        sup = WorkerApiProcess(str(tmp / "config.toml"))
        self.addCleanup(sup.stop)
        sup.sync(cfg)
        deadline = time.time() + 15
        while not api_up(cfg.workers.listen) and time.time() < deadline:
            self.assertIsNone(sup.proc.poll(), "the worker API exited")
            time.sleep(0.1)
        self.assertTrue(api_up(cfg.workers.listen))
        sup.stop()
        self.assertFalse(api_up(cfg.workers.listen))


if __name__ == "__main__":
    unittest.main()
