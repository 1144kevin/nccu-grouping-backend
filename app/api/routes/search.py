from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import AwareDatetime, BaseModel, Field

from app.api.routes.engagement import EngagementDep

router = APIRouter(prefix="/search", tags=["search"])


class SearchItem(BaseModel):
    level: int = Field(default=1, ge=1, le=5)
    badge: str = "🍱"
    id: UUID
    kind: Literal["user", "message", "history"]
    title: str
    detail: str
    post_id: UUID | None = None
    created_at: AwareDatetime


class SearchPage(BaseModel):
    items: list[SearchItem]
    next_cursor: str | None = None


@router.get("", response_model=SearchPage)
def search(
    service: EngagementDep,
    scope: Literal["users", "messages", "history"],
    q: Annotated[str, Query(min_length=2, max_length=80)],
    limit: Annotated[int, Query(ge=1, le=50)] = 30,
    cursor: Annotated[str | None, Query(max_length=700)] = None,
):
    return SearchPage.model_validate(
        service.store._request(
            "POST",
            "/rest/v1/rpc/core_search",
            body={"args": {"scope": scope, "q": q, "limit": limit, "cursor": cursor}},
            token=service.store.token,
        )
    )
