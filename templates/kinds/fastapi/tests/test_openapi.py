from fastapi.testclient import TestClient


def test_schema_has_the_routes(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    assert set(paths["/items"]) == {"get", "post"}
    assert "get" in paths["/health"]
    assert "get" in paths["/ready"]
