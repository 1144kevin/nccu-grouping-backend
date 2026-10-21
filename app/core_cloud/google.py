"""Loopback web OAuth: short-lived, single-use PKCE proofs, never persisted or logged."""

import base64
import hashlib
import secrets
import threading
import time
from typing import Literal
from urllib.parse import urlencode

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.core_cloud.store import PROJECT_URL, SupabaseCoreStore
from app.core_local.schemas import SessionView

CALLBACK_URL = "http://localhost:8081/auth/callback"
ACCOUNT_CALLBACK_URL = "http://localhost:8082/auth/callback"
IOS_CALLBACK_URL = "nccugrouping://auth/callback"
FLOW_TTL = 600
MAX_FLOWS = 128


class GoogleStartInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client: Literal["web", "web_account", "ios"] = "web"


class GoogleStartView(BaseModel):
    authorize_url: str
    flow_id: str
    expires_in: int = FLOW_TTL


class GoogleFinishInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    flow_id: SecretStr = Field(min_length=40, max_length=100)
    code: SecretStr = Field(min_length=1, max_length=2048)


class GoogleFlows:
    def __init__(self):
        self._flows: dict[str, tuple[float, str]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _digest(flow_id: str) -> str:
        return hashlib.sha256(flow_id.encode()).hexdigest()

    def start(
        self, store: SupabaseCoreStore, *, client: Literal["web", "web_account", "ios"] = "web"
    ) -> GoogleStartView:
        from app.core.config import get_settings

        settings = getattr(store, "settings", None) or get_settings()
        if client == "web_account" and settings.environment != "local":
            raise HTTPException(403, "google_account_web_local_only")
        callback = (
            settings.cloud_google_web_callback if settings.core_cloud_enabled else CALLBACK_URL
        )
        if client == "web" and settings.core_cloud_enabled and not callback:
            raise HTTPException(403, "google_cloud_web_not_configured")
        settings = store._request("GET", "/auth/v1/settings")
        providers = settings.get("external") if isinstance(settings, dict) else None
        if not isinstance(providers, dict) or providers.get("google") is not True:
            raise HTTPException(503, "google_login_not_configured")
        flow_id = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .rstrip(b"=")
            .decode()
        )
        now = time.monotonic()
        with self._lock:
            self._flows = {key: value for key, value in self._flows.items() if value[0] > now}
            if len(self._flows) >= MAX_FLOWS:
                raise HTTPException(429, "too_many_auth_attempts")
            self._flows[self._digest(flow_id)] = (now + FLOW_TTL, verifier)
        query = urlencode(
            {
                "provider": "google",
                "redirect_to": IOS_CALLBACK_URL
                if client == "ios"
                else ACCOUNT_CALLBACK_URL
                if client == "web_account"
                else callback,
                "code_challenge": challenge,
                "code_challenge_method": "s256",
                "scopes": "openid email profile",
                "prompt": "select_account",
            }
        )
        return GoogleStartView(
            authorize_url=PROJECT_URL + "/auth/v1/authorize?" + query, flow_id=flow_id
        )

    def finish(self, store: SupabaseCoreStore, value: GoogleFinishInput) -> SessionView:
        # Pop under lock BEFORE network I/O: a proof cannot race or be replayed.
        with self._lock:
            proof = self._flows.pop(self._digest(value.flow_id.get_secret_value()), None)
        if proof is None or proof[0] <= time.monotonic():
            raise HTTPException(400, "google_login_expired_restart")
        try:
            data = store._request(
                "POST",
                "/auth/v1/token?grant_type=pkce",
                body={"auth_code": value.code.get_secret_value(), "code_verifier": proof[1]},
            )
        except HTTPException as error:
            if error.status_code in (400, 401, 403, 422):
                raise HTTPException(400, "google_login_expired_restart") from None
            raise
        if not isinstance(data, dict):
            raise HTTPException(503, "supabase_invalid_response")
        return store._session(data)


google_flows = GoogleFlows()
