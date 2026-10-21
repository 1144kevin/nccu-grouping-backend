from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import get_settings
from app.core_cloud.store import SupabaseCoreStore
from app.core_local import dependencies
from app.core_local.schemas import AppPasswordInput
from app.main import app
from tests.test_core_cloud import profile


@pytest.fixture
def password_api(monkeypatch):
    monkeypatch.setenv("CORE_SUPABASE_ENABLED", "true")
    monkeypatch.setenv("CORE_LOCAL_ENABLED", "false")
    monkeypatch.setenv("ENVIRONMENT", "local")
    get_settings.cache_clear()
    store = Mock(spec=SupabaseCoreStore)
    from app.core_local.schemas import ProfileView

    store.authenticate.return_value = ProfileView.model_validate(profile())
    app.dependency_overrides[dependencies.get_store] = lambda: store
    dependencies._attempts.clear()
    try:
        with TestClient(app) as client:
            yield client, store
    finally:
        app.dependency_overrides.pop(dependencies.get_store, None)
        dependencies._attempts.clear()
        get_settings.cache_clear()


def test_password_needs_live_session_and_only_modifies_its_owner(password_api):
    client, store = password_api
    value = {
        "password": "Synthetic-password-123!",
        "expected_user_id": str(store.authenticate.return_value.id),
    }
    assert client.post("/api/v1/auth/password", json=value).status_code == 401
    store.set_app_password.assert_not_called()
    headers = {"Authorization": "Bearer synthetic-session"}
    result = client.post("/api/v1/auth/password", headers=headers, json=value)
    assert result.status_code == 204 and not result.content
    assert result.headers["cache-control"] == "no-store"
    store.authenticate.assert_called_with("synthetic-session")
    assert store.set_app_password.call_args.args[0] == "synthetic-session"
    assert (
        client.post(
            "/api/v1/auth/password",
            headers=headers,
            json={**value, "expected_user_id": str(uuid4())},
        ).status_code
        == 409
    )
    assert store.set_app_password.call_count == 1
    assert (
        client.post(
            "/api/v1/auth/password", headers=headers, json={**value, "user_id": "someone-else"}
        ).status_code
        == 422
    )


def test_revoked_unverified_and_deleting_accounts_cannot_set_password(password_api):
    client, store = password_api
    headers = {"Authorization": "Bearer synthetic-session"}
    for field in ("email_verified", "deletion_pending"):
        original = store.authenticate.return_value
        store.authenticate.return_value = original.model_copy(
            update={field: field == "deletion_pending"}
        )
        assert (
            client.post(
                "/api/v1/auth/password",
                headers=headers,
                json={"password": "Synthetic-password-123!", "expected_user_id": str(original.id)},
            ).status_code
            == 403
        )
        store.authenticate.return_value = original
    store.authenticate.side_effect = HTTPException(401, "invalid_or_expired_session")
    assert (
        client.post(
            "/api/v1/auth/password", headers=headers, json={"password": "Synthetic-password-123!"}
        ).status_code
        == 401
    )
    store.set_app_password.assert_not_called()


def test_password_input_rejects_short_values_without_echoing_them(password_api):
    client, store = password_api
    result = client.post(
        "/api/v1/auth/password",
        headers={"Authorization": "Bearer synthetic-session"},
        json={"password": "short", "expected_user_id": str(store.authenticate.return_value.id)},
    )
    assert result.status_code == 422 and '"input"' not in result.text
    with pytest.raises(ValidationError):
        AppPasswordInput(password="x" * 129, expected_user_id=uuid4())
