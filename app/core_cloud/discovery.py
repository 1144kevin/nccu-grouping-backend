"""Index only the caller's confirmed signals; cloud retrieves private eligible pairs."""

from datetime import UTC, datetime

from app.stealth.ollama import LocalModelError
from app.stealth.provider_contracts import EmbeddingProvider


def document(signal) -> str:
    return (
        f"{signal.title}；{signal.category}；偏好：{'、'.join(signal.tags)}；"
        f"角色：{'、'.join(signal.roles)}"
    )


def discover(service, embeddings: EmbeddingProvider):
    now = datetime.now(UTC)
    signals = [s for s in service.signals().items if s.status == "active" and s.expires_at > now][
        :10
    ]
    if not signals:
        return {"created": 0, "indexed": 0, "requires_unanimous_confirmation": True}
    texts = [document(s) for s in signals]
    documents = embeddings.embed(texts, purpose="document")
    queries = embeddings.embed(texts, purpose="query")
    if (documents.model_id, documents.dimensions) != (queries.model_id, queries.dimensions):
        raise LocalModelError("embedding_space_changed")
    if (
        documents.dimensions != 512
        or len(documents.vectors) != len(signals)
        or len(queries.vectors) != len(signals)
    ):
        raise LocalModelError("invalid_embedding_response")

    def rpc(action, args):
        return service.store._request(
            "POST",
            "/rest/v1/rpc/core_semantic_discovery",
            body={"action": action, "args": args},
            token=service.store.token,
        )

    def items(vectors):
        return [
            {"signal_id": str(s.id), "version": s.version, "vector": v}
            for s, v in zip(signals, vectors, strict=True)
        ]

    # RPC rechecks ownership, version, expiry and all pair constraints after slow inference.
    indexed = rpc("index", {"model_id": documents.model_id, "items": items(documents.vectors)})
    result = rpc(
        "search",
        {"model_id": queries.model_id, "queries": items(queries.vectors), "max_results": 3},
    )
    return {**result, "indexed": indexed["indexed"]}
