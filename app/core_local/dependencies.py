import threading
import time
from collections import defaultdict, deque
from functools import lru_cache
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings
from app.core_cloud.store import SupabaseCoreStore
from app.core_local.schemas import ProfileView
from app.core_local.store import CoreStore

ROOT = Path(__file__).resolve().parents[2]
bearer = HTTPBearer(auto_error=False)
_attempts: dict[str, deque[float]] = defaultdict(deque)
_rate_lock = threading.Lock()


def require_local_mode(request: Request) -> None:
    settings = get_settings()
    if (
        not (settings.core_local_enabled or settings.core_supabase_enabled)
        or settings.environment != "local"
    ):
        raise HTTPException(503, "core_local_adapter_disabled")
    if settings.core_local_enabled and settings.core_supabase_enabled:
        raise HTTPException(503, "core_adapter_mode_conflict")
    if not request.client or request.client.host not in ("127.0.0.1", "::1", "testclient"):
        raise HTTPException(403, "loopback_only")
    if any(
        key in request.headers
        for key in ("forwarded", "x-forwarded-for", "x-real-ip", "x-forwarded-host")
    ):
        raise HTTPException(403, "local_proxy_headers_not_allowed")
    origin = request.headers.get("origin")
    if origin is not None:
        parsed = urlsplit(origin)
        if (
            origin not in settings.backend_cors_origins
            or parsed.hostname not in ("localhost", "127.0.0.1", "::1")
            or parsed.scheme != "http"
            or parsed.username
            or parsed.password
        ):
            raise HTTPException(403, "local_browser_origin_not_allowed")


@lru_cache
def local_store() -> CoreStore:
    settings = get_settings()
    root = (ROOT / ".local").resolve()
    path = (ROOT / settings.core_local_db_path).resolve()
    if not root.is_relative_to(ROOT) or not path.is_relative_to(root) or path == root:
        raise HTTPException(503, "local_storage_path_not_allowed")
    return CoreStore(path)


def get_store(
    response: Response, request: Request, _: Annotated[None, Depends(require_local_mode)]
) -> CoreStore | SupabaseCoreStore:
    # Post responses contain account-specific membership state and must not be cached.
    response.headers["Cache-Control"] = "no-store"
    settings = get_settings()
    if settings.core_supabase_enabled:
        authorization = request.headers.get("authorization", "")
        token = authorization[7:] if authorization.lower().startswith("bearer ") else None
        return SupabaseCoreStore(settings, token)
    return local_store()


StoreDep = Annotated[CoreStore, Depends(get_store)]


def credentials(
    value: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> str:
    if value is None or value.scheme.lower() != "bearer":
        raise HTTPException(401, "authentication_required", headers={"WWW-Authenticate": "Bearer"})
    return value.credentials


TokenDep = Annotated[str, Depends(credentials)]


def current_user(store: StoreDep, token: TokenDep) -> ProfileView:
    return store.authenticate(token)


UserDep = Annotated[ProfileView, Depends(current_user)]


def auth_rate_limit(request: Request, _: Annotated[None, Depends(require_local_mode)]) -> None:
    key = request.client.host
    now = time.monotonic()
    with _rate_lock:
        queue = _attempts[key]
        while queue and queue[0] <= now - 60:
            queue.popleft()
        if len(queue) >= 20:
            raise HTTPException(429, "too_many_auth_attempts", headers={"Retry-After": "60"})
        queue.append(now)
