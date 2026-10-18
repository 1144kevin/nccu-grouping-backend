from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core_cloud.engagement import PreferencesView, ReportInput
from app.core_cloud.store import SupabaseCoreStore
from app.core_local import dependencies
from app.core_local.schemas import ProfileView
from app.main import app

HEADERS = {"Authorization": "Bearer synthetic-session"}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("CORE_SUPABASE_ENABLED", "true")
    monkeypatch.setenv("CORE_LOCAL_ENABLED", "false")
    monkeypatch.setenv("ENVIRONMENT", "local")
    get_settings.cache_clear()
    store = Mock(spec=SupabaseCoreStore)
    store.token = "synthetic-session"
    store.authenticate.return_value = ProfileView(
        id=uuid4(),
        email="fixture@example.test",
        display_name="fixture",
        created_at=datetime.now(UTC),
        auth_mode="supabase",
    )
    app.dependency_overrides[dependencies.get_store] = lambda: store
    try:
        with TestClient(app) as api:
            yield api, store
    finally:
        app.dependency_overrides.pop(dependencies.get_store, None)
        get_settings.cache_clear()


@pytest.mark.parametrize(
    "path",
    [
        "/account/preferences",
        "/account/inbox",
        "/reports",
        "/recommendations",
        "/search?scope=messages&q=晚餐",
    ],
)
def test_account_features_require_session(client, path):
    api, store = client
    assert api.get("/api/v1" + path).status_code == 401
    store._request.assert_not_called()


def test_preferences_use_current_session_and_version(client):
    api, store = client
    value = PreferencesView(version=0).model_dump()
    store._request.return_value = value
    response = api.get("/api/v1/account/preferences", headers=HEADERS)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json()["version"] == 0
    store._request.return_value = {**value, "version": 1}
    response = api.put(
        "/api/v1/account/preferences",
        headers=HEADERS,
        json={
            "expected_version": 0,
            "value": {k: v for k, v in value.items() if k != "version"},
        },
    )
    assert response.status_code == 200
    assert store._request.call_args.kwargs["token"] == "synthetic-session"
    assert store._request.call_args.kwargs["body"]["action"] == "preferences_put"


@pytest.mark.parametrize(
    "patch",
    [
        {"owner_id": str(uuid4())},
        {"expected_version": "0"},
        {"value": {"notifications": {"messages": "false"}}},
        {"value": {"preferred_tags": ["x"] * 25}},
        {"value": {"preferred_tags": ["x", "x"]}},
        {"value": {"preferred_tags": ["\0"]}},
    ],
)
def test_invalid_or_forged_settings_never_reach_database(client, patch):
    api, store = client
    body = {
        "expected_version": 0,
        "value": PreferencesView(version=0).model_dump(exclude={"version"}),
        **patch,
    }
    assert api.put("/api/v1/account/preferences", headers=HEADERS, json=body).status_code == 422
    store._request.assert_not_called()


def test_reports_are_private_idempotent_requests_not_fake_success(client):
    api, store = client
    body = ReportInput(kind="feedback", detail="這是明確描述的測試問題，需要管理者查看。")
    store._request.return_value = {
        **body.model_dump(mode="json"),
        "id": str(uuid4()),
        "status": "received",
        "created_at": datetime.now(UTC).isoformat(),
    }
    response = api.post(
        "/api/v1/reports",
        headers={**HEADERS, "Idempotency-Key": "test-report-key-001"},
        json=body.model_dump(mode="json"),
    )
    assert response.status_code == 201 and response.json()["status"] == "received"
    assert response.headers["cache-control"] == "no-store"
    assert store._request.call_args.kwargs["body"] == {
        "action": "report",
        "args": {"value": body.model_dump(mode="json"), "request_key": "test-report-key-001"},
    }


@pytest.mark.parametrize(
    "body",
    [
        {"kind": "post", "detail": "內容需要至少十個字才能檢舉。"},
        {"kind": "feedback", "target_id": str(uuid4()), "detail": "一般回報不能假冒檢舉對象。"},
        {"kind": "feedback", "detail": "太短"},
        {"kind": "feedback", "detail": "x" * 1001},
        {"kind": "feedback", "detail": "x" * 12, "status": "resolved"},
    ],
)
def test_report_schema_rejects_bad_targets_and_forged_resolution(client, body):
    api, store = client
    response = api.post(
        "/api/v1/reports", headers={**HEADERS, "Idempotency-Key": "test-report-key-001"}, json=body
    )
    assert response.status_code == 422
    store._request.assert_not_called()


