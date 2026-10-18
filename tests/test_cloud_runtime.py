from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.cloud_runtime import validate_cloud_settings
from app.core.cloud_security import CloudSecurity, WindowQuota
from app.core.config import Settings
from app.core_cloud.store import SupabaseCoreStore
from app.core_local.schemas import ProfileView
from app.main import create_app


def configured(**changes):
    values = dict(
        _env_file=None,
        environment="cloud",
        core_cloud_enabled=True,
        core_supabase_enabled=True,
        cloud_public_api_url="https://api.team-domain.org/api/v1",
        cloud_proxy_networks=["127.0.0.1/32"],
        backend_cors_origins=[],
        supabase_url="https://axdwbfhrkbovgrbngrrh.supabase.co",
        supabase_publishable_key="sb_publishable_synthetic_test_key",
    )
    values.update(changes)
    return Settings(**values)


def headers(**changes):
    values = {"x-forwarded-proto": "https", "x-forwarded-for": "198.51.100.8"}
    values.update(changes)
    return values


@pytest.mark.parametrize(
    "changes",
    [
        {"core_cloud_enabled": False},
        {"environment": "local"},
        {"core_local_enabled": True},
        {"core_device_test_enabled": True},
        {"stealth_local_enabled": True},
        {"debug": True},
        {"cloud_public_api_url": "http://api.team-domain.org/api/v1"},
        {"cloud_public_api_url": "https://127.0.0.1/api/v1"},
        {"cloud_public_api_url": "https://x:secret@api.team-domain.org/api/v1"},
        {"cloud_public_api_url": "https://api.team-domain.org/api/v1?token=secret"},
        {"cloud_proxy_networks": []},
        {"cloud_proxy_networks": ["0.0.0.0/0"]},
        {"cloud_proxy_networks": ["10.0.0.0/8"]},
        {"cloud_workers": 2},
        {"cloud_instances": 2},
        {"stealth_ai_provider": "ollama_local"},
        {"stealth_semantic_enabled": True},
        {"openai_api_key": "synthetic-secret"},
        {"supabase_service_role_key": "synthetic-secret"},
        {"core_supabase_enabled": False},
        {"supabase_url": "https://another-project.supabase.co"},
        {"supabase_publishable_key": "sb_secret_synthetic"},
        {"backend_cors_origins": ["*"]},
        {"backend_cors_origins": ["http://localhost:8081"]},
        {"cloud_auth_limit": 0},
        {"cloud_body_limit": 10_000_000},
        {"core_media_enabled": True},
        {"cloud_google_web_callback": "https://web.team-domain.org/auth/callback"},
    ],
)
def test_cloud_configuration_fails_closed(changes):
    with pytest.raises(ValueError):
        validate_cloud_settings(configured(**changes))


def test_native_only_settings_are_valid_without_admin_or_ai_credentials():
    validate_cloud_settings(configured())


@pytest.mark.parametrize(
    "override",
    [
        {"x-forwarded-proto": "http"},
        {"x-forwarded-for": "198.51.100.8, 127.0.0.1"},
        {"x-forwarded-for": "not-an-IP"},
        {"x-forwarded-host": "evil.invalid"},
        {"host": "evil.invalid"},
        {"forwarded": "proto=https"},
        {"x-real-ip": "198.51.100.8"},
        {"origin": "https://evil.invalid"},
    ],
)
def test_proxy_spoofing_and_bad_origins_are_rejected(override):
    with TestClient(
        create_app(configured()), base_url="https://api.team-domain.org", client=("127.0.0.1", 1234)
    ) as client:
        assert (
            client.get("/api/v1/development/readiness", headers=headers(**override)).status_code
            == 403
        )


def test_untrusted_network_cannot_spoof_forwarded_headers():
    with TestClient(
        create_app(configured()),
        base_url="https://api.team-domain.org",
        client=("198.51.100.2", 1234),
    ) as client:
        assert client.get("/api/v1/development/readiness", headers=headers()).status_code == 403


def test_cloud_capabilities_health_and_development_boundaries():
    with TestClient(
        create_app(configured()), base_url="https://api.team-domain.org", client=("127.0.0.1", 1234)
    ) as client:
        assert client.get("/health").json() == {"status": "ok"}
        assert client.get("/api/v1/development/readiness").status_code == 403
        response = client.get("/api/v1/development/readiness", headers=headers())
        assert response.json()["profile"] == "cloud"
        assert response.json()["ai_provider"] == "offline_rules"
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["strict-transport-security"] == "max-age=31536000"
        assert len(response.headers["x-request-id"]) == 32
        for path in ("/docs", "/openapi.json", "/api/v1/stealth/agent"):
            assert client.get(path, headers=headers()).status_code == 404
        assert client.delete("/api/v1/development/demo", headers=headers()).status_code == 404


