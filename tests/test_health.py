from fastapi.testclient import TestClient

from app.main import app


def test_health_check() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "政缺一咖 API",
        "environment": "local",
    }


def test_openapi_uses_versioned_api_prefix() -> None:
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()

    assert "/health" in schema["paths"]
