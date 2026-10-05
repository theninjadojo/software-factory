"""The worker pool: up to [runner] max_parallel jobs (agent runs and what follows them) at once, at most one per ticket.

Only the poll thread submits. It asks `can_take(key)` before doing any work for a ticket and leaves the ticket queued (its
trigger label stays, nothing is recorded) when the answer is no, so the queue is GitHub's labels and survives a restart.
The key is the ticket, (repo, number): a build, its review, a CI fix and a conflict resolution of one ticket never overlap.

A running job is stopped only by cancel(key): it sets an event the job's thread reads through current_cancel() (the agent
container is killed and nothing is published).

Pool(n) without `threaded` runs each job inline, in the caller's thread, which is what tests and `--once` use."""
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor

log = logging.getLogger("factory.pool")
_local = threading.local()


def current_cancel() -> threading.Event | None:
    """The cancel event of the job running in this thread, or None outside a pool job."""
    return getattr(_local, "cancel", None)


def key(repo: str, num: int) -> tuple[str, int]:
    return repo.lower(), int(num)


class Pool:
    def __init__(self, max_parallel: int = 1, threaded: bool = False):
        self.max = max_parallel
        self._ex = ThreadPoolExecutor(max_workers=max_parallel, thread_name_prefix="job") if threaded else None
        self._lock = threading.Lock()
        self._inflight: dict[tuple[str, int], dict] = {}
        self.draining = False

    @property
    def inline(self) -> bool:
        return self._ex is None

    def busy(self, k) -> bool:
        with self._lock:
            return k in self._inflight

    def can_take(self, k) -> bool:
        with self._lock:
            return not self.draining and k not in self._inflight and len(self._inflight) < self.max

    def idle(self) -> bool:
        with self._lock:
            return not self._inflight

    def running(self) -> list[dict]:
        with self._lock:
            return [{a: b for a, b in v.items() if a != "cancel"} for v in self._inflight.values()]

    def submit(self, k, kind: str, fn) -> None:
        """Run fn() for ticket k. Raises if the ticket is in flight or no slot is free: the caller checks can_take first."""
        with self._lock:
            if self.draining or k in self._inflight or len(self._inflight) >= self.max:
                raise RuntimeError(f"no free slot for {k}")
            self._inflight[k] = {"repo": k[0], "issue": k[1], "kind": kind, "started": time.time(), "cancel": threading.Event()}
        if self._ex is None:
            self._run(k, fn)
        else:
            try:
                self._ex.submit(self._run, k, fn)
            except Exception:
                self._release(k)
                raise

    def cancel(self, k) -> bool:
        """Ask the job for ticket k to stop. False when none is running."""
        with self._lock:
            job = self._inflight.get(k)
        if job is None:
            return False
        job["cancel"].set()
        return True

    def _run(self, k, fn) -> None:
        with self._lock:
            job = self._inflight.get(k)
        _local.cancel = job["cancel"] if job else None
        try:
            fn()
        except Exception:
            log.exception("job for %s#%s failed", *k)       # never re-raised: a job's crash must not stop the pool
        finally:
            _local.cancel = None
            self._release(k)

    def _release(self, k) -> None:
        with self._lock:
            self._inflight.pop(k, None)

    def drain(self, on: bool = True) -> None:
        """Stop taking new jobs (before a restart); the running ones finish. drain(False) takes jobs again."""
        with self._lock:
            self.draining = on

    def join(self) -> None:
        if self._ex is not None:
            self._ex.shutdown(wait=True)
