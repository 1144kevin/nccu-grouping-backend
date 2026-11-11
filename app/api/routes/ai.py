import hashlib
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request, Response
from pydantic import BaseModel

from app.core.cloud_security import WindowQuota
from app.core.config import get_settings
from app.core_cloud.gemini_provider import GeminiExtractionProvider
from app.core_cloud.openai_budget import configured_budget
from app.core_cloud.openai_provider import OpenAIExtractionProvider
from app.core_cloud.post_writing import PostDraft, WritingInput, draft_post
from app.core_cloud.writing_jobs import writing_jobs
from app.core_local.dependencies import StoreDep, TokenDep, UserDep
from app.stealth.ollama import LocalModelError, OllamaDraftProvider

router = APIRouter(prefix="/ai", tags=["ai"])
quota = WindowQuota(max_keys=1024)


class WritingJobView(BaseModel):
    id: UUID
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"]
    queue_position: int
    result: PostDraft | None = None
    error: str | None = None


def configured_provider(request):
    settings = getattr(request.state, "runtime_settings", None) or get_settings()
    if settings.stealth_ai_provider == "openai" and (
        settings.environment == "local" or settings.core_cloud_enabled
    ):
        try:
            return OpenAIExtractionProvider(
                settings.local_openai_api_key, budget=configured_budget(settings)
            )
        except LocalModelError as error:
            raise HTTPException(503, str(error)) from None
    if settings.stealth_ai_provider == "gemini":
        try:
            return GeminiExtractionProvider(settings.stealth_gemini_api_key)
        except LocalModelError as error:
            raise HTTPException(503, str(error)) from None
    if (
        settings.environment != "local"
        or settings.core_cloud_enabled
        or settings.stealth_ai_provider != "ollama_local"
        or not settings.stealth_ollama_local_only_confirmed
    ):
        raise HTTPException(503, "local_post_writing_not_configured")
    try:
        return OllamaDraftProvider(settings.stealth_ollama_model, local_only_confirmed=True)
    except LocalModelError as error:
        raise HTTPException(503, str(error)) from None


@router.post("/post-writing/jobs", response_model=WritingJobView, status_code=202)
def start_writing_job(
    value: WritingInput,
    request: Request,
    response: Response,
    store: StoreDep,
    user: UserDep,
    token: TokenDep,
    key: Annotated[str, Header(alias="Idempotency-Key", min_length=12, max_length=100)],
):
    response.headers["Cache-Control"] = "no-store"
    provider = configured_provider(request)
    owner = str(user.id)

    def work():
        if str(store.authenticate(token).id) != owner:
            raise HTTPException(401, "invalid_or_expired_session")
        result = draft_post(value, provider)
        if str(store.authenticate(token).id) != owner:
            raise HTTPException(401, "invalid_or_expired_session")
        return result

    return writing_jobs.submit(
        owner,
        key,
        hashlib.sha256(value.model_dump_json().encode()).hexdigest(),
        work,
        admit=lambda: quota.allow(("post-writing", owner), 6),
    )


@router.get("/post-writing/jobs/{job_id}", response_model=WritingJobView)
def writing_job(job_id: UUID, response: Response, user: UserDep):
    response.headers["Cache-Control"] = "no-store"
    return writing_jobs.get(str(user.id), job_id)


@router.delete("/post-writing/jobs/{job_id}", response_model=WritingJobView)
def cancel_writing_job(job_id: UUID, response: Response, user: UserDep):
    response.headers["Cache-Control"] = "no-store"
    return writing_jobs.get(str(user.id), job_id, cancel=True)


@router.post("/post-writing", response_model=PostDraft)
def post_writing(
    value: WritingInput,
    request: Request,
    response: Response,
    store: StoreDep,
    user: UserDep,
    token: TokenDep,
):
    response.headers["Cache-Control"] = "no-store"
    if not quota.allow(("post-writing", str(user.id)), 6):
        raise HTTPException(429, "post_writing_rate_limit")
    provider = configured_provider(request)
    result = draft_post(value, provider)
    if store.authenticate(token).id != user.id:
        raise HTTPException(401, "invalid_or_expired_session")
    return result
