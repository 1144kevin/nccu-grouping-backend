from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.routes import recommendations
from app.core_cloud.feed_semantic import (
    FEED_SLOT,
    FeedSemanticRequest,
    eligible_pool,
    semantic_feed,
)
from app.stealth.ollama import LocalModelError
from app.stealth.provider_contracts import VectorBatch
from tests.test_core_engagement import HEADERS, client  # noqa: F401


def post(content="活動剩餘便當，請來領取", **changes):
    now = datetime.now(UTC)
    return {
        "id": str(uuid4()), "author_id": str(uuid4()), "author_name": "合成主揪",
        "content": content, "tags": ["分享"],
        "location": {"name": "政大正門", "latitude": 24.98, "longitude": 121.57},
        "price_per_person": "0", "deadline_at": now + timedelta(hours=1),
        "capacity": 5, "joined": 1, "status": "open", "my_status": "none",
        "created_at": now, "version": 1,
        "recommendation": {
            "version": "rules_v2", "score": 12, "remaining_seats": 4, "as_of": now,
            "reasons": [{"code": "remaining_seats", "text": "尚有4個名額"}],
        }, **changes,
    }


def service(before, after=None):
    requests = []
    feeds = iter([before, after if after is not None else before])

    def request(*args, **kwargs):
        requests.append((args, kwargs))
        return {"items": next(feeds), "next_cursor": None}

    return SimpleNamespace(
        store=SimpleNamespace(token="synthetic-session", _request=request),
        preferences=Mock(return_value=SimpleNamespace(version=1)), requests=requests,
    )


class Embeddings:
    def __init__(self):
        self.calls = []

    def embed(self, texts, *, purpose):
        self.calls.append((texts, purpose))
        vectors = [
            [1.0, 0.0] + [0.0] * 14 if purpose == "query" or "便當" in t
            else [0.0, 1.0] + [0.0] * 14 for t in texts
        ]
        return VectorBatch(model_id="synthetic-v1", dimensions=16, vectors=vectors)


def test_dinner_ranks_leftover_bento_without_shared_keywords_and_preserves_rules():
    unrelated, bento = post("下午想喝一杯手搖飲料"), post()
    svc, provider = service([unrelated, bento]), Embeddings()
    result = semantic_feed(svc, "想找晚餐", lambda: provider)
    assert result.status == "completed"
    assert str(result.items[0].id) == bento["id"]
    assert result.items[0].semantic_recommendation.similarity == 1
    assert result.items[0].recommendation.reasons[0].code == "remaining_seats"
    assert result.next_cursor is None
    assert all(kwargs["token"] == "synthetic-session" for _, kwargs in svc.requests)
    assert all(args[1] == "/rest/v1/rpc/core_recommendation_feed" for args, _ in svc.requests)
    assert all("author_id" not in t for texts, _ in provider.calls for t in texts)


@pytest.mark.parametrize("change", [
    {"status": "cancelled"}, {"joined": 5},
    {"deadline_at": datetime.now(UTC) - timedelta(seconds=1)},
    {"version": 2}, {"content": "內容已經改成另外一筆活動"},
    {"location": {"name": "不同地點", "latitude": 25.0, "longitude": 121.5}},
    {"price_per_person": "50"},
])
def test_post_inference_revalidation_removes_changed_closed_or_full_posts(change):
    original = post()
    result = semantic_feed(service([original], [{**original, **change}]), "晚餐", Embeddings)
    assert result.items == []


def test_new_block_or_hidden_post_disappears_during_inference():
    result = semantic_feed(service([post()], []), "晚餐", Embeddings)
    assert result.items == []


def test_ineligible_posts_never_reach_model():
    svc = service([post(status="expired"), post(joined=5)])
    factory = Mock()
    result = semantic_feed(svc, "晚餐", factory)
    assert result.status == "empty"
    factory.assert_not_called()


def test_preferences_changed_while_model_runs_requires_reload():
    svc = service([post()])
    svc.preferences.side_effect = [SimpleNamespace(version=1), SimpleNamespace(version=2)]
    with pytest.raises(HTTPException) as error:
        semantic_feed(svc, "晚餐", Embeddings)
    assert error.value.status_code == 409


