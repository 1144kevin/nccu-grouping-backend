from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.routes.engagement import EngagementDep
from app.core_cloud.user_blocks import BlockList, BlockResult, UserBlocks
from app.core_local.dependencies import UserDep

router = APIRouter(prefix="/account", tags=["account-blocks"])


def safety(service: EngagementDep, user: UserDep):
    return UserBlocks(service.store, user.id)


SafetyDep = Annotated[UserBlocks, Depends(safety)]


@router.get("/blocks", response_model=BlockList)
def blocks(service: SafetyDep):
    return service.blocks()


@router.put("/blocks/{user_id}", response_model=BlockResult)
def block(user_id: UUID, service: SafetyDep):
    return service.block(user_id, True)


@router.delete("/blocks/{user_id}", response_model=BlockResult)
def unblock(user_id: UUID, service: SafetyDep):
    return service.block(user_id, False)
