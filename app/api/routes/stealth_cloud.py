from datetime import datetime
from typing import Annotated, Literal
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import Field, field_validator

from app.core.cloud_security import WindowQuota
from app.core.config import get_settings
from app.core_cloud import openai_speech
from app.core_cloud.conversation import ConversationInput, ConversationResult, converse
from app.core_cloud.discovery import discover, index_active_signals
from app.core_cloud.gemini_conversation import GeminiDialogue
from app.core_cloud.gemini_provider import GeminiExtractionProvider, GeminiIntentInterpreter
from app.core_cloud.intake import IntakeRequest, IntakeResult
from app.core_cloud.openai_budget import configured_budget
from app.core_cloud.openai_provider import (
    OpenAIDialogue,
    OpenAIExtractionProvider,
    OpenAIIntentInterpreter,
)
from app.core_cloud.quick_stealth import QuickInterview, QuickResult, QuickStart
from app.core_cloud.quick_stealth import interview as quick_interview
from app.core_cloud.quick_stealth import start as quick_start
from app.core_cloud.reply_speech import SpeechInput, stream_speech, synthesize
from app.core_cloud.reply_suggestions import (
    ReplyRequest,
    ReplyResult,
    context_key,
    snapshot,
    suggest,
)
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
reindex_quota = WindowQuota()


def cloud(store: StoreDep, _: UserDep, response: Response) -> CloudStealth:
    response.headers["Cache-Control"] = "no-store"
    if not isinstance(store, SupabaseCoreStore):
        raise HTTPException(503, "stealth_requires_supabase")
    return CloudStealth(store)


Cloud = Annotated[CloudStealth, Depends(cloud)]


def configured_interpreter(settings):
    if settings.stealth_ai_provider == "openai" and (
        settings.environment == "local" or settings.core_cloud_enabled
    ):
        try:
            return OpenAIIntentInterpreter(
                settings.local_openai_api_key, budget=configured_budget(settings)
            )
        except LocalModelError as error:
            raise HTTPException(503, str(error)) from None
    if settings.stealth_ai_provider == "gemini":
        try:
            return GeminiIntentInterpreter(settings.stealth_gemini_api_key)
        except LocalModelError as error:
            raise HTTPException(503, str(error)) from None
    if settings.environment == "local" and settings.stealth_ai_provider == "ollama_local":
        return OllamaIntentInterpreter(
            OllamaDraftProvider(
                settings.stealth_ollama_model,
                local_only_confirmed=settings.stealth_ollama_local_only_confirmed,
            )
        )
    return None


@router.get("/quick-status")
def quick_status(service: Cloud, request: Request):
    try:
        service.store._request(
            "POST",
            "/rest/v1/rpc/core_stealth_quick",
            body={
                "action": "lookup",
                "args": {"request_key": "00000000-0000-0000-0000-000000000000"},
            },
            token=service.store.token,
        )
    except HTTPException as error:
        if error.status_code != 503:
            raise
        return {"ready": False}
    settings = getattr(request.state, "runtime_settings", None) or get_settings()
    return {
        "ready": True,
        "conversation_provider": settings.stealth_conversation_provider,
        "conversation_configured": (
            bool(settings.local_openai_api_key.get_secret_value().strip())
            if settings.stealth_conversation_provider == "openai"
            else settings.stealth_conversation_provider != "gemini"
            or bool(settings.stealth_gemini_api_key.get_secret_value().strip())
        ),
    }


@router.post("/quick-start", response_model=QuickResult)
def start_quick(
    value: QuickStart,
    service: Cloud,
    request: Request,
    user: UserDep,
    key: Annotated[str, Header(alias="Idempotency-Key", min_length=12, max_length=100)],
):
    settings = getattr(request.state, "runtime_settings", None) or get_settings()
    interpreter = configured_interpreter(settings) if value.confirmed_signal is None else None
    result = quick_start(service, value, key, interpreter=interpreter)
    watch = getattr(request.app.state, "stealth_watch", None)
    if watch is not None:
        watch.subscribe(str(user.id), service)
    return result


@router.post("/conversation", response_model=ConversationResult)
def conversation(value: ConversationInput, service: Cloud, request: Request, user: UserDep):
    settings = getattr(request.state, "runtime_settings", None) or get_settings()
    if settings.stealth_conversation_provider == "openai" and (
        settings.environment == "local" or settings.core_cloud_enabled
    ):
        try:
            return converse(
                service,
                value,
                dialogue=OpenAIDialogue(
                    settings.local_openai_api_key, str(user.id), budget=configured_budget(settings)
                ),
            )
        except LocalModelError as error:
            raise HTTPException(503, str(error)) from None
    if settings.stealth_conversation_provider == "gemini":
        return converse(
            service, value, dialogue=GeminiDialogue(settings.stealth_gemini_api_key, str(user.id))
        )
    interpreter = configured_interpreter(settings)
    try:
        return converse(service, value, interpreter=interpreter)
    except LocalModelError as error:
        raise HTTPException(503, str(error)) from None


