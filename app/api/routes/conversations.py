from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Query, Response

from app.core_cloud.store import SupabaseCoreStore
from app.core_local.dependencies import StoreDep, UserDep
from app.core_local.schemas import (
    ChatActivity,
    ChatPage,
    ChatReadInput,
    ChatReadView,
    ConversationPage,
    MessageInput,
    MessageView,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])


def cloud_chat(store) -> SupabaseCoreStore:
    if not isinstance(store, SupabaseCoreStore):
        raise HTTPException(503, "chat_requires_supabase")
    return store


@router.get("/activity", response_model=ChatActivity)
def activity(store: StoreDep, _: UserDep, response: Response) -> ChatActivity:
    response.headers["Cache-Control"] = "no-store"
    return cloud_chat(store).chat_activity()


@router.post("/{post_id}/read", response_model=ChatReadView)
def read_chat(
    post_id: UUID, value: ChatReadInput, store: StoreDep, _: UserDep, response: Response
) -> ChatReadView:
    response.headers["Cache-Control"] = "no-store"
    return cloud_chat(store).read_chat(post_id, value.message_id)


@router.get("", response_model=ConversationPage)
def conversations(
    store: StoreDep,
    _: UserDep,
    response: Response,
    limit: Annotated[int, Query(ge=1, le=50)] = 30,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
) -> ConversationPage:
    response.headers["Cache-Control"] = "no-store"
    return cloud_chat(store).conversations(limit, cursor)


@router.get("/{post_id}/messages", response_model=ChatPage)
def messages(
    post_id: UUID,
    store: StoreDep,
    _: UserDep,
    response: Response,
    limit: Annotated[int, Query(ge=1, le=50)] = 30,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
) -> ChatPage:
    response.headers["Cache-Control"] = "no-store"
    return cloud_chat(store).messages(post_id, limit, cursor)


@router.post("/{post_id}/messages", response_model=MessageView, status_code=201)
def send_message(
    post_id: UUID,
    value: MessageInput,
    store: StoreDep,
    _: UserDep,
    response: Response,
    idempotency_key: Annotated[str, Header(min_length=12, max_length=100)],
) -> MessageView:
    response.headers["Cache-Control"] = "no-store"
    return cloud_chat(store).send_message(post_id, value, idempotency_key)
