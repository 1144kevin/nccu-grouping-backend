from typing import Annotated

from fastapi import APIRouter, HTTPException, Query

from app.api.routes.engagement import EngagementDep
from app.core_cloud.feed_semantic import (
    FeedSemanticPage,
    FeedSemanticRequest,
    FeedValidationRequest,
    eligible_pool,
    semantic_feed,
)
from app.core_cloud.taiwan_geo import filter_posts_by_regions
from app.core_local.schemas import RecommendationPage
from app.stealth.provider_registry import local_semantic_matcher

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.post("/semantic", response_model=FeedSemanticPage)
def semantic_recommendations(value: FeedSemanticRequest, service: EngagementDep):
    return semantic_feed(
        service, value.query, lambda: local_semantic_matcher(service.store.settings).embeddings
    )


@router.post("/semantic/validate", response_model=RecommendationPage)
def validate_semantic_recommendations(value: FeedValidationRequest, service: EngagementDep):
    preferences = service.preferences()
    if preferences.version != value.preference_version:
        raise HTTPException(409, "recommendations_changed_reload")
    allowed, _ = eligible_pool(service, getattr(preferences, "preferred_tags", None) or [])
    ids = set(value.ids)
    return RecommendationPage(items=[p for p in allowed if p.id in ids])


@router.get("", response_model=RecommendationPage)
def recommendations(
    service: EngagementDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 30,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
):
    page = RecommendationPage.model_validate(
        service.store._request(
            "POST",
            "/rest/v1/rpc/core_recommendation_feed",
            body={"args": {"limit": limit, "cursor": cursor}},
            token=service.store.token,
        )
    )
    preferences = service.preferences()
    filtered = filter_posts_by_regions(page.items, preferences.preferred_tags)
    return page.model_copy(update={"items": filtered})
