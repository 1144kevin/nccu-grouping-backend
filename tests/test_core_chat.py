from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core_cloud.store import SupabaseCoreStore
from app.core_local import dependencies
from app.core_local.schemas import (
    ChatActivity,
    ChatReadView,
    ConversationPage,
    MessageView,
    OrderActivityPage,
    ProfileView,
)
from app.main import app


@pytest.fixture
def chat_api(monkeypatch):
    monkeypatch.setenv("CORE_SUPABASE_ENABLED", "true")
    monkeypatch.setenv("CORE_LOCAL_ENABLED", "false")
    monkeypatch.setenv("ENVIRONMENT", "local")
    get_settings.cache_clear()
    store = Mock(spec=SupabaseCoreStore)
    user = ProfileView(
        id=uuid4(),
        email="chat-fixture@example.test",
        display_name="測試團員",
        created_at=datetime.now(UTC),
        auth_mode="supabase",
    )
    store.authenticate.return_value = user
    store.conversations.return_value = ConversationPage(items=[])
    message = MessageView(
        id=uuid4(),
        post_id=uuid4(),
        sender_id=user.id,
        sender_name=user.display_name,
        sender_badge="🍱",
        content="六點見。",
        created_at=datetime.now(UTC),
    )
    store.send_message.return_value = message
    store.chat_activity.return_value = ChatActivity(groups=[], total_unread=0, pending_requests=1)
    store.read_chat.return_value = ChatReadView()
    store.order_activity.return_value = OrderActivityPage(items=[])
    app.dependency_overrides[dependencies.get_store] = lambda: store
    try:
        with TestClient(app) as client:
            yield client, store, message
    finally:
        app.dependency_overrides.pop(dependencies.get_store, None)
        get_settings.cache_clear()


def test_chat_list_requires_auth_and_private_responses_are_not_cached(chat_api):
    client, store, _ = chat_api
    assert client.get("/api/v1/conversations").status_code == 401
    store.conversations.assert_not_called()
    response = client.get(
        "/api/v1/conversations", headers={"Authorization": "Bearer synthetic-chat-token"}
    )
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    store.conversations.assert_called_once_with(30, None)


def test_send_uses_verified_identity_and_idempotency_key(chat_api):
    client, store, message = chat_api
    headers = {
        "Authorization": "Bearer synthetic-chat-token",
        "Idempotency-Key": "synthetic-message-key",
    }
    path = f"/api/v1/conversations/{message.post_id}/messages"
    response = client.post(path, json={"content": "  六點見。  "}, headers=headers)
    assert response.status_code == 201 and response.headers["cache-control"] == "no-store"
    assert response.json()["sender_id"] == str(message.sender_id)
    call = store.send_message.call_args.args
    assert (
        call[0] == message.post_id
        and call[1].content == "六點見。"
        and call[2] == "synthetic-message-key"
    )
    store.send_message.reset_mock()
    response = client.post(
        path, json={"content": "private-chat-body", "sender_id": str(uuid4())}, headers=headers
    )
    assert response.status_code == 422 and "private-chat-body" not in response.text
    store.send_message.assert_not_called()


def test_chat_query_and_message_size_boundaries(chat_api):
    client, store, message = chat_api
    headers = {
        "Authorization": "Bearer synthetic-chat-token",
        "Idempotency-Key": "synthetic-message-key",
    }
    assert client.get("/api/v1/conversations?limit=51", headers=headers).status_code == 422
    assert (
        client.get("/api/v1/conversations/not-a-uuid/messages", headers=headers).status_code == 422
    )
    path = f"/api/v1/conversations/{message.post_id}/messages"
    assert client.post(path, json={"content": " "}, headers=headers).status_code == 422
    assert client.post(path, json={"content": "x" * 2001}, headers=headers).status_code == 422
    store.send_message.assert_not_called()


def test_chat_activity_and_read_markers_use_own_authenticated_session(chat_api):
    client, store, message = chat_api
    headers = {"Authorization": "Bearer synthetic-chat-token"}
    result = client.get("/api/v1/conversations/activity", headers=headers)
    assert result.status_code == 200 and result.json()["pending_requests"] == 1
    assert result.headers["cache-control"] == "no-store"
    path = f"/api/v1/conversations/{message.post_id}/read"
    assert client.post(path, json={"message_id": str(message.id)}, headers=headers).json() == {
        "ok": True
    }
    store.read_chat.assert_called_once_with(message.post_id, message.id)
    store.read_chat.reset_mock()
    assert (
        client.post(
            path, json={"message_id": str(message.id), "viewer_id": str(uuid4())}, headers=headers
        ).status_code
        == 422
    )
    assert client.post(path, json={"message_id": str(message.id)}, headers={}).status_code == 401
    store.read_chat.assert_not_called()


def test_order_activity_requires_auth_and_rejects_unbounded_pages(chat_api):
    client, store, _ = chat_api
    assert client.get("/api/v1/notifications").status_code == 401
    headers = {"Authorization": "Bearer synthetic-chat-token"}
    result = client.get("/api/v1/notifications", headers=headers)
    assert result.status_code == 200 and result.json() == {"items": [], "next_cursor": None}
    assert result.headers["cache-control"] == "no-store"
    store.order_activity.assert_called_once_with(30, None)
    store.order_activity.reset_mock()
    assert client.get("/api/v1/notifications?limit=51", headers=headers).status_code == 422
    store.order_activity.assert_not_called()


@pytest.mark.parametrize("content", ["hello\0world", "hello\ud800world"])
def test_invalid_database_text_never_reaches_cloud(chat_api, content):
    client, store, message = chat_api
    headers = {
        "Authorization": "Bearer synthetic-chat-token",
        "Idempotency-Key": "synthetic-message-key",
    }
    import json

    result = client.post(
        f"/api/v1/conversations/{message.post_id}/messages",
        content=json.dumps({"content": content}),
        headers={**headers, "Content-Type": "application/json"},
    )
    assert result.status_code == 422
    store.send_message.assert_not_called()
