"""Tests use a real PostgreSQL: the database in DATABASE_URL with "_test" added to its name, created when missing."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, make_url, text
from sqlalchemy.orm import Session, sessionmaker

from shikumi_app.config import Settings
from shikumi_app.db import make_engine, make_sessionmaker
from shikumi_app.main import create_app

ROOT = Path(__file__).parent.parent


def _test_url() -> str:
    url = make_url(Settings().database_url)
    name = url.database or "shikumi_app"
    return url.set(database=name if name.endswith("_test") else f"{name}_test").render_as_string(hide_password=False)


def _create_database(url: str) -> None:
    target = make_url(url)
    admin = create_engine(target.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        exists = conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": target.database})
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    admin.dispose()


def alembic_config(url: str) -> Config:
    cfg = Config(ROOT / "alembic.ini")
    cfg.attributes.update(url=url, configure_logger=False)
    return cfg


@pytest.fixture(scope="session")
def settings() -> Settings:
    url = _test_url()
    _create_database(url)
    command.upgrade(alembic_config(url), "head")
    return Settings(database_url=url, cors_origins=["http://localhost:5173"])


@pytest.fixture(scope="session")
def engine(settings: Settings) -> Iterator[Engine]:
    engine = make_engine(settings.database_url)
    yield engine
    engine.dispose()


@pytest.fixture
def sessions(engine: Engine) -> sessionmaker[Session]:
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE items, jobs RESTART IDENTITY"))
    return make_sessionmaker(engine)


@pytest.fixture
def client(settings: Settings, sessions: sessionmaker[Session]) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as client:
        yield client
