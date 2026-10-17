from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response

from app.core_local.dependencies import StoreDep, UserDep
from app.core_local.schemas import PostPage, PublicProfileView

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/{user_id}", response_model=PublicProfileView)
def public_profile(
    user_id: UUID, store: StoreDep, _: UserDep, response: Response
) -> PublicProfileView:
    response.headers["Cache-Control"] = "no-store"
    return store.public_profile(user_id)


@router.get("/{user_id}/posts", response_model=PostPage)
def public_posts(
    user_id: UUID,
    store: StoreDep,
    actor: UserDep,
    response: Response,
    limit: Annotated[int, Query(ge=1, le=50)] = 30,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
) -> PostPage:
    response.headers["Cache-Control"] = "no-store"
    return store.user_posts(actor.id, user_id, limit, cursor)
