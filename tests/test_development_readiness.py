from starlette.responses import Response

from app.api.routes.development import readiness
from app.core.config import Settings


def test_readiness_is_configuration_only_and_never_contains_secrets(monkeypatch):
    settings = Settings(
        _env_file=None,
        core_supabase_enabled=True,
        supabase_publishable_key="sb_publishable_synthetic",
        core_media_signing_key="synthetic-private-key",
    )
    monkeypatch.setattr("app.api.routes.development.get_settings", lambda: settings)
    response = Response()
    result = readiness(response, None)
    assert result["data_mode"] == "supabase"
    assert result["production_ready"] is False
    assert result["remote_push"] is False and result["voice_upload"] is False
    assert "synthetic" not in str(result)
    assert response.headers["cache-control"] == "no-store"


def test_readiness_endpoint_keeps_default_loopback_gate(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    settings = Settings(_env_file=None, core_supabase_enabled=True)
    monkeypatch.setattr("app.api.routes.development.get_settings", lambda: settings)
    monkeypatch.setattr("app.core_local.dependencies.get_settings", lambda: settings)
    with TestClient(app, client=("192.168.1.20", 1234)) as client:
        assert client.get("/api/v1/development/readiness").status_code == 403
    with TestClient(app) as client:
        assert client.get("/api/v1/development/readiness").json()["production_ready"] is False
