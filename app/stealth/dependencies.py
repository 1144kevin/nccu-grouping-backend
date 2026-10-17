from functools import lru_cache
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings
from app.stealth.ai import DraftProvider, OfflineDraftProvider
from app.stealth.ollama import LocalModelError, OllamaDraftProvider
from app.stealth.service import StealthService
from app.stealth.storage import LocalStore

bearer = HTTPBearer(auto_error=False)
ROOT = Path(__file__).resolve().parents[2]


def get_draft_provider() -> DraftProvider:
    settings = get_settings()
    if settings.stealth_ai_provider == "offline_rules":
        return OfflineDraftProvider()
    try:
        return OllamaDraftProvider(
            settings.stealth_ollama_model,
            local_only_confirmed=settings.stealth_ollama_local_only_confirmed,
        )
    except LocalModelError as exc:
        raise HTTPException(503, detail=str(exc)) from None


@lru_cache
def local_service() -> StealthService:
    settings = get_settings()
    path = (ROOT / settings.stealth_local_db_path).resolve()
    local_root = (ROOT / ".local").resolve()
    if (
        not local_root.is_relative_to(ROOT.resolve())
        or not path.is_relative_to(local_root)
        or path == local_root
    ):
        raise HTTPException(503, detail="local_storage_path_not_allowed")
    return StealthService(LocalStore(path))


def require_local_mode(request: Request) -> None:
    settings = get_settings()
    if not settings.stealth_local_enabled or settings.environment != "local":
        raise HTTPException(503, detail="stealth_local_adapter_disabled")
    # Do not trust forwarded headers. This is a local test boundary, not production auth.
    if not request.client or request.client.host not in ("127.0.0.1", "::1", "testclient"):
        raise HTTPException(403, detail="loopback_only")
    if "origin" in request.headers:
        raise HTTPException(403, detail="local_browser_origin_not_allowed")
    if any(
        header in request.headers
        for header in (
            "forwarded",
            "x-forwarded-for",
            "x-forwarded-host",
            "x-real-ip",
        )
    ):
        raise HTTPException(403, detail="local_proxy_headers_not_allowed")


def get_local_service(_: Annotated[None, Depends(require_local_mode)]) -> StealthService:
    return local_service()


def current_local_user(
    service: Annotated[StealthService, Depends(get_local_service)],
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> UUID:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            401,
            detail="authentication_required",
            headers={
                "WWW-Authenticate": "Bearer",
            },
        )
    with service.store.transaction() as unit:
        actor = unit.authenticate(credentials.credentials)
    if actor is None:
        raise HTTPException(
            401, detail="invalid_local_credentials", headers={"WWW-Authenticate": "Bearer"}
        )
    return actor
