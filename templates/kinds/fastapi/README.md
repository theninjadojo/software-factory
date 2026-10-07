# Shikumi App

<!-- shikumi:summary -->

An HTTP API built with FastAPI on Python 3.13, with PostgreSQL (SQLAlchemy 2 and Alembic migrations) and a worker that runs
background jobs from a queue kept in the same database.

## Run it locally

You need [uv](https://docs.astral.sh/uv/) and Docker (for PostgreSQL).

```sh
docker run -d --name shikumi-app-db -p 5432:5432 \
  -e POSTGRES_USER=app -e POSTGRES_PASSWORD=app -e POSTGRES_DB=shikumi_app postgres:18
cp .env.example .env
uv sync
uv run alembic upgrade head                           # create or update the tables
uv run uvicorn shikumi_app.main:app --reload          # the API on http://localhost:8000
uv run python -m shikumi_app.worker                   # the worker, in a second terminal
```

The interactive API docs are at http://localhost:8000/docs and the schema at http://localhost:8000/openapi.json.

```sh
curl -X POST localhost:8000/items -H 'content-type: application/json' -d '{"name": "First item"}'
curl localhost:8000/items
```

## Test

The tests run against a real PostgreSQL: the database in `DATABASE_URL` with `_test` added to its name (created if it is missing).

```sh
uv run pytest
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

## Container

```sh
docker build -t shikumi-app .
docker run --rm --env-file .env shikumi-app alembic upgrade head      # migrations are a separate step
docker run --rm --env-file .env -p 8000:8000 shikumi-app               # the API
docker run --rm --env-file .env shikumi-app python -m shikumi_app.worker
```

From inside a container, `localhost` is the container itself: point `DATABASE_URL` at the database's host name.

## Deploy

See [DEPLOY.md](DEPLOY.md).