def test_cloud_uses_existing_user_auth_and_core_store(monkeypatch):
    actor = uuid4()

    def authenticate(self, token):
        assert token == "synthetic-user-token"
        assert self.settings.core_cloud_enabled
        return ProfileView(
            id=actor,
            email="isolated@example.test",
            display_name="隔離測試",
            created_at=datetime.now(UTC),
            auth_mode="supabase",
            email_verified=True,
        )

    def posts(self, owner, *args):
        assert owner == actor
        return {"items": [], "next_cursor": None}

    monkeypatch.setattr(SupabaseCoreStore, "authenticate", authenticate)
    monkeypatch.setattr(SupabaseCoreStore, "posts", posts)
    with TestClient(
        create_app(configured()), base_url="https://api.team-domain.org", client=("127.0.0.1", 1234)
    ) as client:
        assert client.get("/api/v1/posts", headers=headers()).status_code == 401
        assert (
            client.get(
                "/api/v1/posts", headers=headers(authorization="Bearer synthetic-user-token")
            ).json()["items"]
            == []
        )


def test_auth_quota_uses_verified_client_not_shared_gateway_ip():
    with TestClient(
        create_app(configured(cloud_auth_limit=2)),
        base_url="https://api.team-domain.org",
        client=("127.0.0.1", 1234),
    ) as client:
        for _ in range(2):
            assert client.post("/api/v1/auth/login", headers=headers(), json={}).status_code == 422
        response = client.post("/api/v1/auth/login", headers=headers(), json={})
        assert response.status_code == 429 and response.headers["retry-after"] == "60"
        assert (
            client.post(
                "/api/v1/auth/login",
                headers=headers(**{"x-forwarded-for": "198.51.100.9"}),
                json={},
            ).status_code
            == 422
        )


def test_quota_is_bounded_atomic_and_expires():
    quota = WindowQuota(max_keys=1)
    with ThreadPoolExecutor(max_workers=10) as pool:
        accepted = list(pool.map(lambda _: quota.allow("a", 5, now=10), range(20)))
    assert sum(accepted) == 5
    assert not quota.allow("b", 5, now=10)
    assert quota.allow("b", 5, now=71)


def test_body_limits_and_json_gate_precede_application_work():
    with TestClient(
        create_app(configured(cloud_body_limit=1024)),
        base_url="https://api.team-domain.org",
        client=("127.0.0.1", 1234),
    ) as client:
        assert (
            client.post("/api/v1/auth/login", headers=headers(), content=b"x" * 1025).status_code
            == 413
        )
        assert (
            client.post("/api/v1/auth/login", headers=headers(), content=b"secret").status_code
            == 415
        )
        response = client.post("/api/v1/auth/login", headers=headers(), json={"password": 123})
        assert response.status_code == 422 and "123" not in response.text


def test_cloud_cors_is_exact_and_does_not_enable_cookie_credentials():
    with TestClient(
        create_app(configured(backend_cors_origins=["https://web.team-domain.org"])),
        base_url="https://api.team-domain.org",
        client=("127.0.0.1", 1234),
    ) as client:
        response = client.options(
            "/api/v1/posts",
            headers=headers(
                origin="https://web.team-domain.org",
                **{
                    "access-control-request-method": "POST",
                    "access-control-request-headers": "authorization,idempotency-key",
                },
            ),
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "https://web.team-domain.org"
        assert "access-control-allow-credentials" not in response.headers


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "duplicate,oversize,expected", [(True, False, 400), (False, True, 413), (False, False, 200)]
)
async def test_streaming_body_and_duplicate_headers(duplicate, oversize, expected):
    sent, seen = [], []

    async def application(scope, receive, send):
        seen.append(await receive())
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    messages = iter(
        [
            {"type": "http.request", "body": b"x" * (700 if oversize else 3), "more_body": True},
            {"type": "http.request", "body": b"x" * (700 if oversize else 3), "more_body": False},
        ]
    )

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    raw = [
        (b"host", b"api.team-domain.org"),
        (b"x-forwarded-proto", b"https"),
        (b"x-forwarded-for", b"198.51.100.8"),
        (b"content-type", b"application/json"),
    ]
    if duplicate:
        raw.append((b"x-forwarded-for", b"198.51.100.9"))
    scope = {
        "type": "http",
        "path": "/api/v1/posts",
        "method": "POST",
        "headers": raw,
        "client": ("127.0.0.1", 1234),
        "scheme": "http",
    }
    await CloudSecurity(application, configured(cloud_body_limit=1024))(scope, receive, send)
    assert next(m for m in sent if m["type"] == "http.response.start")["status"] == expected
    assert bool(seen) == (expected == 200)
    if seen:
        assert seen[0]["body"] == b"xxxxxx"
        assert scope["state"]["cloud_client"] == "198.51.100.8"


def test_cloud_native_google_preserves_fixed_callback_and_disables_web_by_default(monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    def request(self, *args, **kwargs):
        return {"external": {"google": True}}

    monkeypatch.setattr(SupabaseCoreStore, "_request", request)
    with TestClient(
        create_app(configured(core_google_ios_enabled=True)),
        base_url="https://api.team-domain.org",
        client=("127.0.0.1", 1234),
    ) as client:
        response = client.post(
            "/api/v1/auth/google/start", headers=headers(), json={"client": "ios"}
        )
        assert response.status_code == 200
        assert parse_qs(urlsplit(response.json()["authorize_url"]).query)["redirect_to"] == [
            "nccugrouping://auth/callback"
        ]
        assert (
            client.post(
                "/api/v1/auth/google/start", headers=headers(), json={"client": "web"}
            ).status_code
            == 403
        )
