import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import get_settings
from app.core_cloud.google import CALLBACK_URL, GoogleFinishInput, GoogleFlows
from app.core_cloud.store import SupabaseCoreStore
from app.core_local import dependencies
from app.main import app


def configured_store():
    store = Mock(spec=SupabaseCoreStore)
    store._request.return_value = {"external": {"google": True}}
    return store


def test_google_pkce_exchange_uses_correct_proof_and_fixed_callback():
    flows, store = GoogleFlows(), configured_store()
    started = flows.start(store)
    parsed = urlsplit(started.authorize_url)
    parameters = parse_qs(parsed.query)
    assert parsed.hostname == "axdwbfhrkbovgrbngrrh.supabase.co"
    assert parameters["redirect_to"] == [CALLBACK_URL]
    assert parameters["provider"] == ["google"]
    assert parameters["code_challenge_method"] == ["s256"]
    assert "verifier" not in started.authorize_url and started.flow_id not in started.authorize_url
    store._request.return_value = {"access_token": "synthetic-token"}
    value = GoogleFinishInput(flow_id=started.flow_id, code="synthetic-code")
    flows.finish(store, value)
    call = store._request.call_args
    assert call.args == ("POST", "/auth/v1/token?grant_type=pkce")
    proof = call.kwargs["body"]["code_verifier"]
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(proof.encode()).digest()).rstrip(b"=").decode()
    )
    assert parameters["code_challenge"] == [expected]
    assert call.kwargs["body"]["auth_code"] == "synthetic-code"
    store._session.assert_called_once_with({"access_token": "synthetic-token"})
    with pytest.raises(HTTPException) as failed:
        flows.finish(store, value)
    assert failed.value.detail == "google_login_expired_restart"


@pytest.mark.parametrize("settings", [{"external": {"google": False}}, {"external": None}, {}])
def test_disabled_or_malformed_google_provider_does_not_start_flow(settings):
    store = configured_store()
    store._request.return_value = settings
    with pytest.raises(HTTPException) as failed:
        GoogleFlows().start(store)
    assert failed.value.detail == "google_login_not_configured"


def test_expired_and_unknown_flows_never_exchange_codes(monkeypatch):
    from app.core_cloud import google

    store, flows = configured_store(), GoogleFlows()
    monkeypatch.setattr(google.time, "monotonic", lambda: 10)
    started = flows.start(store)
    store._request.reset_mock()
    monkeypatch.setattr(google.time, "monotonic", lambda: 1000)
    for flow_id in (started.flow_id, "x" * 43):
        with pytest.raises(HTTPException):
            flows.finish(store, GoogleFinishInput(flow_id=flow_id, code="synthetic-code"))
    store._request.assert_not_called()


def test_concurrent_google_callback_exchanges_only_once():
    flows, store = GoogleFlows(), configured_store()
    started = flows.start(store)
    store._request.reset_mock()
    store._request.return_value = {"access_token": "synthetic-token"}
    value = GoogleFinishInput(flow_id=started.flow_id, code="synthetic-code")

    def complete(_):
        try:
            flows.finish(store, value)
            return 200
        except HTTPException as error:
            return error.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(complete, (1, 2))) == [200, 400]
    store._request.assert_called_once()


def test_rejected_code_cannot_reuse_proof():
    flows, store = GoogleFlows(), configured_store()
    started = flows.start(store)
    store._request.side_effect = HTTPException(400, "private upstream details")
    with pytest.raises(HTTPException) as failed:
        flows.finish(store, GoogleFinishInput(flow_id=started.flow_id, code="synthetic-code"))
    assert failed.value.detail == "google_login_expired_restart"
    with pytest.raises(HTTPException):
        flows.finish(store, GoogleFinishInput(flow_id=started.flow_id, code="synthetic-code"))


def test_api_does_not_accept_redirect_injection_or_echo_auth_code(monkeypatch):
    monkeypatch.setenv("CORE_SUPABASE_ENABLED", "true")
    monkeypatch.setenv("CORE_LOCAL_ENABLED", "false")
    monkeypatch.setenv("ENVIRONMENT", "local")
    get_settings.cache_clear()
    store = configured_store()
    app.dependency_overrides[dependencies.get_store] = lambda: store
    dependencies._attempts.clear()
    try:
        with TestClient(app) as client:
            result = client.post(
                "/api/v1/auth/google/finish",
                json={
                    "code": "private-code-value",
                    "flow_id": "short",
                    "redirect_to": "https://untrusted.invalid",
                },
            )
            assert result.status_code == 422
            assert "private-code-value" not in result.text
            store._request.assert_not_called()
    finally:
        app.dependency_overrides.pop(dependencies.get_store, None)
        get_settings.cache_clear()
        dependencies._attempts.clear()


def test_finish_schema_rejects_extra_fields():
    with pytest.raises(ValidationError):
        GoogleFinishInput(flow_id="x" * 43, code="synthetic-code", author_id="spoofed")
