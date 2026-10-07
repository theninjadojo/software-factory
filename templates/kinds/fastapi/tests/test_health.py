from fastapi.testclient import TestClient

from shikumi_app.config import Settings
from shikumi_app.main import create_app


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_ready_when_the_database_answers(client: TestClient) -> None:
    assert client.get("/ready").json() == {"status": "ok"}


def test_not_ready_without_a_database() -> None:
    down = Settings(database_url="postgresql+psycopg://nobody:x@127.0.0.1:1/none")
    with TestClient(create_app(down)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/ready").status_code == 503
