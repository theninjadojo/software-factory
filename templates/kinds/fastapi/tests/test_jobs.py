from datetime import timedelta
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from shikumi_app.domain.jobs import JobStatus, backoff
from shikumi_app.repositories.jobs import SqlJobQueue
from shikumi_app.repositories.tables import JobRow
from shikumi_app.worker import HANDLERS, run_once


def jobs(sessions: sessionmaker[Session]) -> list[JobRow]:
    with sessions() as s:
        return list(s.scalars(select(JobRow).order_by(JobRow.id)))


def make_due(sessions: sessionmaker[Session]) -> None:
    with sessions.begin() as s:
        s.execute(update(JobRow).values(run_at=JobRow.created_at - timedelta(days=1)))


def test_creating_an_item_queues_a_job_the_worker_runs(client: TestClient, sessions: sessionmaker[Session]) -> None:
    item_id = client.post("/items", json={"name": "Queued"}).json()["id"]
    [job] = jobs(sessions)
    assert (job.kind, job.payload, job.status) == ("process_item", {"item_id": item_id}, "queued")

    assert run_once(sessions) is True
    assert run_once(sessions) is False  # nothing left
    assert jobs(sessions)[0].status == JobStatus.DONE
    assert client.get("/items").json()[0]["processed_at"] is not None


def test_enqueueing_twice_with_a_key_adds_one_job(sessions: sessionmaker[Session]) -> None:
    with sessions.begin() as s:
        SqlJobQueue(s).enqueue("process_item", {"item_id": 1}, dedupe_key="k")
        SqlJobQueue(s).enqueue("process_item", {"item_id": 1}, dedupe_key="k")
    assert len(jobs(sessions)) == 1


def test_the_example_handler_is_idempotent(client: TestClient, sessions: sessionmaker[Session]) -> None:
    item_id = client.post("/items", json={"name": "Twice"}).json()["id"]
    run_once(sessions)
    first = client.get("/items").json()[0]["processed_at"]
    with sessions.begin() as s:
        HANDLERS["process_item"](s, {"item_id": item_id})
    assert client.get("/items").json()[0]["processed_at"] == first


def test_a_failing_job_is_retried_with_backoff_then_given_up(
    sessions: sessionmaker[Session],
) -> None:
    calls: list[Any] = []

    def broken(session: Session, payload: dict[str, Any]) -> None:
        calls.append(payload)
        raise RuntimeError("boom")

    with sessions.begin() as s:
        SqlJobQueue(s).enqueue("broken", {"n": 1})

    assert run_once(sessions, {"broken": broken})
    [job] = jobs(sessions)
    assert (job.status, job.attempts, job.last_error) == ("queued", 1, "RuntimeError: boom")
    assert job.run_at >= job.updated_at + backoff(1) - timedelta(seconds=1)
    assert run_once(sessions, {"broken": broken}) is False  # not due yet

    for _ in range(job.max_attempts - 1):
        make_due(sessions)
        run_once(sessions, {"broken": broken})
    [job] = jobs(sessions)
    assert (job.status, job.attempts) == ("failed", job.max_attempts)
    assert len(calls) == job.max_attempts


def test_a_failing_handler_s_writes_are_undone(client: TestClient, sessions: sessionmaker[Session]) -> None:
    def half_done(session: Session, payload: dict[str, Any]) -> None:
        HANDLERS["process_item"](session, payload)
        raise RuntimeError("after writing")

    client.post("/items", json={"name": "Undone"})
    run_once(sessions, {"process_item": half_done})
    assert client.get("/items").json()[0]["processed_at"] is None


def test_workers_skip_a_job_another_worker_holds(sessions: sessionmaker[Session]) -> None:
    with sessions.begin() as s:
        SqlJobQueue(s).enqueue("process_item", {"item_id": 1})
    with sessions() as holder:
        assert SqlJobQueue(holder).claim() is not None  # locked until this block ends
        assert run_once(sessions) is False


def test_backoff_doubles_up_to_a_cap() -> None:
    assert [backoff(n).total_seconds() for n in (1, 2, 3)] == [2, 4, 8]
    assert backoff(50) == timedelta(hours=1)
