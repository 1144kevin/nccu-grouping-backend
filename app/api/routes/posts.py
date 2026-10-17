from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, Query

from app.core_local.dependencies import StoreDep, UserDep
from app.core_local.schemas import JoinInput, JoinView, PostEditInput, PostInput, PostPage, PostView

router = APIRouter(prefix="/posts", tags=["posts"])


@router.get("", response_model=PostPage)
def list_posts(
    store: StoreDep,
    user: UserDep,
    scope: Literal["feed", "hosted", "participating", "history"] = "feed",
    limit: Annotated[int, Query(ge=1, le=50)] = 30,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
    q: Annotated[str, Query(max_length=100)] = "",
    active_only: bool = False,
) -> PostPage:
    return store.posts(user.id, scope, limit, cursor, q, active_only)


@router.post("", response_model=PostView, status_code=201)
def create_post(
    value: PostInput,
    store: StoreDep,
    user: UserDep,
    idempotency_key: Annotated[str, Header(min_length=12, max_length=100)],
) -> PostView:
    return store.create_post(user.id, value, idempotency_key)


@router.get("/{post_id}", response_model=PostView)
def get_post(post_id: UUID, store: StoreDep, user: UserDep) -> PostView:
    return store.post(user.id, post_id)


@router.patch("/{post_id}", response_model=PostView)
def edit_post(post_id: UUID, value: PostEditInput, store: StoreDep, user: UserDep) -> PostView:
    return store.edit_post(user.id, post_id, value)


@router.post("/{post_id}/join-requests", response_model=JoinView, status_code=201)
def request_join(post_id: UUID, value: JoinInput, store: StoreDep, user: UserDep) -> JoinView:
    return store.request_join(user.id, post_id, value.expected_version)


@router.get("/{post_id}/join-requests", response_model=list[JoinView])
def list_join_requests(post_id: UUID, store: StoreDep, user: UserDep) -> list[JoinView]:
    return store.join_requests(user.id, post_id)


@router.delete("/{post_id}/membership", response_model=PostView)
def withdraw(post_id: UUID, store: StoreDep, user: UserDep) -> PostView:
    return store.withdraw(user.id, post_id)


@router.post("/{post_id}/cancel", response_model=PostView)
def cancel(post_id: UUID, store: StoreDep, user: UserDep) -> PostView:
    return store.cancel(user.id, post_id)
