"""Runs the worker API (factory/workerapi.py) as a child process of the UI while [workers] enabled = true, so turning workers on in
Settings is all it takes: no separate service to install or start. It stays its own process (sharing only the SQLite file, as before) and
listens where workers.listen says (loopback by default). If something already answers on that address (an older factory-workers.service),
it is left alone."""
import logging
import os
import subprocess
import sys
import threading
import time

from .workers import api_up

log = logging.getLogger("factory.ui")
POLL_SECONDS = 3
RETRY_SECONDS = 10      # after a start, wait this long before starting it again if it died


class WorkerApiProcess:
    def __init__(self, config_path: str, listen: str | None = None, popen=subprocess.Popen, clock=time.monotonic, probe=api_up):
        self.config_path, self.listen = config_path, listen      # listen: overrides workers.listen (in Docker: 0.0.0.0:8788 in the container)
        self.popen, self.clock, self.probe = popen, clock, probe
        self.proc, self.args, self.next_try = None, None, 0.0
        self.lock, self.done = threading.Lock(), threading.Event()

    def command(self, cfg) -> list[str]:
        return [sys.executable, "-m", "factory.workerapi", "--config", self.config_path, "--listen", self.listen or cfg.workers.listen,
                "--parent-pid", str(os.getpid())]

    def sync(self, cfg) -> None:
        """Start, stop or restart the child so it matches the config. Called every few seconds with the current config."""
        with self.lock:
            if self.done.is_set():
                return
            if self.proc and self.proc.poll() is not None:
                log.warning("worker API exited with code %s", self.proc.returncode)
                self.proc = None
            want = self.command(cfg) if cfg.workers.enabled else None
            if self.proc and want != self.args:
                self._stop()
            if want and not self.proc and self.clock() >= self.next_try and not self.probe(cfg.workers.listen):
                log.info("starting the worker API on %s", want[want.index("--listen") + 1])
                self.proc, self.args, self.next_try = self.popen(want), want, self.clock() + RETRY_SECONDS

    def run(self, load_cfg) -> None:
        while not self.done.is_set():
            try:
                self.sync(load_cfg())
            except Exception:
                log.exception("worker API supervisor")
            self.done.wait(POLL_SECONDS)

    def stop(self) -> None:
        with self.lock:
            self.done.set()
            self._stop()

    def _stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            log.info("stopping the worker API")
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.proc, self.args, self.next_try = None, None, 0.0     # a deliberate stop may start again at once; only a crash waits
