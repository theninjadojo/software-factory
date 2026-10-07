#!/usr/bin/env bash
# Checks the fastapi kind in place, as its CI does. Needs uv, Docker, and DATABASE_URL pointing at a running PostgreSQL
# (the tests use and create the database named there plus "_test"), for example:
#   docker run -d --rm -p 5432:5432 -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=shikumi_app postgres:18
#   DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/shikumi_app templates/verify/fastapi.sh
set -euo pipefail
: "${DATABASE_URL:?set DATABASE_URL to a running PostgreSQL}"
cd "$(dirname "$0")/../kinds/fastapi"

uv sync --locked
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv run alembic upgrade head
docker build -t shikumi-app-template-check .
