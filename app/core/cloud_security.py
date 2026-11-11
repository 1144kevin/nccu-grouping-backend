"""Single-instance ASGI ingress boundary; never trusts arbitrary forwarded headers."""

import asyncio
import ipaddress
import threading
import time
import uuid
from collections import deque
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

from app.core.cloud_runtime import validate_cloud_settings


class WindowQuota:
    def __init__(self, max_keys=4096):
        self.max_keys = max_keys
        self.entries = {}
        self.lock = threading.Lock()

    def allow(self, key, limit, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            for expired in [k for k, q in self.entries.items() if not q or q[-1] <= now - 60]:
                del self.entries[expired]
            if key not in self.entries:
                if len(self.entries) >= self.max_keys:
                    return False
                self.entries[key] = deque()
            queue = self.entries[key]
            while queue and queue[0] <= now - 60:
                queue.popleft()
            if len(queue) >= limit:
                return False
            queue.append(now)
            return True


class CloudSecurity:
    def __init__(self, app, settings):
        validate_cloud_settings(settings)
        self.app, self.settings = app, settings
        self.host = urlsplit(settings.cloud_public_api_url).hostname
        self.proxies = [ipaddress.ip_network(v) for v in settings.cloud_proxy_networks]
        self.quota = WindowQuota()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid.uuid4().hex

        async def secure_send(message):
            if message["type"] == "http.response.start":
                headers = [
                    (k, v)
                    for k, v in message.get("headers", [])
                    if k.lower()
                    not in (b"cache-control", b"x-request-id", b"strict-transport-security")
                ]
                headers.extend(
                    [
                        (b"cache-control", b"no-store"),
                        (b"x-request-id", request_id.encode()),
                        (b"x-content-type-options", b"nosniff"),
                        (b"referrer-policy", b"no-referrer"),
                        (b"strict-transport-security", b"max-age=31536000"),
                    ]
                )
                message["headers"] = headers
            await send(message)

        async def deny(status, detail):
            response = JSONResponse(
                {"detail": detail},
                status_code=status,
                headers={"Retry-After": "60"} if status == 429 else None,
            )
            await response(scope, receive, secure_send)

        # Liveness is deliberately dependency-free; never claims database readiness.
        if scope["path"] == "/health" and scope["method"] in ("GET", "HEAD"):
            await JSONResponse({"status": "ok"})(scope, receive, secure_send)
            return
        values = {}
        for key, value in scope.get("headers", []):
            name = key.lower()
            if name in values and name in (
                b"host",
                b"origin",
                b"content-length",
                b"content-type",
                b"x-forwarded-for",
                b"x-forwarded-proto",
                b"x-forwarded-host",
                b"authorization",
                b"idempotency-key",
            ):
                await deny(400, "ambiguous_request_headers")
                return
            values[name] = value.decode("latin1")
        render_mode = self.settings.cloud_proxy_mode == "render"
        try:
            peer = ipaddress.ip_address(scope["client"][0])
            raw_xff = values.get(b"x-forwarded-for", "")
            if render_mode:
                hops = [
                    ipaddress.ip_address(part.strip())
                    for part in raw_xff.split(",")
                    if part.strip()
                ]
                if not hops or any(h.is_unspecified or h.is_multicast for h in hops):
                    raise ValueError("invalid_xff")
                cf_ip = values.get(b"cf-connecting-ip", "").strip()
                client = ipaddress.ip_address(cf_ip) if cf_ip else hops[0]
                if client not in hops:
                    client = hops[0]
            else:
                client = ipaddress.ip_address(raw_xff)
        except (ValueError, TypeError, KeyError):
            await deny(403, "untrusted_cloud_ingress")
            return
        proto = values.get(b"x-forwarded-proto", "")
        proto_ok = (
            proto.split(",", 1)[0].strip().lower() == "https"
            if render_mode
            else proto == "https"
        )
        fhost = values.get(b"x-forwarded-host", self.host).split(",", 1)[0].strip().lower()
        if (
            not any(peer in network for network in self.proxies)
            or values.get(b"host", "").lower() not in (self.host, self.host + ":443")
            or not proto_ok
            or fhost not in (self.host, self.host + ":443")
            or (not render_mode and (b"forwarded" in values or b"x-real-ip" in values))
            or client.is_unspecified
            or client.is_multicast
        ):
            await deny(403, "untrusted_cloud_ingress")
            return
        origin = values.get(b"origin")
        if origin is not None and origin not in self.settings.backend_cors_origins:
            await deny(403, "cloud_origin_not_allowed")
            return
        path = scope["path"]
        if (
            path.startswith(("/docs", "/redoc", "/openapi.json", "/api/v1/stealth/"))
            or path == "/api/v1/development/demo"
        ):
            await deny(404, "not_found")
            return
        if not self.quota.allow(("all", str(client)), self.settings.cloud_request_limit):
            await deny(429, "too_many_requests")
            return
        if path.startswith("/api/v1/auth/") and not self.quota.allow(
            ("auth", str(client)), self.settings.cloud_auth_limit
        ):
            await deny(429, "too_many_auth_attempts")
            return
        post_writing = (
            scope["method"] == "POST"
            and path in ("/api/v1/ai/post-writing", "/api/v1/ai/post-writing/jobs")
            and self.settings.stealth_ai_provider == "openai"
        )
        max_body = (
            max(self.settings.cloud_body_limit, 30 * 1024 * 1024)
            if post_writing
            else self.settings.cloud_body_limit
        )
        try:
            length = int(values.get(b"content-length", "0"))
        except ValueError:
            length = -1
        if length < 0 or length > max_body:
            await deny(413, "request_body_too_large")
            return
        body, size = bytearray(), 0
        try:
            async with asyncio.timeout(30):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    size += len(message.get("body", b""))
                    if size > max_body:
                        await deny(413, "request_body_too_large")
                        return
                    body.extend(message.get("body", b""))
                    if not message.get("more_body", False):
                        break
        except TimeoutError:
            await deny(408, "request_body_timeout")
            return
        if size and values.get(b"content-type", "").split(";", 1)[0].lower() != "application/json":
            await deny(415, "json_body_required")
            return
        if b"content-length" in values and size != length:
            await deny(400, "body_length_mismatch")
            return
        scope.setdefault("state", {}).update(
            cloud_verified=True, cloud_client=str(client), runtime_settings=self.settings
        )
        scope["scheme"] = "https"

        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        await self.app(scope, replay, secure_send)
