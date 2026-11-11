from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response

from app.core_cloud.store import SupabaseCoreStore
from app.core_local.dependencies import StoreDep, UserDep
from app.core_local.schemas import Input, OrderActivityPage

router = APIRouter(prefix="/notifications", tags=["notifications"])


class ReadOrders(Input):
    post_id: UUID | None = None


class ReadPenalty(Input):
    id: UUID


@router.get("/order-events")
def order_events(store: StoreDep, _: UserDep, response: Response):
    response.headers["Cache-Control"] = "no-store"
    if not isinstance(store, SupabaseCoreStore):
        raise HTTPException(503, "activity_requires_supabase")
    return store._request(
        "POST",
        "/rest/v1/rpc/core_order_workflow_notices",
        body={"action": "list"},
        token=store.token,
    )


@router.post("/order-events/read")
def read_order_events(store: StoreDep, _: UserDep):
    if not isinstance(store, SupabaseCoreStore):
        raise HTTPException(503, "activity_requires_supabase")
    return store._request(
        "POST",
        "/rest/v1/rpc/core_order_workflow_notices",
        body={"action": "read"},
        token=store.token,
    )


@router.get("/unread")
def unread_notifications(store: StoreDep, _: UserDep, response: Response):
    response.headers["Cache-Control"] = "no-store"
    if not isinstance(store, SupabaseCoreStore):
        raise HTTPException(503, "activity_requires_supabase")
    return store._request(
        "POST", "/rest/v1/rpc/core_notification_badge", body={}, token=store.token
    )


@router.get("/penalties")
def penalty_notices(store: StoreDep, _: UserDep, response: Response):
    response.headers["Cache-Control"] = "no-store"
    if not isinstance(store, SupabaseCoreStore):
        raise HTTPException(503, "activity_requires_supabase")
    return store._request(
        "POST",
        "/rest/v1/rpc/core_penalty_notices",
        body={"action": "list", "args": {}},
        token=store.token,
    )


@router.post("/penalties/read")
def read_penalty(value: ReadPenalty, store: StoreDep, _: UserDep):
    if not isinstance(store, SupabaseCoreStore):
        raise HTTPException(503, "activity_requires_supabase")
    return store._request(
        "POST",
        "/rest/v1/rpc/core_penalty_notices",
        body={"action": "read", "args": {"id": str(value.id)}},
        token=store.token,
    )


@router.post("/read")
def read_orders(value: ReadOrders, store: StoreDep, _: UserDep):
    if not isinstance(store, SupabaseCoreStore):
        raise HTTPException(503, "activity_requires_supabase")
    return store._request(
        "POST",
        "/rest/v1/rpc/core_notifications",
        body={"action": "read_orders", "args": value.model_dump(mode="json")},
        token=store.token,
    )


@router.get("", response_model=OrderActivityPage)
def order_activity(
    store: StoreDep,
    _: UserDep,
    response: Response,
    limit: Annotated[int, Query(ge=1, le=50)] = 30,
    cursor: Annotated[str | None, Query(max_length=500)] = None,
) -> OrderActivityPage:
    response.headers["Cache-Control"] = "no-store"
    if not isinstance(store, SupabaseCoreStore):
        raise HTTPException(503, "activity_requires_supabase")
    return store.order_activity(limit, cursor)
