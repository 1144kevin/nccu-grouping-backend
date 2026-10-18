from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
from fastapi import HTTPException

from app.core.config import Settings
from app.core_local.schemas import (
    ChatActivity,
    ChatPage,
    ChatReadView,
    ConfirmationView,
    ConversationPage,
    DemoCleanupView,
    JoinView,
    LoginInput,
    MessageInput,
    MessageView,
    OrderActivityPage,
    PostEditInput,
    PostInput,
    PostPage,
    PostView,
    ProfileInput,
    ProfileView,
    PublicProfileView,
    RefreshInput,
    RegisterInput,
    ServerClockView,
    SessionView,
)

PROJECT_URL = "https://axdwbfhrkbovgrbngrrh.supabase.co"


class SupabaseCoreStore:
    def __init__(self, settings: Settings, token: str | None = None):
        # Explicitly scope this development adapter to the authorized project.
        if settings.supabase_url.rstrip(
            "/"
        ) != PROJECT_URL or not settings.supabase_publishable_key.startswith("sb_publishable_"):
            raise HTTPException(503, "supabase_development_not_configured")
        self.key = settings.supabase_publishable_key
        self.settings = settings
        self.token = token

    def _request(self, method: str, path: str, *, body=None, token: str | None = None) -> Any:
        headers = {"apikey": self.key, "Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            with httpx.Client(timeout=15, trust_env=False, follow_redirects=False) as client:
                response = client.request(method, PROJECT_URL + path, headers=headers, json=body)
        except httpx.HTTPError:
            raise HTTPException(503, "supabase_unavailable") from None
        if response.status_code >= 400:
            try:
                data = response.json()
            except ValueError:
                data = {}
            if not isinstance(data, dict):
                data = {}
            code = data.get("code", "")
            if path == "/auth/v1/user" and response.status_code in (401, 403):
                raise HTTPException(401, "invalid_or_expired_session")
            if isinstance(code, str) and code.startswith("PT") and code[2:].isdigit():
                status = int(code[2:])
                if status in (401, 403, 404, 409, 422, 429):
                    raise HTTPException(status, data.get("message", "supabase_operation_failed"))
            if code in (
                "invalid_credentials",
                "email_not_confirmed",
                "user_already_exists",
                "over_email_send_rate_limit",
                "email_address_not_authorized",
                "email_address_invalid",
            ):
                detail = {
                    "invalid_credentials": "invalid_email_or_password",
                    "email_not_confirmed": "email_confirmation_required",
                    "user_already_exists": "email_already_registered",
                    "over_email_send_rate_limit": "email_send_rate_limit",
                    "email_address_not_authorized": "email_provider_not_configured",
                    "email_address_invalid": "email_address_invalid",
                }[code]
                raise HTTPException(
                    401
                    if code == "invalid_credentials"
                    else 429
                    if response.status_code == 429
                    else 400,
                    detail,
                )
            if response.status_code == 401:
                raise HTTPException(401, "invalid_or_expired_session")
            if response.status_code == 429:
                raise HTTPException(429, "too_many_auth_attempts")
            if path == "/auth/v1/token?grant_type=refresh_token" and response.status_code in (
                400,
                403,
                404,
                422,
            ):
                raise HTTPException(401, "invalid_or_expired_session")
            if path == "/auth/v1/token?grant_type=pkce" and response.status_code in (
                400,
                403,
                404,
                422,
            ):
                raise HTTPException(400, "google_login_expired_restart")
            raise HTTPException(503, "supabase_operation_failed")
        if response.status_code == 204:
            return None
        try:
            return response.json()
        except ValueError:
            raise HTTPException(503, "supabase_invalid_response") from None

    def _rpc(self, action: str, args: dict | None = None, token: str | None = None):
        credential = token or self.token
        if not credential:
            raise HTTPException(401, "authentication_required")
        return self._request(
            "POST",
            "/rest/v1/rpc/core_dispatch",
            body={"action": action, "args": args or {}},
            token=credential,
        )

    def _session(self, data: dict) -> SessionView:
        token = data.get("access_token")
        if not isinstance(token, str) or not token:
            raise HTTPException(503, "supabase_invalid_response")
        user = self.authenticate(token)
        expires = data.get("expires_at")
        return SessionView(
            access_token=token,
            refresh_token=data.get("refresh_token"),
            expires_at=datetime.fromtimestamp(expires, UTC)
            if isinstance(expires, (int, float))
            else datetime.now(UTC) + timedelta(seconds=int(data.get("expires_in", 3600))),
            user=user,
        )

    def register(self, value: RegisterInput) -> SessionView | ConfirmationView:
        data = self._request(
            "POST",
            "/auth/v1/signup",
            body={
                "email": value.email,
                "password": value.password.get_secret_value(),
                "data": {"display_name": value.display_name},
            },
        )
        if not data.get("access_token"):
            return ConfirmationView()
        return self._session(data)

    def login(self, value: LoginInput) -> SessionView:
        return self._session(
            self._request(
                "POST",
                "/auth/v1/token?grant_type=password",
                body={"email": value.email, "password": value.password.get_secret_value()},
            )
        )

    def refresh(self, value: RefreshInput) -> SessionView:
        return self._session(
            self._request(
                "POST",
                "/auth/v1/token?grant_type=refresh_token",
                body={"refresh_token": value.refresh_token.get_secret_value()},
            )
        )

    def authenticate(self, token: str) -> ProfileView:
        # Auth server verifies signature/expiry; RPC additionally checks live session ownership.
        self._request("GET", "/auth/v1/user", token=token)
        return ProfileView.model_validate(self._rpc("me", token=token))

    def logout(self, token: str) -> None:
        self._request("POST", "/auth/v1/logout?scope=local", token=token)

    def clear_demo(self) -> DemoCleanupView:
        return DemoCleanupView.model_validate(
            self._request(
                "POST",
                "/rest/v1/rpc/core_clear_demo",
                body={},
                token=self.token,
            )
        )

    def update_profile(self, actor: UUID, value: ProfileInput) -> ProfileView:
        return ProfileView.model_validate(self._rpc("profile", value.model_dump(mode="json")))

    def posts(
        self,
        actor: UUID,
        scope: str,
        limit: int,
        cursor: str | None,
        q: str = "",
        active_only: bool = False,
    ) -> PostPage:
        if scope == "history":
            return PostPage.model_validate(
                self._request(
                    "POST",
                    "/rest/v1/rpc/core_order_history",
                    body={
                        "args": {
                            "limit": limit,
                            "cursor": cursor,
                            "q": q,
                            "active_only": active_only,
                        }
                    },
                    token=self.token,
                )
            )
        return PostPage.model_validate(
            self._rpc(
                "posts",
                {
                    "scope": scope,
                    "limit": limit,
                    "cursor": cursor,
                    "q": q,
                    "active_only": active_only,
                },
            )
        )

    def post(self, actor: UUID, post_id: UUID) -> PostView:
        return PostView.model_validate(self._rpc("post", {"post_id": str(post_id)}))

    def create_post(self, actor: UUID, value: PostInput, request_key: str) -> PostView:
        return PostView.model_validate(
            self._rpc(
                "create", {"value": value.model_dump(mode="json"), "request_key": request_key}
            )
        )

    def edit_post(self, actor: UUID, post_id: UUID, value: PostEditInput) -> PostView:
        return PostView.model_validate(
            self._rpc(
                "edit",
                {
                    "post_id": str(post_id),
                    "expected_version": value.expected_version,
                    "value": value.model_dump(mode="json", exclude={"expected_version"}),
                },
            )
        )

    def request_join(self, actor: UUID, post_id: UUID, expected_version: int) -> JoinView:
        return JoinView.model_validate(
            self._rpc("join", {"post_id": str(post_id), "expected_version": expected_version})
        )

    def join_requests(self, actor: UUID, post_id: UUID) -> list[JoinView]:
        return [
            JoinView.model_validate(item)
            for item in self._rpc("requests", {"post_id": str(post_id)})
        ]

    def decide(self, actor: UUID, join_id: UUID, decision: str) -> JoinView:
        return JoinView.model_validate(
            self._rpc("decide", {"join_id": str(join_id), "status": decision})
        )

    def withdraw(self, actor: UUID, post_id: UUID) -> PostView:
        return PostView.model_validate(self._rpc("withdraw", {"post_id": str(post_id)}))

    def cancel(self, actor: UUID, post_id: UUID) -> PostView:
        return PostView.model_validate(self._rpc("cancel", {"post_id": str(post_id)}))

    def _chat(self, action: str, args: dict):
        return self._request(
            "POST",
            "/rest/v1/rpc/core_chat",
            body={"action": action, "args": args},
            token=self.token,
        )

    def chat_activity(self) -> ChatActivity:
        return ChatActivity.model_validate(
            self._request(
                "POST",
                "/rest/v1/rpc/core_chat_activity",
                body={"action": "status", "args": {}},
                token=self.token,
            )
        )

    def read_chat(self, post_id: UUID, message_id: UUID) -> ChatReadView:
        return ChatReadView.model_validate(
            self._request(
                "POST",
                "/rest/v1/rpc/core_chat_activity",
                body={
                    "action": "read",
                    "args": {"post_id": str(post_id), "message_id": str(message_id)},
                },
                token=self.token,
            )
        )

    def public_profile(self, user_id: UUID) -> PublicProfileView:
        return PublicProfileView.model_validate(
            self._request(
                "POST",
                "/rest/v1/rpc/core_public_user",
                body={"action": "profile", "args": {"user_id": str(user_id)}},
                token=self.token,
            )
        )

    def order_activity(self, limit: int, cursor: str | None) -> OrderActivityPage:
        return OrderActivityPage.model_validate(
            self._request(
                "POST",
                "/rest/v1/rpc/core_order_activity",
                body={"args": {"limit": limit, "cursor": cursor}},
                token=self.token,
            )
        )

    def clock(self) -> ServerClockView:
        return ServerClockView.model_validate(
            self._request(
                "POST",
                "/rest/v1/rpc/core_server_clock",
                body={},
                token=self.token,
            )
        )

    def user_posts(self, actor: UUID, user_id: UUID, limit: int, cursor: str | None) -> PostPage:
        return PostPage.model_validate(
            self._request(
                "POST",
                "/rest/v1/rpc/core_public_user",
                body={
                    "action": "posts",
                    "args": {"user_id": str(user_id), "limit": limit, "cursor": cursor},
                },
                token=self.token,
            )
        )

    def conversations(self, limit: int, cursor: str | None) -> ConversationPage:
        return ConversationPage.model_validate(
            self._chat("list", {"limit": limit, "cursor": cursor})
        )

    def messages(self, post_id: UUID, limit: int, cursor: str | None) -> ChatPage:
        return ChatPage.model_validate(
            self._chat("messages", {"post_id": str(post_id), "limit": limit, "cursor": cursor})
        )

    def send_message(self, post_id: UUID, value: MessageInput, client_key: str) -> MessageView:
        return MessageView.model_validate(
            self._chat(
                "send",
                {"post_id": str(post_id), "content": value.content, "client_key": client_key},
            )
        )
