from typing import Annotated

from fastapi import APIRouter, Query

from app.api.routes.engagement import EngagementDep
from app.core_local.schemas import PostPage

router = APIRouter(prefix="/recommendations", tags=["recommendations"])


@router.get("", response_model=PostPage)
def recommendations(
    service: EngagementDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 30,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
):
    return PostPage.model_validate(
        service.store._request(
            "POST",
            "/rest/v1/rpc/core_recommendation_feed",
            body={"args": {"limit": limit, "cursor": cursor}},
            token=service.store.token,
        )
    )
