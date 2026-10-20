from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from app.core_cloud.moderation import AppealInput, AppealReview, Moderation, ReviewInput
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


@router.get("/reports/{report_id}/context")
def report_context(report_id: UUID, service: Operator):
    return service.store._request(
        "POST",
        "/rest/v1/rpc/core_moderation_context",
        body={"report_id": str(report_id)},
        token=service.store.token,
    )


@router.get("/appeals/mine")
def my_appeals(service: Operator):
    return service.appeals("mine")


@router.get("/decisions/mine")
def my_decisions(service: Operator):
    return service.appeals("decisions")


@router.post("/reports/{report_id}/appeal", status_code=201)
def appeal(report_id: UUID, value: AppealInput, service: Operator):
    return service.appeals("create", {"report_id": str(report_id), **value.model_dump(mode="json")})


@router.get("/appeals")
def appeals(service: Operator):
    return service.appeals("list")


@router.post("/appeals/{appeal_id}/review")
def review_appeal(appeal_id: UUID, value: AppealReview, service: Operator):
    return service.appeals("review", {"appeal_id": str(appeal_id), **value.model_dump(mode="json")})
