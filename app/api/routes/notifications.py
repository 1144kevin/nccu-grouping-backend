from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response

from app.core_cloud.store import SupabaseCoreStore
from app.core_local.dependencies import StoreDep, UserDep
from app.core_local.schemas import OrderActivityPage

router = APIRouter(prefix="/notifications", tags=["notifications"])


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
