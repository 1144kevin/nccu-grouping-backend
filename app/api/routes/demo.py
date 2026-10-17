from fastapi import APIRouter, HTTPException, Response

from app.core_cloud.store import SupabaseCoreStore
from app.core_local.dependencies import StoreDep, UserDep
from app.core_local.schemas import DemoCleanupView, ServerClockView

router = APIRouter(prefix="/development/demo", tags=["development"])
clock_router = APIRouter(prefix="/development", tags=["development"])


@clock_router.get("/clock", response_model=ServerClockView)
def server_clock(store: StoreDep, _: UserDep, response: Response) -> ServerClockView:
    response.headers["Cache-Control"] = "no-store"
    return store.clock()


@router.delete("", response_model=DemoCleanupView)
def clear_demo(store: StoreDep, _: UserDep) -> DemoCleanupView:
    if not isinstance(store, SupabaseCoreStore):
        raise HTTPException(503, "demo_requires_supabase")
    return store.clear_demo()
