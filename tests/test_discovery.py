from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core_cloud.discovery import discover, index_active_signals
from app.stealth.ollama import LocalModelError
from app.stealth.provider_contracts import VectorBatch


def test_cloud_discovery_indexes_only_own_active_signals_and_uses_session():
    calls = []
    signal = SimpleNamespace(
        id=uuid4(),
        version=2,
        status="active",
        title="想喝熱湯",
        category="meal",
        tags=["熱食"],
        roles=["share"],
        location_id="政大正門",
        starts_at=datetime(2026, 1, 1, tzinfo=UTC),
        ends_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
        budget_max_twd=100,
        participation_preference=None,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    def request(*args, **kwargs):
        calls.append(kwargs)
        action = kwargs["body"]["action"]
        if action == "index":
            return {"indexed": 1}
        if action == "candidates":
            return {
                "candidates": [
                    {
                        "fingerprint": "fp-1",
                        "own_signal_id": str(signal.id),
                        "candidate_signal_id": "00000000-0000-0000-0000-000000000099",
                        "similarity": 0.88,
                        "own": {"title": "想喝熱湯", "category": "meal"},
                        "partner": {"title": "想喝熱湯一起分", "category": "meal"},
                    }
                ]
            }
        return {"created": 1}

    class FakeTransport:
        def request(self, path, payload, **kwargs):
            return {
                "output_text": '{"verdict":"compatible","score":88,"reasons":["雙方都想喝熱湯，可一起拼單"],"conflicts":[],"unknowns":[]}'
            }

    settings = SimpleNamespace(
        stealth_ai_provider="openai",
        environment="local",
        core_cloud_enabled=False,
        local_openai_api_key="sk-test",
    )

    service = SimpleNamespace(
        signals=lambda: SimpleNamespace(items=[signal]),
        store=SimpleNamespace(token="synthetic-user", settings=settings, _request=request),
    )

    class Provider:
        def embed(self, texts, *, purpose):
            assert len(texts) == 1 and all(
                x in texts[0] for x in ("想喝熱湯", "政大正門", "100", "熱食")
            )
            return VectorBatch(model_id="synthetic@v1", dimensions=512, vectors=[[1] + [0] * 511])

    result = discover(service, Provider(), settings=settings, transport=FakeTransport())
    assert result == {
        "created": 1,
        "indexed": 1,
        "is_llm": True,
        "model_id": "synthetic@v1",
        "requires_unanimous_confirmation": True,
    }
    assert all(c["token"] == "synthetic-user" for c in calls)
    assert [c["body"]["action"] for c in calls] == ["index", "candidates", "commit"]
    assert calls[1]["body"]["args"]["queries"][0]["version"] == 2
    assert calls[2]["body"]["args"]["is_llm"] is True
    assert calls[2]["body"]["args"]["score"] == 88
    calls.clear()
    signals, batch, count = index_active_signals(service, Provider())
    assert signals == [signal] and batch.model_id == "synthetic@v1" and count == 1
    assert [call["body"]["action"] for call in calls] == ["index"]


def test_empty_discovery_never_calls_model():
    assert (
        discover(SimpleNamespace(signals=lambda: SimpleNamespace(items=[])), None)["created"] == 0
    )


def test_discovery_rejects_changed_embedding_space_before_cloud_write():
    signal = SimpleNamespace(
        id=uuid4(),
        version=1,
        status="active",
        title="熱湯",
        category="meal",
        tags=[],
        roles=[],
        location_id="政大正門",
        starts_at=datetime(2026, 1, 1, tzinfo=UTC),
        ends_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
        budget_max_twd=None,
        participation_preference=None,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    class Changed:
        def embed(self, texts, *, purpose):
            return VectorBatch(model_id=purpose, dimensions=512, vectors=[[1] + [0] * 511])

    with pytest.raises(LocalModelError, match="embedding_space_changed"):
        discover(SimpleNamespace(signals=lambda: SimpleNamespace(items=[signal])), Changed())
