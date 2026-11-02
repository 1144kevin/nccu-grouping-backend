from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.core_cloud.store import SupabaseCoreStore
from app.core_local.dependencies import StoreDep, UserDep
from app.core_local.schemas import Input, MessageInput, PostView

router = APIRouter(tags=["inbox"])


def social(store, action, args):
    if not isinstance(store, SupabaseCoreStore):
        raise HTTPException(503, "social_requires_supabase")
    return store._request(
        "POST", "/rest/v1/rpc/core_social", body={"action": action, "args": args}, token=store.token
    )


class Thread(BaseModel):
    id: UUID
    kind: Literal["group", "direct", "stealth"]
    post: PostView | None = None
    title: str | None = None
    preview: str | None = None
    peer_id: UUID
    peer_name: str
    peer_badge: str
    peer_level: int = Field(default=1, ge=1, le=5)
    last_message: dict | None = None
    updated_at: str
    unread_count: int = Field(ge=0)
    pinned: bool
    inbox: Literal["primary", "general"]
    muted: bool
    deleted: bool


class InboxPage(BaseModel):
    items: list[Thread]
    next_cursor: str | None = None
    total_unread: int = Field(ge=0)


class ThreadPreference(Input):
    pinned: bool | None = Field(default=None, strict=True)
    muted: bool | None = Field(default=None, strict=True)
    deleted: bool | None = Field(default=None, strict=True)
    inbox: Literal["primary", "general"] | None = None


class StartDirect(Input):
    post_id: UUID


class ReadDirect(Input):
    message_id: UUID


@router.get("/inbox", response_model=InboxPage)
def inbox(
    store: StoreDep,
    _: UserDep,
    kind: Literal["all", "primary", "group", "direct"] = "all",
    limit: Annotated[int, Query(ge=1, le=50)] = 30,
    cursor: Annotated[str | None, Query(max_length=1000)] = None,
    deleted: bool = False,
):
    return social(
        store, "list", {"kind": kind, "limit": limit, "cursor": cursor, "deleted": deleted}
    )


@router.patch("/inbox/{kind}/{thread_id}", response_model=Thread)
def preference(
    kind: Literal["group", "direct", "stealth"],
    thread_id: UUID,
    value: ThreadPreference,
    store: StoreDep,
    _: UserDep,
):
    return social(
        store,
        "preference",
        {"kind": kind, "thread_id": str(thread_id), **value.model_dump(exclude_none=True)},
    )


@router.post("/direct-messages", response_model=Thread)
def start(value: StartDirect, store: StoreDep, _: UserDep):
    return social(store, "start", value.model_dump(mode="json"))


@router.get("/direct-messages/{thread_id}")
def direct_messages(
    thread_id: UUID,
    store: StoreDep,
    _: UserDep,
    limit: Annotated[int, Query(ge=1, le=50)] = 30,
    cursor: Annotated[str | None, Query(max_length=1000)] = None,
):
    return social(
        store, "messages", {"thread_id": str(thread_id), "limit": limit, "cursor": cursor}
    )


@router.post("/direct-messages/{thread_id}/messages", status_code=201)
def direct_send(
    thread_id: UUID,
    value: MessageInput,
    store: StoreDep,
    _: UserDep,
    idempotency_key: Annotated[str, Header(min_length=12, max_length=100)],
):
    return social(
        store,
        "send",
        {"thread_id": str(thread_id), "content": value.content, "client_key": idempotency_key},
    )


@router.post("/direct-messages/{thread_id}/read")
def direct_read(thread_id: UUID, value: ReadDirect, store: StoreDep, _: UserDep):
    return social(store, "read", {"thread_id": str(thread_id), "message_id": str(value.message_id)})


@router.get("/orders/{post_id}/summary")
def order(post_id: UUID, store: StoreDep, _: UserDep):
    return social(store, "order", {"post_id": str(post_id)})


@router.post("/orders/{post_id}/handoff")
def handoff(post_id: UUID, store: StoreDep, _: UserDep):
    return social(store, "handoff", {"post_id": str(post_id)})


@router.get("/account/experience")
def experience(store: StoreDep, _: UserDep):
    return social(store, "experience", {})
