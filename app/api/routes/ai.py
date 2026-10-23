from fastapi import APIRouter, HTTPException, Request, Response

from app.core.cloud_security import WindowQuota
from app.core.config import get_settings
from app.core_cloud.post_writing import WritingInput, WritingOutput, write_post
from app.core_local.dependencies import StoreDep, TokenDep, UserDep
from app.stealth.ollama import LocalModelError, OllamaDraftProvider

router = APIRouter(prefix="/ai", tags=["ai"])
quota = WindowQuota(max_keys=1024)


@router.post("/post-writing", response_model=WritingOutput)
def post_writing(
    value: WritingInput,
    request: Request,
    response: Response,
    store: StoreDep,
    user: UserDep,
    token: TokenDep,
):
    response.headers["Cache-Control"] = "no-store"
    settings = getattr(request.state, "runtime_settings", None) or get_settings()
    if (
        settings.environment != "local"
        or settings.core_cloud_enabled
        or settings.stealth_ai_provider != "ollama_local"
        or not settings.stealth_ollama_local_only_confirmed
    ):
        raise HTTPException(503, "local_post_writing_not_configured")
    if not quota.allow(("post-writing", str(user.id)), 6):
        raise HTTPException(429, "post_writing_rate_limit")
    try:
        provider = OllamaDraftProvider(settings.stealth_ollama_model, local_only_confirmed=True)
    except LocalModelError as error:
        raise HTTPException(503, str(error)) from None
    result = write_post(value, provider)
    if store.authenticate(token).id != user.id:
        raise HTTPException(401, "invalid_or_expired_session")
    return WritingOutput(text=result)
