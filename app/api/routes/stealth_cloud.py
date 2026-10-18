from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from pydantic import Field, field_validator

from app.core.config import get_settings
from app.core_cloud.intake import IntakeRequest, IntakeResult
from app.core_cloud.semantic import SemanticCloudMatches, SemanticRequest, semantic_cloud_matches
from app.core_cloud.stealth import (
    CloudAgent,
    CloudAgentInput,
    CloudMatch,
    CloudMatches,
    CloudRun,
    CloudSignalCreated,
    CloudSignalInput,
    CloudSignals,
    CloudStealth,
    CloudTerms,
)
from app.core_cloud.store import SupabaseCoreStore
from app.core_local.dependencies import StoreDep, UserDep
from app.stealth.ai import OfflineDraftProvider
from app.stealth.local_providers import OllamaIntentInterpreter
from app.stealth.ollama import LocalModelError, OllamaDraftProvider
from app.stealth.provider_registry import local_semantic_matcher
from app.stealth.schemas import (
    DraftInput,
    DraftOutput,
    MatchRunInput,
    Signal,
    StrictModel,
)

router = APIRouter(prefix="/stealth-cloud", tags=["stealth-supabase"])


def cloud(store: StoreDep, _: UserDep, response: Response) -> CloudStealth:
    response.headers["Cache-Control"] = "no-store"
    if not isinstance(store, SupabaseCoreStore):
        raise HTTPException(503, "stealth_requires_supabase")
    return CloudStealth(store)


Cloud = Annotated[CloudStealth, Depends(cloud)]


class IntentDecision(StrictModel):
    decision: Literal["accepted", "declined"]


@router.get("/agent", response_model=CloudAgent | None)
def agent(service: Cloud):
    return service.agent()


@router.put("/agent", response_model=CloudAgent)
def save_agent(value: CloudAgentInput, service: Cloud):
    return service.save_agent(value)


@router.post("/draft", response_model=DraftOutput)
def draft(value: DraftInput, _: Cloud):
    return OfflineDraftProvider().draft(value.text)


@router.post("/intake", response_model=IntakeResult)
def intake(value: IntakeRequest, _: Cloud):
    settings = get_settings()
    if settings.environment != "local" or settings.stealth_ai_provider != "ollama_local":
        raise HTTPException(503, "local_model_not_configured")
    try:
        return OllamaIntentInterpreter(
            OllamaDraftProvider(
                settings.stealth_ollama_model,
                local_only_confirmed=settings.stealth_ollama_local_only_confirmed,
            )
        ).interpret(value)
    except LocalModelError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/signals", response_model=CloudSignalCreated, status_code=201)
def create_signal(
    value: CloudSignalInput,
    service: Cloud,
    key: Annotated[str, Header(alias="Idempotency-Key", min_length=12, max_length=100)],
):
    return service.create(value, key)


@router.get("/signals", response_model=CloudSignals)
def signals(service: Cloud):
    return service.signals()


@router.delete("/signals/{signal_id}", response_model=Signal)
def revoke(signal_id: UUID, service: Cloud):
    return service.revoke(signal_id)


@router.post("/matches/run", response_model=CloudRun)
def run(value: MatchRunInput, service: Cloud):
    return service.run(value.max_proposals)


@router.get("/matches", response_model=CloudMatches)
def matches(service: Cloud):
    return service.matches()


@router.post("/matches/semantic", response_model=SemanticCloudMatches)
def semantic_matches(value: SemanticRequest, service: Cloud):
    matcher = local_semantic_matcher(service.store.settings)
    return semantic_cloud_matches(service, matcher, value.review_limit)


@router.post("/matches/{proposal_id}/decision", response_model=CloudMatch)
def decide(proposal_id: UUID, value: IntentDecision, service: Cloud):
    return service.decide(proposal_id, value.decision)


class TermsRequest(StrictModel):
    expected_revision: int = Field(ge=0, le=20, strict=True)
    value: CloudTerms


class ConsentRequest(StrictModel):
    expected_revision: int = Field(ge=1, le=20, strict=True)
    agree: Literal[True]

    @field_validator("agree", mode="before")
    @classmethod
    def explicit_consent(cls, value):
        if value is not True:
            raise ValueError("explicit_consent_required")
        return value


@router.put("/matches/{proposal_id}/terms", response_model=CloudMatch)
def terms(proposal_id: UUID, value: TermsRequest, service: Cloud):
    return service.formation(
        "terms", proposal_id, value.expected_revision, value=value.value.model_dump(mode="json")
    )


@router.post("/matches/{proposal_id}/consent", response_model=CloudMatch)
def consent(proposal_id: UUID, value: ConsentRequest, service: Cloud):
    return service.formation("consent", proposal_id, value.expected_revision, agree=value.agree)
