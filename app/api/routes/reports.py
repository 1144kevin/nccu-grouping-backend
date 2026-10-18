from typing import Annotated

from fastapi import APIRouter, Header

from app.api.routes.engagement import EngagementDep
from app.core_cloud.engagement import ReportInput, ReportPage, ReportView

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("", response_model=ReportPage)
def reports(service: EngagementDep):
    return service.reports()


@router.post("", response_model=ReportView, status_code=201)
def create_report(
    value: ReportInput,
    service: EngagementDep,
    key: Annotated[str, Header(alias="Idempotency-Key", min_length=12, max_length=100)],
):
    return service.report(value, key)