def test_inbox_requires_real_booleans_and_does_not_accept_other_owner(client):
    api, store = client
    for value in [
        {"muted": "true", "archived": False},
        {"muted": True, "archived": False, "owner_id": str(uuid4())},
    ]:
        assert (
            api.put(f"/api/v1/account/inbox/{uuid4()}", headers=HEADERS, json=value).status_code
            == 422
        )
    store._request.assert_not_called()


def test_recommendations_are_authenticated_and_paginated(client):
    api, store = client
    store._request.return_value = {"items": [], "next_cursor": None}
    response = api.get("/api/v1/recommendations?limit=12", headers=HEADERS)
    assert response.status_code == 200 and response.json()["items"] == []
    assert store._request.call_args.args == ("POST", "/rest/v1/rpc/core_recommendation_feed")
    assert store._request.call_args.kwargs["body"] == {"args": {"limit": 12, "cursor": None}}


def test_recommendation_reasons_survive_api_response_validation(client):
    api, store = client
    now = datetime.now(UTC).isoformat()
    details = {
        "version": "rules_v2",
        "score": 40,
        "remaining_seats": 1,
        "as_of": now,
        "reasons": [{"code": "remaining_seats", "text": "尚有 1 個名額"}],
    }
    post = {
        "id": str(uuid4()),
        "author_id": str(uuid4()),
        "author_name": "隔離驗證",
        "content": "隔離推薦理由的回應驗證，沒有真實訂單。",
        "tags": ["驗證"],
        "location": {"name": "虛構測試點", "latitude": 24.987, "longitude": 121.576},
        "price_per_person": None,
        "deadline_at": now,
        "capacity": 3,
        "joined": 2,
        "status": "open",
        "my_status": "none",
        "created_at": now,
        "recommendation": details,
    }
    store._request.return_value = {"items": [post], "next_cursor": None}
    response = api.get("/api/v1/recommendations", headers=HEADERS)
    assert response.status_code == 200
    result = response.json()["items"][0]["recommendation"]
    assert result["reasons"] == details["reasons"] and result["remaining_seats"] == 1
    # Old development SQL remains compatible while the reviewed upgrade is pending.
    post.pop("recommendation")
    response = api.get("/api/v1/recommendations", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["items"][0]["recommendation"] is None


def test_saved_posts_cannot_forge_owner_or_boolean(client):
    api, store = client
    for value in (
        {"hidden": "true", "reminder": False},
        {"hidden": False, "reminder": True, "owner_id": str(uuid4())},
    ):
        assert (
            api.put(
                f"/api/v1/account/saved-posts/{uuid4()}",
                headers=HEADERS,
                json=value,
            ).status_code
            == 422
        )
    store._request.assert_not_called()


def test_saved_reminder_is_database_timed_and_never_claims_push(client):
    api, store = client
    now = datetime.now(UTC).isoformat()
    post = str(uuid4())
    store._request.return_value = {
        "post_id": post,
        "hidden": False,
        "reminder": True,
        "reminder_at": now,
        "deadline_at": now,
        "post_content": "隔離測試",
        "status": "due",
    }
    result = api.put(
        f"/api/v1/account/saved-posts/{post}",
        headers=HEADERS,
        json={
            "hidden": False,
            "reminder": True,
        },
    )
    assert result.status_code == 200 and result.json()["status"] == "due"
    assert "push_sent" not in result.json()
    assert store._request.call_args.kwargs["body"]["args"] == {
        "post_id": post,
        "hidden": False,
        "reminder": True,
    }


def test_scoped_search_uses_session_and_does_not_forward_owner_query(client):
    api, store = client
    store._request.return_value = {"items": [], "next_cursor": None}
    response = api.get("/api/v1/search?scope=messages&q=晚餐&owner_id=somebody", headers=HEADERS)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert store._request.call_args.kwargs["token"] == "synthetic-session"
    assert store._request.call_args.kwargs["body"] == {
        "args": {"scope": "messages", "q": "晚餐", "limit": 30, "cursor": None}
    }


@pytest.mark.parametrize(
    "query",
    [
        "scope=all&q=晚餐",
        "scope=messages&q=x",
        "scope=users&q=" + "x" * 81,
        "scope=users&q=晚餐&limit=100",
    ],
)
def test_search_bounds_rejected_before_sql(client, query):
    api, store = client
    assert api.get("/api/v1/search?" + query, headers=HEADERS).status_code == 422
    store._request.assert_not_called()
