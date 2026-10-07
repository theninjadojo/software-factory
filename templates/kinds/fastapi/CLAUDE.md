# Shikumi App

A FastAPI service on Python 3.13 with PostgreSQL, managed with uv. One image runs two processes: the HTTP API and a job worker.

## How the code is organised

Hexagonal: the domain in the middle, adapters around it. Dependencies point inwards only.

```
src/shikumi_app/
  domain/          plain dataclasses and rules. No FastAPI, SQLAlchemy or pydantic imports here.
  services/        use cases (ItemService). ports.py holds the Protocols they need (ItemRepository, JobQueue).
  repositories/    SQLAlchemy adapters for the ports; tables.py has every table.
  api/             HTTP: routes/, schemas.py (request and response models), deps.py (wiring), middleware.py
  worker.py        the job worker and its HANDLERS (job kind -> function)
  config.py        Settings (pydantic-settings), read from the environment and .env
  logs.py          JSON logging; every line carries the request (or job) id
  main.py          create_app(): middleware, routes, the database engine
migrations/        Alembic; one file per schema change
tests/             pytest against a real PostgreSQL
```

To add a feature, copy the `items` example through every layer: domain type, port, service, repository, schema, route, tests.

## Conventions

- SQLAlchemy 2 is used synchronously (typed `Mapped[...]` models, psycopg 3). Route functions are plain `def`, so FastAPI runs
  them in its thread pool. Do not make them `async def` while they use a `Session`.
- A route that changes data calls `session.commit()` itself, once, after the service returns. Services never commit.
- Services depend on the Protocols in `services/ports.py`, not on SQLAlchemy, so they can be unit tested with fakes
  (see `tests/test_item_service.py`).
- Domain errors (for example `InvalidItemError`) are turned into HTTP responses in `main.py`, not raised as `HTTPException`
  from services.
- Background work: `SqlJobQueue(session).enqueue(kind, payload, dedupe_key=...)` in the same transaction as the change that
  needs it, and a handler in `worker.HANDLERS`. Handlers must be idempotent: a job can run more than once. A failing job is
  retried with exponential backoff (`domain.jobs.backoff`) and marked `failed` after `max_attempts`.
- Configuration goes in `config.Settings` with a type and a safe default, and in `.env.example`. Never commit a secret.
- Log with `logging.getLogger(__name__)` and pass fields with `extra={...}`; do not format values into the message.
- Changing a table: edit `repositories/tables.py`, then generate a migration and read it before committing:
  `uv run alembic revision --autogenerate -m "what changed"`. `tests/test_migrations.py` fails when they disagree.
- Ruff (lint and format) and mypy `--strict` must pass. Line length is 120.

## Commands

Installing needs the network; everything after that works offline.

```sh
uv sync                                      # install from uv.lock (network)
uv run alembic upgrade head                  # apply migrations to DATABASE_URL
uv run uvicorn shikumi_app.main:app --reload # the API on :8000
uv run python -m shikumi_app.worker          # the worker
uv run pytest                                # tests (need PostgreSQL, see below)
uv run ruff check . && uv run ruff format --check . && uv run mypy
uv run ruff format .                         # fix formatting
docker build -t shikumi-app .                # the image (network)
```

The tests need a PostgreSQL server. They use the database named in `DATABASE_URL` plus `_test`
(default `postgresql+psycopg://app:app@localhost:5432/shikumi_app_test`) and create it when it is missing. They empty
its tables before each test. CI starts PostgreSQL as a service container. Locally:

```sh
docker run -d --name shikumi-app-db -p 5432:5432 \
  -e POSTGRES_USER=app -e POSTGRES_PASSWORD=app -e POSTGRES_DB=shikumi_app postgres:18
```

Adding a dependency: `uv add <package>` (or `uv add --dev <package>`), which updates `pyproject.toml` and `uv.lock`; this needs
the network. Commit both files.
