import json

import pytest
from fastapi.testclient import TestClient

from shikumi_app.logs import configure_logging


def test_request_id_is_returned_and_logged(client: TestClient, capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging()  # again, so the log goes to the stdout capsys captures
    response = client.get("/health", headers={"X-Request-ID": "abc-123"})
    assert response.headers["X-Request-ID"] == "abc-123"
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    request = next(line for line in lines if line["message"] == "request")
    assert request["request_id"] == "abc-123"
    assert request["path"] == "/health"
    assert request["status"] == 200


def test_a_request_id_is_made_when_missing_or_unsafe(client: TestClient) -> None:
    assert len(client.get("/health").headers["X-Request-ID"]) == 32
    unsafe = client.get("/health", headers={"X-Request-ID": "bad id\n"})
    assert unsafe.headers["X-Request-ID"] != "bad id\n"


def test_cors_allows_the_configured_origin(client: TestClient) -> None:
    response = client.options(
        "/items",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"},
    )
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    other = client.get("/items", headers={"Origin": "https://elsewhere.example"})
    assert "access-control-allow-origin" not in other.headers
