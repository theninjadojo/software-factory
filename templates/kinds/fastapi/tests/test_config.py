import pytest

from shikumi_app.config import Settings


@pytest.mark.parametrize("given", ["postgres://u:p@h:5432/d", "postgresql://u:p@h:5432/d"])
def test_host_database_urls_get_the_driver(given: str) -> None:
    assert Settings(database_url=given).database_url == "postgresql+psycopg://u:p@h:5432/d"


def test_cors_origins_come_from_a_json_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", '["https://a.example","https://b.example"]')
    assert Settings().cors_origins == ["https://a.example", "https://b.example"]
