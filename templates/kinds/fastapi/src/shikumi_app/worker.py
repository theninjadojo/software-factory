"""The job worker: python -m shikumi_app.worker

Each job runs in the transaction that claimed it: its writes and the "done" mark are saved together. If it fails, its
writes are undone and it is tried again later (see domain.jobs.backoff). A job can run more than once (a worker can die
after the work but before the commit), so handlers must be idempotent.
"""

import logging
import signal
import threading
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from shikumi_app.config import get_settings
from shikumi_app.db import make_engine, make_sessionmaker
from shikumi_app.logs import configure_logging, request_id
from shikumi_app.repositories.items import SqlItemRepository
from shikumi_app.repositories.jobs import SqlJobQueue
from shikumi_app.services.items import PROCESS_ITEM, ItemService

log = logging.getLogger("shikumi_app.worker")

Handler = Callable[[Session, dict[str, Any]], None]


def process_item(session: Session, payload: dict[str, Any]) -> None:
    ItemService(SqlItemRepository(session), SqlJobQueue(session)).process(int(payload["item_id"]))


HANDLERS: Mapping[str, Handler] = {PROCESS_ITEM: process_item}


def run_once(sessions: sessionmaker[Session], handlers: Mapping[str, Handler] = HANDLERS) -> bool:
    """Run the next due job, if there is one. True when a job was run (whether or not it succeeded)."""
    with sessions() as session:
        queue = SqlJobQueue(session)
        job = queue.claim()
        if job is None:
            return False
        token = request_id.set(f"job-{job.id}-{uuid.uuid4().hex[:8]}")
        try:
            with session.begin_nested():  # a savepoint: a failing handler's writes are undone alone
                handlers[job.kind](session, job.payload)
            queue.finish(job)
            log.info("job done", extra={"job_id": job.id, "kind": job.kind})
        except Exception as e:
            log.exception("job failed", extra={"job_id": job.id, "kind": job.kind})
            queue.retry_or_fail(job, f"{type(e).__name__}: {e}")
        finally:
            request_id.reset(token)
        session.commit()
        return True


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    sessions = make_sessionmaker(make_engine(settings.database_url))
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    log.info("worker started")
    while not stop.is_set():
        if not run_once(sessions):
            stop.wait(settings.worker_poll_seconds)
    log.info("worker stopped")


if __name__ == "__main__":
    main()
