"""Index only the caller's confirmed signals; cloud retrieves private eligible pairs."""

from datetime import UTC, datetime

from app.core_cloud.match_judge import judge_candidate_pair
from app.stealth.ollama import LocalModelError
from app.stealth.provider_contracts import EmbeddingProvider


def document(signal) -> str:
    return (
        f"需求：{signal.title}；類型：{signal.category}；地點：{signal.location_id}；"
        f"時間：{signal.starts_at.isoformat()} 至 {signal.ends_at.isoformat()}；"
        f"預算：{signal.budget_max_twd if signal.budget_max_twd is not None else '未指定'}；"
        f"偏好：{'、'.join(signal.tags)}；角色：{'、'.join(signal.roles)}；"
        f"參與偏好：{signal.participation_preference or '未指定'}"
    )


def index_active_signals(service, embeddings: EmbeddingProvider, *, require_query=False):
    """Rebuild only the current owner's active signals without searching or matching."""
    now = datetime.now(UTC)
    signals = [s for s in service.signals().items if s.status == "active" and s.expires_at > now][
        :10
    ]
    if not signals:
        return signals, None, 0
    texts = [document(s) for s in signals]
    documents = embeddings.embed(texts, purpose="document")
    queries = embeddings.embed(texts, purpose="query") if require_query else None
    if queries and (documents.model_id, documents.dimensions) != (
        queries.model_id,
        queries.dimensions,
    ):
        raise LocalModelError("embedding_space_changed")
    if (
        documents.dimensions != 512
        or len(documents.vectors) != len(signals)
        or (queries is not None and len(queries.vectors) != len(signals))
    ):
        raise LocalModelError("invalid_embedding_response")
    items = [
        {"signal_id": str(s.id), "version": s.version, "vector": v}
        for s, v in zip(signals, documents.vectors, strict=True)
    ]
    indexed = service.store._request(
        "POST",
        "/rest/v1/rpc/core_semantic_discovery",
        body={"action": "index", "args": {"model_id": documents.model_id, "items": items}},
        token=service.store.token,
    )
    return signals, queries if require_query else documents, indexed["indexed"]


def _signal_locked_in_chat(service, signals) -> bool:
    matches_fn = getattr(service, "matches", None)
    if not callable(matches_fn):
        return False
    try:
        items = matches_fn().items
    except Exception:
        return False
    if not isinstance(items, list):
        return False
    return any(
        getattr(m, "status", None) == "ready"
        or (
            getattr(m, "status", None) == "formed"
            and any(
                s.title == getattr(m, "my_signal_title", None)
                and getattr(m, "created_at", s.created_at) >= s.created_at
                for s in signals
            )
        )
        for m in items
    )


def discover(
    service,
    embeddings: EmbeddingProvider,
    maximum=1,
    *,
    settings=None,
    transport=None,
):
    now = datetime.now(UTC)
    active_signals = [s for s in service.signals().items if s.status == "active" and s.expires_at > now][
        :10
    ]
    if not active_signals or _signal_locked_in_chat(service, active_signals):
        return {"created": 0, "indexed": 0, "is_llm": True, "requires_unanimous_confirmation": True}
    signals, queries, indexed = index_active_signals(service, embeddings, require_query=True)
    if not signals:
        return {"created": 0, "indexed": 0, "is_llm": True, "requires_unanimous_confirmation": True}
    items = [
        {"signal_id": str(s.id), "version": s.version, "vector": v}
        for s, v in zip(signals, queries.vectors, strict=True)
    ]
    raw_candidates = service.store._request(
        "POST",
        "/rest/v1/rpc/core_semantic_discovery",
        body={
            "action": "candidates",
            "args": {
                "model_id": queries.model_id,
                "queries": items,
                "max_results": min(maximum, 1),
            },
        },
        token=service.store.token,
    )
    candidates = raw_candidates.get("candidates") or []
    if not candidates:
        return {
            "created": 0,
            "indexed": indexed,
            "model_id": queries.model_id,
            "is_llm": True,
            "requires_unanimous_confirmation": True,
        }

    runtime_settings = settings or getattr(service.store, "settings", None)
    # Filter first with fail-closed LLM compatibility judgment, then rank qualified candidates.
    qualified = []
    for candidate in candidates:
        verdict = judge_candidate_pair(
            candidate, settings=runtime_settings, transport=transport
        )
        if verdict.verdict != "compatible" or not verdict.reasons or verdict.conflicts:
            continue
        qualified.append((verdict, candidate))

    if not qualified:
        return {
            "created": 0,
            "indexed": indexed,
            "model_id": queries.model_id,
            "is_llm": True,
            "requires_unanimous_confirmation": True,
        }

    qualified.sort(
        key=lambda pair: (
            pair[0].score,
            float(pair[1].get("similarity") or 0.0),
            str(pair[1].get("fingerprint") or ""),
        ),
        reverse=True,
    )

    created = 0
    limit = min(maximum, 1)
    for verdict, candidate in qualified:
        committed = service.store._request(
            "POST",
            "/rest/v1/rpc/core_semantic_discovery",
            body={
                "action": "commit",
                "args": {
                    "signal_id": candidate["own_signal_id"],
                    "candidate_signal_id": candidate["candidate_signal_id"],
                    "score": verdict.score,
                    "reasons": verdict.reasons,
                    "unknowns": verdict.unknowns,
                    "is_llm": True,
                },
            },
            token=service.store.token,
        )
        created += int(committed.get("created") or 0)
        if created >= limit:
            break

    return {
        "created": created,
        "indexed": indexed,
        "model_id": queries.model_id,
        "is_llm": True,
        "requires_unanimous_confirmation": True,
    }
