from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.routes.engagement import EngagementDep
from app.core_cloud.account_deletion import (
    AccountDeletion,
    Deleted,
    DeletionConfirm,
    DeletionPreview,
    DeletionStatus,
)
from app.core_local.dependencies import UserDep

router = APIRouter(prefix="/account", tags=["account-deletion"])


def safety(service: EngagementDep, user: UserDep):
    return AccountDeletion(service.store, user.id)


SafetyDep = Annotated[AccountDeletion, Depends(safety)]


@router.get("/deletion", response_model=DeletionStatus)
def deletion_status(service: SafetyDep):
    return service.status()


@router.post("/deletion-preview", response_model=DeletionPreview)
def deletion_preview(service: SafetyDep):
    return service.preview()


@router.delete("", response_model=Deleted)
def delete_account(value: DeletionConfirm, service: SafetyDep):
    return service.delete(value)
