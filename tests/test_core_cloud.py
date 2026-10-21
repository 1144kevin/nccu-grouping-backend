import json
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

from app.core.config import Settings
from app.core_cloud.store import PROJECT_URL, SupabaseCoreStore
from app.core_local.schemas import ConfirmationView, LoginInput, RefreshInput, RegisterInput


@pytest.fixture
def cloud(monkeypatch):
    real_client = httpx.Client
    requests = []
    responses = []

    def handle(request):
        requests.append(request)
        status, data = responses.pop(0)
        return httpx.Response(status, json=data)

    def client(**kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False
        return real_client(**kwargs, transport=httpx.MockTransport(handle))

    monkeypatch.setattr(httpx, "Client", client)
    settings = Settings(
        _env_file=None,
        supabase_url=PROJECT_URL,
        supabase_publishable_key="sb_publishable_synthetic",
    )
    return SupabaseCoreStore(settings, "synthetic-user-token"), requests, responses


def profile():
    return {
        "id": str(uuid4()),
        "email": "synthetic@example.test",
        "display_name": "測試主揪",
        "created_at": datetime.now(UTC).isoformat(),
        "auth_mode": "supabase",
        "email_verified": True,
    }


def test_refresh_rotates_credentials_and_verifies_live_session(cloud):
    store, requests, responses = cloud
    responses.extend(
        [
            (200, {"access_token": "rotated-access", "refresh_token": "rotated-refresh"}),
            (200, {"id": "auth-user"}),
            (200, profile()),
        ]
    )
    session = store.refresh(RefreshInput(refresh_token="synthetic-refresh"))
    assert session.refresh_token == "rotated-refresh"
    assert json.loads(requests[0].content) == {"refresh_token": "synthetic-refresh"}
    assert "authorization" not in requests[0].headers
    assert requests[2].headers["authorization"] == "Bearer rotated-access"


@pytest.mark.parametrize("status", [400, 403, 404, 422])
def test_invalid_refresh_is_session_expiry_without_leaking_provider_details(cloud, status):
    store, _, responses = cloud
    responses.append((status, {"code": "refresh_token_not_found", "msg": "private-credential"}))
    with pytest.raises(HTTPException) as failure:
        store.refresh(RefreshInput(refresh_token="synthetic-refresh"))
    assert failure.value.status_code == 401
    assert failure.value.detail == "invalid_or_expired_session"


def test_cloud_auth_verifies_with_auth_server_and_live_session_rpc(cloud):
    store, requests, responses = cloud
    responses.extend(
        [
            (200, {"access_token": "issued-user-token", "expires_in": 3600}),
            (200, {"id": "auth-user"}),
            (200, profile()),
        ]
    )
    session = store.login(
        LoginInput(email="synthetic@example.test", password="Dedicated-test-password")
    )
    assert session.user.auth_mode == "supabase" and session.user.email_verified
    assert [request.url.path for request in requests] == [
        "/auth/v1/token",
        "/auth/v1/user",
        "/rest/v1/rpc/core_dispatch",
    ]
    assert requests[1].headers["authorization"] == "Bearer issued-user-token"
    assert json.loads(requests[2].content) == {"action": "me", "args": {}}


def test_signup_pending_confirmation_does_not_invent_a_session(cloud):
    store, requests, responses = cloud
    responses.append((200, {"id": str(uuid4()), "email": "synthetic@example.test"}))
    result = store.register(
        RegisterInput(
            email="synthetic@example.test", password="Dedicated-test-password", display_name="測試"
        )
    )
    assert isinstance(result, ConfirmationView)
    assert len(requests) == 1 and "authorization" not in requests[0].headers


@pytest.mark.parametrize(
    "data,expected_status,expected_detail",
    [
        ({"code": 400, "error_code": "invalid_credentials"}, 401, "invalid_email_or_password"),
        ({"error_code": "email_not_confirmed"}, 400, "email_confirmation_required"),
        ({"code": "email_not_confirmed"}, 400, "email_confirmation_required"),
        ({"code": 400, "error_code": "unknown_auth_error"}, 503, "supabase_operation_failed"),
        ({"code": 400, "error_code": None}, 503, "supabase_operation_failed"),
    ],
)
def test_login_accepts_both_auth_error_formats_without_exposing_details(
    cloud, data, expected_status, expected_detail
):
    store, requests, responses = cloud
    responses.append((400, {**data, "msg": "private account details"}))
    with pytest.raises(HTTPException) as failure:
        store.login(LoginInput(email="synthetic@example.test", password="Dedicated-test-password"))
    assert failure.value.status_code == expected_status
    assert failure.value.detail == expected_detail
    assert len(requests) == 1


def test_revoked_supabase_session_is_401_even_when_auth_returns_403(cloud):
    store, _, responses = cloud
    responses.append((403, {"code": "session_not_found", "msg": "sensitive internal data"}))
    with pytest.raises(HTTPException) as failure:
        store.authenticate("revoked-token")
    assert failure.value.status_code == 401
    assert failure.value.detail == "invalid_or_expired_session"


def test_rpc_uses_only_request_token_and_never_accepts_actor_as_authority(cloud):
    store, requests, responses = cloud
    responses.append((200, profile()))
    store._rpc("me")
    assert requests[0].headers["authorization"] == "Bearer synthetic-user-token"
    assert requests[0].headers["apikey"] == "sb_publishable_synthetic"
    assert "actor" not in json.loads(requests[0].content)


def test_history_uses_independent_readonly_rpc_with_verified_request_token(cloud):
    store, requests, responses = cloud
    responses.append((200, {"items": [], "next_cursor": None}))
    assert store.posts(uuid4(), "history", 20, None).items == []
    assert requests[0].url.path == "/rest/v1/rpc/core_order_history"
    assert requests[0].headers["authorization"] == "Bearer synthetic-user-token"
    assert json.loads(requests[0].content) == {
        "args": {"limit": 20, "cursor": None, "q": "", "active_only": False}
    }


@pytest.mark.parametrize(
    "status,data,expected",
    [
        (400, {"code": "PT409", "message": "post_full"}, 409),
        (400, {"code": "invalid_credentials"}, 401),
        (400, {"code": "email_address_invalid", "msg": "private email value"}, 400),
        (400, {"code": "email_address_not_authorized"}, 400),
        (500, {"message": "private upstream failure"}, 503),
    ],
)
def test_cloud_errors_are_mapped_without_leaking_upstream_bodies(cloud, status, data, expected):
    store, _, responses = cloud
    responses.append((status, data))
    with pytest.raises(HTTPException) as failure:
        store._rpc("me")
    assert failure.value.status_code == expected
    assert "private" not in failure.value.detail
    if data.get("code") == "email_address_invalid":
        assert failure.value.detail == "email_address_invalid"
    if data.get("code") == "email_address_not_authorized":
        assert failure.value.detail == "email_provider_not_configured"


def test_wrong_project_and_privileged_keys_are_rejected():
    for url, key in [
        ("https://other-project.supabase.co", "sb_publishable_synthetic"),
        (PROJECT_URL, "sb_secret_synthetic"),
        (PROJECT_URL, "service_role_key"),
    ]:
        with pytest.raises(HTTPException) as failure:
            SupabaseCoreStore(
                Settings(_env_file=None, supabase_url=url, supabase_publishable_key=key)
            )
        assert failure.value.status_code == 503


def test_missing_oauth_code_reports_restart_instead_of_service_outage(cloud):
    store, _, responses = cloud
    responses.append((404, {"code": "flow_state_not_found", "message": "private details"}))
    with pytest.raises(HTTPException) as failure:
        store._request(
            "POST",
            "/auth/v1/token?grant_type=pkce",
            body={"auth_code": "synthetic-code", "code_verifier": "synthetic-proof"},
        )
    assert failure.value.status_code == 400
    assert failure.value.detail == "google_login_expired_restart"
