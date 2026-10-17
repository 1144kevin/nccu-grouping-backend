from datetime import UTC, datetime, timedelta
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core_cloud.store import SupabaseCoreStore
from app.core_local import dependencies
from app.core_local.schemas import DemoCleanupView, ProfileView, ServerClockView, SessionView
from app.main import app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CORE_SUPABASE_ENABLED", "true")
    monkeypatch.setenv("CORE_LOCAL_ENABLED", "false")
    monkeypatch.setenv("ENVIRONMENT", "local")
    get_settings.cache_clear()
    store = Mock(spec=SupabaseCoreStore)
    user = ProfileView(
        id=uuid4(),
        email="fixture@example.test",
        display_name="測試人物",
        created_at=datetime.now(UTC),
        auth_mode="supabase",
    )
    store.authenticate.return_value = user
    store.refresh.return_value = SessionView(
        access_token="rotated-access",
        refresh_token="rotated-refresh",
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        user=user,
    )
    store.clear_demo.return_value = DemoCleanupView(deleted_posts=2, kept_posts=1)
    store.clock.return_value = ServerClockView(
        server_time=datetime(2026, 10, 16, 13, 0, tzinfo=UTC)
    )
    app.dependency_overrides[dependencies.get_store] = lambda: store
    dependencies._attempts.clear()
    try:
        with TestClient(app) as transport:
            yield transport, store
    finally:
        app.dependency_overrides.pop(dependencies.get_store, None)
        dependencies._attempts.clear()
        get_settings.cache_clear()


def test_refresh_can_use_rotating_credential_without_an_unexpired_access_token(client):
    transport, store = client
    result = transport.post("/api/v1/auth/refresh", json={"refresh_token": "synthetic-refresh"})
    assert result.status_code == 200
    assert result.headers["cache-control"] == "no-store"
    assert result.json()["refresh_token"] == "rotated-refresh"
    store.authenticate.assert_not_called()
    assert store.refresh.call_args.args[0].refresh_token.get_secret_value() == "synthetic-refresh"


def test_refresh_rejects_extra_actor_or_redirect_and_never_echoes_credentials(client):
    transport, store = client
    result = transport.post(
        "/api/v1/auth/refresh", json={"refresh_token": "private-test-credential", "actor": "forged"}
    )
    assert result.status_code == 422
    assert "private-test-credential" not in result.text
    store.refresh.assert_not_called()


def test_deadline_clock_uses_store_time_and_requires_auth(client):
    transport, store = client
    assert transport.get("/api/v1/development/clock").status_code == 401
    result = transport.get(
        "/api/v1/development/clock", headers={"Authorization": "Bearer synthetic-access"}
    )
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    assert ServerClockView.model_validate(result.json()).server_time == datetime(
        2026, 10, 16, 13, 0, tzinfo=UTC
    )
    assert set(result.json()) == {"server_time"}


def test_cleanup_requires_access_token_and_cannot_specify_another_owner(client):
    transport, store = client
    assert transport.delete("/api/v1/development/demo").status_code == 401
    store.clear_demo.assert_not_called()
    result = transport.delete(
        "/api/v1/development/demo", headers={"Authorization": "Bearer synthetic-access"}
    )
    assert result.status_code == 200
    assert result.json() == {"deleted_posts": 2, "kept_posts": 1}
    store.clear_demo.assert_called_once_with()
