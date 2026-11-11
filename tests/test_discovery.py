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
        return {"indexed": 1} if kwargs["body"]["action"] == "index" else {"created": 1}

    service = SimpleNamespace(
        signals=lambda: SimpleNamespace(items=[signal]),
        store=SimpleNamespace(token="synthetic-user", _request=request),
    )

    class Provider:
        def embed(self, texts, *, purpose):
            assert len(texts) == 1 and all(
                x in texts[0] for x in ("想喝熱湯", "政大正門", "100", "熱食")
            )
            return VectorBatch(model_id="synthetic@v1", dimensions=512, vectors=[[1] + [0] * 511])

    result = discover(service, Provider())
    assert result == {"created": 1, "indexed": 1}
    assert all(c["token"] == "synthetic-user" for c in calls)
    assert calls[1]["body"]["args"]["queries"][0]["version"] == 2
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
