import json
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.core_cloud.reply_suggestions import context_key, snapshot, suggest
from app.stealth.ollama import LocalModelError
from tests.test_stealth_cloud import HEADERS, ROOT
from tests.test_stealth_cloud import cloud_api as _cloud_api

cloud_api = _cloud_api


def test_suggestions_fallback_and_refresh_never_assign_work_or_cost():
    first = suggest({"match": {"budget_max_twd": 120}}, 0)
    second = suggest({}, 1)
    assert len(first.suggestions) == 3 and len(set(first.suggestions)) == 3
    assert first.suggestions != second.suggestions
    assert not first.is_llm
    assert all("120" not in text for text in first.suggestions)
    provider = Mock()
    provider.extract.return_value = json.dumps({"suggestions": first.suggestions})
    refreshed = suggest({}, 3, provider, first.suggestions)
    assert not set(refreshed.suggestions).intersection(first.suggestions)
    assert not refreshed.is_llm


def test_model_receives_only_scoped_context_and_rejects_malformed_or_invented_numbers():
    context = {
        "match": {"id": "123456", "budget_max_twd": 120},
        "messages": [{"content": "你方便在哪取餐？"}],
    }
    provider = Mock()
    values = [
        "你方便在哪個地點交接呢？",
        "我們要不要先確認取餐時間？",
        "想先確認一下品項和各自份量，可以嗎？",
    ]
    provider.extract.return_value = json.dumps({"suggestions": values})
    result = suggest(context, 7, provider)
    assert result.is_llm and result.suggestions == values
    assert json.loads(provider.extract.call_args.args[0][0]["content"])["batch"] == 7
    for invalid in (
        [values[0]] * 3,
        [*values[:2], "總共123456元"],
        [*values[:2], "每人120元"],
        ["bad"],
    ):
        provider.extract.return_value = json.dumps({"suggestions": invalid})
        assert not suggest(context, provider=provider).is_llm
    provider.extract.side_effect = LocalModelError("local_model_busy")
    assert not suggest(context, provider=provider).is_llm


def test_context_only_includes_recent_messages_and_changes_when_opponent_replies():
    match = Mock()
    match.model_dump.return_value = {"category": "drink"}
    room = {"items": [{"id": str(i), "content": "照片"} for i in range(30)]}
    value = snapshot(match, room)
    assert len(value["messages"]) == 20
    old = context_key(value)
    room["items"].append({"id": "new", "content": "你方便取餐嗎？", "is_me": False})
    assert context_key(snapshot(match, room)) != old


def test_route_requires_auth_checks_room_twice_and_does_not_send(cloud_api):
    client, store, _ = cloud_api
    path = ROOT + f"/matches/{uuid4()}/reply-suggestions"
    assert client.post(path, json={}).status_code == 401
    with patch("app.api.routes.stealth_cloud.reply_context", return_value={}) as read:
        result = client.post(path, json={"batch": 0}, headers=HEADERS)
        assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
        assert len(result.json()["suggestions"]) == 3 and read.call_count == 2
        store._request.assert_not_called()
    with patch("app.api.routes.stealth_cloud.reply_context", side_effect=[{}, {"new": "message"}]):
        assert (
            client.post(path, json={}, headers=HEADERS).json()["detail"]
            == "stealth_reply_context_changed"
        )
    with patch(
        "app.api.routes.stealth_cloud.reply_context", side_effect=HTTPException(403, "blocked")
    ):
        assert client.post(path, json={}, headers=HEADERS).status_code == 403
    assert client.post(path, json={"batch": -1}, headers=HEADERS).status_code == 422


def test_readonly_room_cannot_generate():
    from app.api.routes.stealth_cloud import reply_context

    service = SimpleNamespace(store=Mock())
    service.store._request.return_value = {"opened": True, "can_send": False}
    with pytest.raises(HTTPException) as error:
        reply_context(service, uuid4())
    assert error.value.status_code == 409


def test_new_local_account_requires_profile_until_saved_and_restored(tmp_path):
    from app.core_local.schemas import LoginInput, ProfileInput, RegisterInput
    from app.core_local.store import CoreStore

    store = CoreStore(tmp_path / "onboarding.db")
    value = RegisterInput(
        email="new@example.test", password="test-password-123", display_name="新朋友"
    )
    session = store.register(value)
    assert session.user.profile_setup_required
    assert store.authenticate(session.access_token).profile_setup_required
    with pytest.raises(HTTPException):
        store.update_profile(
            session.user.id,
            ProfileInput(display_name="新名字", bio="", badge="🍜", expected_version=99),
        )
    assert store.authenticate(session.access_token).profile_setup_required
    updated = store.update_profile(
        session.user.id, ProfileInput(display_name="新名字", bio="", badge="🍜", expected_version=1)
    )
    assert not updated.profile_setup_required
    restored = CoreStore(store.path).login(LoginInput(email=value.email, password=value.password))
    assert not restored.user.profile_setup_required
