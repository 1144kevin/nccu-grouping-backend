"""Opt-in private TLS device boundary; never exposes Ollama itself."""

import asyncio

from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.cloud_security import WindowQuota
from app.core.device_test import validate_device_settings


class DeviceSecurity:
    def __init__(self, app, settings):
        validate_device_settings(settings)
        self.app, self.settings = app, settings
        self.quota = WindowQuota(max_keys=64)
        prefix = settings.api_v1_prefix + "/stealth-cloud"
        self.ai_paths = {
            prefix + "/intake",
            prefix + "/matches/semantic",
            prefix + "/matches/discover",
        }

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        scope.setdefault("state", {})["runtime_settings"] = self.settings
        request = Request(scope)

        async def deny(status, detail):
            await JSONResponse(
                {"detail": detail},
                status_code=status,
                headers={
                    "Cache-Control": "no-store",
                    **({"Retry-After": "60"} if status == 429 else {}),
                },
            )(scope, receive, send)

        from app.core_local.dependencies import require_local_mode

        try:
            require_local_mode(request)
        except HTTPException as exc:
            await deny(exc.status_code, exc.detail)
            return
        names = [name.lower() for name, _ in scope.get("headers", [])]
        if any(
            names.count(name) > 1
            for name in (b"authorization", b"host", b"content-length", b"origin")
        ):
            await deny(400, "ambiguous_request_headers")
            return
        peer = request.client.host
        if not self.quota.allow(("all", peer), 240):
            await deny(429, "too_many_requests")
            return
        if scope["path"] not in self.ai_paths:
            await self.app(scope, receive, send)
            return
        if not self.settings.core_device_ai_enabled:
            await deny(503, "device_ai_disabled")
            return
        # The actual token is still verified by UserDep before inference.
        if not request.headers.get("authorization", "").lower().startswith("bearer "):
            await deny(401, "authentication_required")
            return
        if not self.quota.allow(("ai", peer), 12):
            await deny(429, "device_ai_rate_limit")
            return
        try:
            length = int(request.headers.get("content-length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > 32_768:
            await deny(413, "request_body_too_large")
            return
        body = bytearray()
        try:
            async with asyncio.timeout(10):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    body.extend(message.get("body", b""))
                    if len(body) > 32_768:
                        await deny(413, "request_body_too_large")
                        return
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            await deny(408, "request_body_timeout")
            return
        if "content-length" in request.headers and len(body) != length:
            await deny(400, "body_length_mismatch")
            return
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, send)
