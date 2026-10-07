"""A job queue in PostgreSQL. Workers claim jobs with SELECT ... FOR UPDATE SKIP LOCKED, so many can run at once."""

from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from shikumi_app.domain.jobs import Job, JobStatus, backoff
from shikumi_app.repositories.tables import JobRow


class SqlJobQueue:
    def __init__(self, session: Session) -> None:
        self.session = session

    def enqueue(self, kind: str, payload: dict[str, Any], dedupe_key: str | None = None) -> None:
        stmt = insert(JobRow).values(kind=kind, payload=payload, dedupe_key=dedupe_key)
        self.session.execute(stmt.on_conflict_do_nothing(index_elements=["dedupe_key"]))

    def claim(self) -> Job | None:
        """The next due job, locked until this transaction ends. Others skip it rather than wait."""
        row = self.session.scalars(
            select(JobRow)
            .where(JobRow.status == JobStatus.QUEUED, JobRow.run_at <= func.now())
            .order_by(JobRow.run_at, JobRow.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).first()
        if row is None:
            return None
        return Job(row.id, row.kind, row.payload, row.attempts, row.max_attempts)

    def finish(self, job: Job) -> None:
        self._set(job, status=JobStatus.DONE, last_error=None)

    def retry_or_fail(self, job: Job, error: str) -> None:
        attempts = job.attempts + 1
        if attempts >= job.max_attempts:
            self._set(job, status=JobStatus.FAILED, attempts=attempts, last_error=error)
        else:
            run_at = func.now() + backoff(attempts)
            self._set(job, attempts=attempts, last_error=error, run_at=run_at)

    def _set(self, job: Job, **values: Any) -> None:
        self.session.execute(update(JobRow).where(JobRow.id == job.id).values(**values))
