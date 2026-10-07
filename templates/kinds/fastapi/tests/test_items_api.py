from datetime import datetime

from fastapi.testclient import TestClient


def test_create_then_list(client: TestClient) -> None:
    created = client.post("/items", json={"name": "  First item "})
    assert created.status_code == 201
    item = created.json()
    assert item["name"] == "First item"
    assert item["processed_at"] is None
    assert datetime.fromisoformat(item["created_at"]).tzinfo is not None

    client.post("/items", json={"name": "Second item"})
    listed = client.get("/items")
    assert listed.status_code == 200
    assert [i["name"] for i in listed.json()] == ["Second item", "First item"]
    assert client.get("/items", params={"limit": 1}).json()[0]["name"] == "Second item"


def test_rejects_a_bad_name(client: TestClient) -> None:
    assert client.post("/items", json={"name": ""}).status_code == 422
    assert client.post("/items", json={"name": "   "}).status_code == 422
    assert client.post("/items", json={"name": "x" * 201}).status_code == 422
    assert client.get("/items").json() == []


def test_responses_match_the_openapi_schema(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()
    item = schema["components"]["schemas"]["ItemOut"]
    created = client.post("/items", json={"name": "Shape"}).json()
    assert set(created) == set(item["properties"])
    assert set(item["required"]) <= set(created)