@router.post("/speech")
def reply_speech(value: SpeechInput, request: Request, user: UserDep, response: Response):
    response.headers["Cache-Control"] = "no-store"
    settings = getattr(request.state, "runtime_settings", None) or get_settings()
    if settings.stealth_conversation_provider == "openai" and (
        settings.environment == "local" or settings.core_cloud_enabled
    ):
        return openai_speech.synthesize(
            value, settings.local_openai_api_key, str(user.id), budget=configured_budget(settings)
        )
    if settings.stealth_conversation_provider != "gemini":
        raise HTTPException(503, "stealth_gemini_not_configured")
    return synthesize(value, settings.stealth_gemini_api_key, str(user.id))


@router.post("/speech-stream")
def reply_speech_stream(value: SpeechInput, request: Request, user: UserDep):
    settings = getattr(request.state, "runtime_settings", None) or get_settings()
    if settings.stealth_conversation_provider == "openai" and (
        settings.environment == "local" or settings.core_cloud_enabled
    ):
        return StreamingResponse(
            openai_speech.stream_speech(
                value,
                settings.local_openai_api_key,
                str(user.id),
                budget=configured_budget(settings),
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )
    if settings.stealth_conversation_provider != "gemini":
        raise HTTPException(503, "stealth_gemini_not_configured")
    return StreamingResponse(
        stream_speech(value, settings.stealth_gemini_api_key, str(user.id)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/quick-interview", response_model=QuickInterview)
def interview_quick(value: IntakeRequest, service: Cloud, request: Request):
    settings = getattr(request.state, "runtime_settings", None) or get_settings()
    interpreter = configured_interpreter(settings)
    try:
        return quick_interview(service, value, interpreter=interpreter)
    except LocalModelError as error:
        raise HTTPException(503, str(error)) from None


class CoordinationMessage(StrictModel):
    content: str = Field(min_length=1, max_length=2000)

    @field_validator("content")
    @classmethod
    def valid_text(cls, value):
        if not value.strip() or "\0" in value:
            raise ValueError("invalid message")
        value.encode("utf-8")
        return value.strip()


def room_rpc(service, action, args):
    return service.store._request(
        "POST",
        "/rest/v1/rpc/core_stealth_quick",
        body={"action": action, "args": args},
        token=service.store.token,
    )


@router.get("/matches/{proposal_id}/room")
def coordination_room(proposal_id: UUID, service: Cloud):
    return room_rpc(service, "state", {"proposal_id": str(proposal_id)})


reply_quota = WindowQuota(max_keys=1024)


def reply_context(service, proposal_id):
    room = room_rpc(service, "state", {"proposal_id": str(proposal_id)})
    if not room.get("opened") or not room.get("can_send"):
        raise HTTPException(409, "stealth_room_read_only")
    match = next((m for m in service.matches().items if m.id == proposal_id), None)
    if match is None or match.status == "unavailable":
        raise HTTPException(404, "stealth_match_unavailable")
    return snapshot(match, room)


@router.post("/matches/{proposal_id}/reply-suggestions", response_model=ReplyResult)
def reply_suggestions(
    proposal_id: UUID, value: ReplyRequest, service: Cloud, request: Request, user: UserDep
):
    context = reply_context(service, proposal_id)
    if not reply_quota.allow(("stealth-reply", str(user.id)), 12):
        raise HTTPException(429, "stealth_reply_rate_limit")
    settings = getattr(request.state, "runtime_settings", None) or get_settings()
    provider = None
    if (
        settings.environment == "local"
        and not settings.core_cloud_enabled
        and settings.stealth_ai_provider == "ollama_local"
        and settings.stealth_ollama_local_only_confirmed
    ):
        try:
            provider = OllamaDraftProvider(settings.stealth_ollama_model, local_only_confirmed=True)
        except LocalModelError:
            pass
    result = suggest(context, value.batch, provider, value.previous)
    # Recheck access and snapshot after potentially slow model inference.
    if context_key(reply_context(service, proposal_id)) != result.context_key:
        raise HTTPException(409, "stealth_reply_context_changed")
    return result


@router.post("/matches/{proposal_id}/room", status_code=201)
def coordination_send(
    proposal_id: UUID,
    value: CoordinationMessage,
    service: Cloud,
    key: Annotated[str, Header(alias="Idempotency-Key", min_length=12, max_length=100)],
):
    # App request identifiers are opaque strings (post-...). Keep existing UUID
    # clients compatible and map opaque identifiers deterministically for SQL.
    try:
        request_key = UUID(key)
    except ValueError:
        request_key = uuid5(NAMESPACE_URL, "core-stealth-room:" + key)
    return room_rpc(
        service,
        "send",
        {
            "proposal_id": str(proposal_id),
            "request_key": str(request_key),
            "content": value.content,
        },
    )


class IntentDecision(StrictModel):
    decision: Literal["accepted", "declined"]


@router.get("/agent", response_model=CloudAgent | None)
def agent(service: Cloud):
    return service.agent()


@router.put("/agent", response_model=CloudAgent)
def save_agent(value: CloudAgentInput, service: Cloud, request: Request, user: UserDep):
    result = service.save_agent(value)
    watch = getattr(request.app.state, "stealth_watch", None)
    if watch is not None:
        if result.enabled:
            watch.subscribe(str(user.id), service)
        else:
            watch.unsubscribe(str(user.id))
    return result


@router.post("/draft", response_model=DraftOutput)
def draft(value: DraftInput, _: Cloud, request: Request):
    settings = getattr(request.state, "runtime_settings", None) or get_settings()
    if settings.stealth_ai_provider == "openai" and (
        settings.environment == "local" or settings.core_cloud_enabled
    ):
        try:
            return OpenAIExtractionProvider(
                settings.local_openai_api_key, budget=configured_budget(settings)
            ).draft(value.text)
        except LocalModelError as error:
            raise HTTPException(503, str(error)) from None
    if settings.stealth_ai_provider == "gemini":
        try:
            return GeminiExtractionProvider(settings.stealth_gemini_api_key).draft(value.text)
        except LocalModelError as error:
            raise HTTPException(503, str(error)) from None
    return OfflineDraftProvider().draft(value.text)


@router.post("/intake", response_model=IntakeResult)
def intake(value: IntakeRequest, _: Cloud, request: Request):
    settings = getattr(request.state, "runtime_settings", None) or get_settings()
    if settings.stealth_ai_provider not in ("ollama_local", "gemini", "openai"):
        raise HTTPException(503, "local_model_not_configured")
    try:
        interpreter = configured_interpreter(settings)
        if interpreter is None:
            raise HTTPException(503, "local_model_not_configured")
        return interpreter.interpret(value)
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
def signals(service: Cloud, request: Request, user: UserDep):
    result = service.signals()
    watch = getattr(request.app.state, "stealth_watch", None)
    if watch is not None and any(s.status == "active" for s in result.items):
        watch.subscribe(str(user.id), service)
    return result


@router.get("/formed/{post_id}/roles")
def formed_roles(post_id: UUID, service: Cloud, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return service.store._request(
        "POST",
        "/rest/v1/rpc/core_stealth_formed_roles",
        body={"post_id": str(post_id)},
        token=service.store.token,
    )


@router.get("/history")
def stealth_history(
    service: Cloud,
    response: Response,
    cursor_time: datetime | None = None,
    cursor_id: UUID | None = None,
    limit: int = Query(default=20, ge=1, le=50),
):
    response.headers["Cache-Control"] = "no-store"
    return service.store._request(
        "POST",
        "/rest/v1/rpc/core_stealth_history",
        body={
            "cursor_time": cursor_time.isoformat() if cursor_time else None,
            "cursor_id": str(cursor_id) if cursor_id else None,
            "page_limit": limit,
        },
        token=service.store.token,
    )


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


@router.post("/matches/discover")
def discover_matches(service: Cloud):
    matcher = local_semantic_matcher(service.store.settings)
    try:
        return discover(service, matcher.embeddings)
    except LocalModelError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/matches/reindex")
def reindex_own_signals(service: Cloud, user: UserDep):
    settings = service.store.settings
    if not settings.cloud_openai_schema_ready:
        raise HTTPException(503, "semantic_migration_not_ready")
    if not reindex_quota.allow(("semantic-reindex", str(user.id)), 2):
        raise HTTPException(429, "openai_rate_limit")
    matcher = local_semantic_matcher(settings)
    try:
        _, vectors, count = index_active_signals(service, matcher.embeddings)
    except LocalModelError as exc:
        raise HTTPException(503, str(exc)) from None
    return {"indexed": count, "model_id": vectors.model_id if vectors else None}


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