def test_unavailable_model_returns_fresh_rules_without_invented_semantic_reasons():
    def unavailable():
        raise LocalModelError("local_model_busy")

    original = post()
    result = semantic_feed(service([original]), "晚餐", unavailable)
    assert result.status == "unavailable"
    assert len(result.items) == 1 and result.items[0].semantic_recommendation is None


def test_partial_batch_or_changed_embedding_space_discards_all_semantic_scores():
    class Changed(Embeddings):
        def embed(self, texts, *, purpose):
            batch = super().embed(texts, purpose=purpose)
            if purpose == "document":
                batch.model_id = "different-model"
            return batch

    result = semantic_feed(service([post()]), "晚餐", Changed)
    assert result.status == "unavailable" and result.items[0].semantic_recommendation is None


def test_busy_feed_does_not_queue_more_model_calls():
    assert FEED_SLOT.acquire(blocking=False)
    factory = Mock()
    try:
        assert semantic_feed(service([post()]), "晚餐", factory).status == "unavailable"
    finally:
        FEED_SLOT.release()
    factory.assert_not_called()


def test_pagination_bounds_pool_and_keeps_rpc_cursor_and_truncation():
    svc = service([])
    first, second = [post() for _ in range(32)], [post() for _ in range(32)]
    svc.store._request = Mock(side_effect=[
        {"items": first, "next_cursor": "page2"},
        {"items": second, "next_cursor": "page3"},
    ])
    items, truncated = eligible_pool(svc)
    assert len(items) == 64 and truncated
    assert svc.store._request.call_args.kwargs["body"] == {
        "args": {"limit": 32, "cursor": "page2"}
    }


@pytest.mark.parametrize("query", [" ", "x" * 161, "\0", "\ud800"])
def test_invalid_query_rejected(query):
    with pytest.raises(ValidationError):
        FeedSemanticRequest(query=query)


def test_semantic_endpoint_requires_auth_validates_input_and_does_not_write(client, monkeypatch):  # noqa: F811
    api, store = client
    factory = Mock(return_value=SimpleNamespace(embeddings=Embeddings()))
    monkeypatch.setattr(recommendations, "local_semantic_matcher", factory)
    assert api.post("/api/v1/recommendations/semantic", json={"query": "晚餐"}).status_code == 401
    for body in ({"query": " "}, {"query": "晚餐", "owner_id": str(uuid4())}):
        assert api.post(
            "/api/v1/recommendations/semantic", headers=HEADERS, json=body
        ).status_code == 422
    store._request.assert_not_called()
    record = post()

    def rpc(*args, **kwargs):
        if args[1].endswith("core_recommendation_feed"):
            return {"items": [record], "next_cursor": None}
        return {"version": 1}

    store._request.side_effect = rpc
    response = api.post(
        "/api/v1/recommendations/semantic", headers=HEADERS, json={"query": "想找晚餐"}
    )
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert response.json()["items"][0]["semantic_recommendation"]["similarity"] == 1
    assert factory.call_count == 1
    # Polling validation only reads the existing database feed, never invokes a model.
    response = api.post(
        "/api/v1/recommendations/semantic/validate", headers=HEADERS,
        json={"ids": [record["id"]], "preference_version": 1},
    )
    assert response.status_code == 200 and factory.call_count == 1
    assert api.post(
        "/api/v1/recommendations/semantic/validate", headers=HEADERS,
        json={"ids": [record["id"]], "preference_version": 0},
    ).status_code == 409
    assert api.post(
        "/api/v1/recommendations/semantic/validate", json={"ids": [record["id"]]}
    ).status_code == 401


def test_auth_failure_after_inference_is_not_downgraded_to_fallback():
    svc = service([post()])
    original = svc.store._request
    count = 0

    def expire(*args, **kwargs):
        nonlocal count
        count += 1
        if count > 1:
            raise HTTPException(401, "invalid_or_expired_session")
        return original(*args, **kwargs)

    svc.store._request = expire
    with pytest.raises(HTTPException) as error:
        semantic_feed(svc, "晚餐", Embeddings)
    assert error.value.status_code == 401
