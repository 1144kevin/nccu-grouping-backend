from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from app.core_cloud.moderation import Moderation, ReviewInput
from app.core_cloud.store import SupabaseCoreStore
from app.core_local.dependencies import StoreDep, UserDep

router = APIRouter(prefix="/moderation", tags=["moderation"])


def moderator(store: StoreDep, _: UserDep, response: Response):
    response.headers["Cache-Control"] = "no-store"
    if not isinstance(store, SupabaseCoreStore) or not store.settings.core_moderation_enabled:
        raise HTTPException(503, "moderation_not_enabled")
    return Moderation(store)


Operator = Annotated[Moderation, Depends(moderator)]


@router.get("/access")
def access(service: Operator):
    return service.store._request(
        "POST", "/rest/v1/rpc/core_moderation_access", body={}, token=service.store.token
    )


@router.get("/reports")
def reports(service: Operator):
    # Database checks an explicit operator allowlist on every operation.
    return service.rpc("list")


@router.post("/reports/{report_id}/review")
def review(report_id: UUID, value: ReviewInput, service: Operator):
    return service.review(report_id, value)
