from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException

from tests.test_stealth_cloud import HEADERS, ROOT
from tests.test_stealth_cloud import cloud_api as _cloud_api

cloud_api = _cloud_api


def test_quick_status_is_readonly_and_reports_an_unavailable_migration(cloud_api):
    client, store, _ = cloud_api
    store._request.side_effect = HTTPException(503, "supabase_operation_failed")
    assert client.get(ROOT + "/quick-status").status_code == 401
    result = client.get(ROOT + "/quick-status", headers=HEADERS)
    assert result.json() == {"ready": False}
    assert store._request.call_args.kwargs["body"]["action"] == "lookup"
    store._request.side_effect = HTTPException(401, "invalid_or_expired_session")
    assert client.get(ROOT + "/quick-status", headers=HEADERS).status_code == 401


def test_quick_start_auth_validation_and_session_scoping(cloud_api):
    client, store, _ = cloud_api
    assert client.post(ROOT + "/quick-start", json={"text": "喝50嵐"}).status_code == 401
    for text in ("", "  ", "x\0y", "x" * 161):
        assert (
            client.post(
                ROOT + "/quick-start",
                json={"text": text},
                headers={**HEADERS, "Idempotency-Key": "quick-test-key-1234"},
            ).status_code
            == 422
        )
    store._request.assert_not_called()
    with patch(
        "app.api.routes.stealth_cloud.quick_start",
        side_effect=HTTPException(409, "agent_not_enabled"),
    ) as start:
        result = client.post(
            ROOT + "/quick-start",
            json={"text": "喝50嵐"},
            headers={**HEADERS, "Idempotency-Key": "quick-test-key-1234"},
        )
        assert result.status_code == 409 and start.call_args.args[1].activate_agent is False
        assert start.call_args.args[0].store.token == "synthetic-user-access"


def test_coordination_messages_require_session_idempotency_and_valid_text(cloud_api):
    client, store, _ = cloud_api
    proposal = str(uuid4())
    path = ROOT + "/matches/" + proposal + "/room"
    assert client.get(path).status_code == 401
    store._request.return_value = {"opened": False, "can_send": False, "items": []}
    assert client.get(path, headers=HEADERS).json()["opened"] is False
    key = str(uuid4())
    store._request.return_value = {"id": str(uuid4()), "content": "聊聊"}
    assert (
        client.post(
            path, json={"content": "聊聊"}, headers={**HEADERS, "Idempotency-Key": key}
        ).status_code
        == 201
    )
    call = store._request.call_args.kwargs
    assert call["token"] == "synthetic-user-access"
    assert call["body"]["args"] == {"proposal_id": proposal, "request_key": key, "content": "聊聊"}
    for content in ("", "  ", "x\0y", "x" * 2001):
        assert (
            client.post(
                path, json={"content": content}, headers={**HEADERS, "Idempotency-Key": key}
            ).status_code
            == 422
        )
