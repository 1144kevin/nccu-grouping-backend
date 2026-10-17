from uuid import UUID

from fastapi import APIRouter

from app.core_local.dependencies import StoreDep, UserDep
from app.core_local.schemas import DecisionInput, JoinView

router = APIRouter(tags=["join-requests"])


@router.patch("/join-requests/{join_id}", response_model=JoinView)
def decide(join_id: UUID, value: DecisionInput, store: StoreDep, user: UserDep) -> JoinView:
    return store.decide(user.id, join_id, value.status)
