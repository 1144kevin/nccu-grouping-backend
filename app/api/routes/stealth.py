from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query, Response

from app.stealth.ai import DraftProvider
from app.stealth.dependencies import current_local_user, get_draft_provider, get_local_service
from app.stealth.ollama import LocalModelError
from app.stealth.provider_registry import local_semantic_matcher
from app.stealth.schemas import (
    Agent,
    AgentInput,
    DecisionInput,
    DraftInput,
    DraftOutput,
    Formation,
    FormationInput,
    MatchRunInput,
    MatchRunOutput,
    ProposalPage,
    ProposalView,
    Signal,
    SignalInput,
    SignalPage,
)
from app.stealth.semantic_local import SemanticMatchRun, run_local_semantic
from app.stealth.service import StealthError, StealthService, utc_now

router = APIRouter(prefix="/stealth", tags=["stealth-local"])
Actor = Annotated[UUID, Depends(current_local_user)]
Service = Annotated[StealthService, Depends(get_local_service)]


@router.get("/agent", response_model=Agent | None)
def get_agent(actor: Actor, service: Service):
    return service.get_agent(actor)


@router.post("/blocks/{user_id}", status_code=204)
def block_user(user_id: UUID, actor: Actor, service: Service):
    service.block_user(actor, user_id, utc_now())
    return Response(status_code=204)


@router.delete("/blocks/{user_id}", status_code=204)
def unblock_user(user_id: UUID, actor: Actor, service: Service):
    service.unblock_user(actor, user_id, utc_now())
    return Response(status_code=204)


@router.put("/agent", response_model=Agent)
def put_agent(value: AgentInput, actor: Actor, service: Service):
    return service.put_agent(actor, value, utc_now())


@router.post("/signals", response_model=Signal, status_code=201)
def create_signal(
    value: SignalInput,
    actor: Actor,
    service: Service,
    request_id: Annotated[UUID, Header(alias="Idempotency-Key")],
    background_tasks: BackgroundTasks,
):
    signal = service.create_signal(actor, value, utc_now(), request_id)
    background_tasks.add_task(service.attempt_passive_matches, actor)
    return signal


@router.get("/signals", response_model=SignalPage)
def list_signals(
    actor: Actor,
    service: Service,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
):
    items, next_cursor = service.list_items(actor, "signals", limit, cursor, utc_now())
    return SignalPage(items=items, next_cursor=next_cursor)


@router.get("/signals/{signal_id}", response_model=Signal)
def get_signal(signal_id: UUID, actor: Actor, service: Service):
    return service.get_signal(actor, signal_id)


@router.delete("/signals/{signal_id}", response_model=Signal)
def revoke_signal(signal_id: UUID, actor: Actor, service: Service):
    return service.revoke_signal(actor, signal_id, utc_now())


@router.post("/drafts", response_model=DraftOutput)
def draft(
    value: DraftInput, actor: Actor, provider: Annotated[DraftProvider, Depends(get_draft_provider)]
):
    try:
        return provider.draft(value.text)
    except LocalModelError as exc:
        raise StealthError(503, str(exc)) from None


@router.post("/matches/run", response_model=MatchRunOutput)
def run_matches(value: MatchRunInput, actor: Actor, service: Service):
    items = service.run_matches(actor, value.max_proposals, utc_now())
    return MatchRunOutput(items=items, created=len(items))


@router.post("/matches/semantic-run", response_model=SemanticMatchRun)
def run_semantic_matches(value: MatchRunInput, actor: Actor, service: Service):
    return run_local_semantic(service, actor, value.max_proposals, local_semantic_matcher())


@router.get("/proposals", response_model=ProposalPage)
def list_proposals(
    actor: Actor,
    service: Service,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
):
    items, next_cursor = service.list_items(actor, "proposals", limit, cursor, utc_now())
    return ProposalPage(items=items, next_cursor=next_cursor)


@router.get("/proposals/{proposal_id}", response_model=ProposalView)
def get_proposal(proposal_id: UUID, actor: Actor, service: Service):
    return service.get_proposal(actor, proposal_id, utc_now())


@router.post("/proposals/{proposal_id}/decision", response_model=ProposalView)
def decide(proposal_id: UUID, value: DecisionInput, actor: Actor, service: Service):
    return service.decide(actor, proposal_id, value.decision, utc_now(), value.decline_reason)


@router.post("/proposals/{proposal_id}/form", response_model=Formation)
def form(proposal_id: UUID, value: FormationInput, actor: Actor, service: Service):
    return service.form(actor, proposal_id, value, utc_now())


@router.get("/proposals/{proposal_id}/formation", response_model=Formation)
def get_formation(proposal_id: UUID, actor: Actor, service: Service):
    return service.get_formation(actor, proposal_id)
