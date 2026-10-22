"""Explicit embedding reranking of a bounded, session-authorized recommendation pool."""

import threading
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import HTTPException
from pydantic import Field, field_validator

from app.core_local.schemas import FeedSemanticInfo, RecommendationPage, RecommendedPostView
from app.stealth.ollama import LocalModelError
from app.stealth.schemas import StrictModel

POOL_LIMIT = 64
PAGE_SIZE = 32
FEED_SLOT = threading.BoundedSemaphore(1)


class FeedSemanticRequest(StrictModel):
    query: str = Field(min_length=1, max_length=160)

    @field_validator("query")
    @classmethod
    def safe_query(cls, value):
        if not value.strip() or "\0" in value or any(0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise ValueError("invalid semantic query")
        return value.strip()


class FeedSemanticPage(RecommendationPage):
    status: Literal["completed", "unavailable", "empty"]
    candidate_count: int
    pool_limit: int = POOL_LIMIT
    truncated: bool = False
    preference_version: int


class FeedValidationRequest(StrictModel):
    ids: list[UUID] = Field(min_length=1, max_length=POOL_LIMIT)
    preference_version: int = Field(ge=0, strict=True)


def eligible_pool(service):
    """Use the existing RPC, including its blocks, hidden posts and session checks."""
    posts, cursor = [], None
    for _ in range(POOL_LIMIT // PAGE_SIZE):
        page = RecommendationPage.model_validate(
            service.store._request(
                "POST",
                "/rest/v1/rpc/core_recommendation_feed",
                body={"args": {"limit": PAGE_SIZE, "cursor": cursor}},
                token=service.store.token,
            )
        )
        posts.extend(page.items)
        cursor = page.next_cursor
        if not cursor:
            break
    now = datetime.now(UTC)
    # Defense in depth; admission remains an authoritative database transaction.
    unique = {
        p.id: p
        for p in posts
        if p.status == "open" and p.joined < p.capacity and p.deadline_at > now
    }
    return list(unique.values()), bool(cursor)


def document(post: RecommendedPostView):
    # No chats, private signals, author identifiers, or inferred food safety claims.
    return f"{post.content}；標籤：{'、'.join(post.tags)}；地點：{post.location.name}"[:2000]


def unchanged(a, b):
    return (
        a.version == b.version
        and document(a) == document(b)
        and a.location == b.location
        and a.deadline_at == b.deadline_at
        and a.price_per_person == b.price_per_person
        and a.capacity == b.capacity
    )


def semantic_feed(service, query: str, provider_factory):
    preferences = service.preferences()
    before, truncated = eligible_pool(service)
    if not before:
        return FeedSemanticPage(
            items=[], status="empty", candidate_count=0, truncated=truncated,
            preference_version=preferences.version,
        )
    similarities = {}
    model = None
    acquired = FEED_SLOT.acquire(blocking=False)
    try:
        if not acquired:
            raise LocalModelError("local_model_busy")
        provider = provider_factory()
        q = provider.embed([query], purpose="query")
        if len(q.vectors) != 1:
            raise LocalModelError("invalid_embedding_response")
        for start in range(0, len(before), 16):
            batch = before[start : start + 16]
            vectors = provider.embed([document(p) for p in batch], purpose="document")
            if (q.model_id, q.dimensions) != (vectors.model_id, vectors.dimensions):
                raise LocalModelError("embedding_space_changed")
            if len(vectors.vectors) != len(batch):
                raise LocalModelError("invalid_embedding_response")
            for post, vector in zip(batch, vectors.vectors, strict=True):
                similarity = sum(a * b for a, b in zip(q.vectors[0], vector, strict=True))
                similarities[post.id] = round(
                    max(-1.0, min(1.0, similarity)),
                    6,
                )
        model = q.model_id
    except (LocalModelError, HTTPException) as error:
        # Never downgrade auth or stale-snapshot failures to apparently successful results.
        if isinstance(error, HTTPException) and error.status_code != 503:
            raise
        similarities.clear()
    finally:
        if acquired:
            FEED_SLOT.release()

    # Re-read using the caller's session AFTER inference, including on fallback.
    fresh, fresh_truncated = eligible_pool(service)
    if service.preferences().version != preferences.version:
        raise HTTPException(409, "recommendations_changed_reload")
    previous = {p.id: p for p in before}
    items = [p for p in fresh if p.id in previous and unchanged(previous[p.id], p)]
    if similarities:
        for post in items:
            similarity = similarities[post.id]
            rules = post.recommendation.score if post.recommendation else 0
            post.semantic_recommendation = FeedSemanticInfo(
                similarity=similarity,
                model_id=model,
                combined_score=round(0.4 * rules + 60 * max(0, similarity), 3),
            )
        items.sort(key=lambda p: -p.semantic_recommendation.combined_score)
    return FeedSemanticPage(
        items=items,
        status=("completed" if similarities else "unavailable") if items else "empty",
        candidate_count=len(before),
        truncated=truncated or fresh_truncated,
        preference_version=preferences.version,
    )
