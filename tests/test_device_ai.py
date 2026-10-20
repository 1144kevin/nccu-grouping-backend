import httpx
import pytest
from starlette.responses import JSONResponse

from app.core.device_security import DeviceSecurity
from app.core.device_test import device_ai_environment, validate_device_settings
from tests.test_device_test import settings


def ai_settings():
    return settings(
        core_device_ai_enabled=True,
        stealth_ai_provider="ollama_local",
        stealth_ollama_model="qwen3.5:4b",
        stealth_ollama_local_only_confirmed=True,
    )


def test_device_ai_opt_in_is_explicit_and_never_inherited():
    assert device_ai_environment({}, {})["STEALTH_AI_PROVIDER"] == "offline_rules"
    with pytest.raises(ValueError):
        device_ai_environment({"DEVICE_AI_ENABLED": "true"}, {})
    local = {
        "STEALTH_AI_PROVIDER": "ollama_local",
        "STEALTH_OLLAMA_MODEL": "qwen3.5:4b",
        "STEALTH_OLLAMA_LOCAL_ONLY_CONFIRMED": "true",
        "STEALTH_SEMANTIC_ENABLED": "true",
    }
    assert (
        device_ai_environment({"DEVICE_AI_ENABLED": "true"}, local)["STEALTH_SEMANTIC_ENABLED"]
        == "true"
    )
    bad = ai_settings()
    bad.core_device_test_enabled = False
    with pytest.raises(ValueError):
        validate_device_settings(bad)


@pytest.mark.asyncio
async def test_device_ai_boundary_rejects_before_inference():
    calls = []

    async def inner(scope, receive, send):
        calls.append(scope["path"])
        await JSONResponse({"safe": True})(scope, receive, send)

    gate = DeviceSecurity(inner, ai_settings())
    transport = httpx.ASGITransport(app=gate, client=("192.168.1.20", 1234))
    async with httpx.AsyncClient(transport=transport, base_url="https://192.168.1.10:8443") as c:
        path = "/api/v1/stealth-cloud/intake"
        assert (await c.post(path, json={})).status_code == 401
        assert (
            await c.post(path, headers={"Authorization": "Bearer fake"}, content=b"x" * 32769)
        ).status_code == 413
        assert (await c.post(path, headers={"X-Forwarded-For": "127.0.0.1"})).status_code == 403
        assert not calls
        for _ in range(11):
            assert (
                await c.post(path, headers={"Authorization": "Bearer fake"}, json={})
            ).status_code == 200
        assert (
            await c.post(path, headers={"Authorization": "Bearer fake"}, json={})
        ).status_code == 429
        assert len(calls) == 11
    for scheme, client in (("http", "192.168.1.20"), ("https", "192.168.1.21")):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=gate, client=(client, 1234)),
            base_url=f"{scheme}://192.168.1.10:8443",
        ) as c:
            assert (await c.post(path)).status_code == 403


@pytest.mark.asyncio
async def test_device_ai_disabled_but_manual_flow_is_available():
    async def inner(scope, receive, send):
        await JSONResponse({"manual": True})(scope, receive, send)

    gate = DeviceSecurity(inner, settings())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gate, client=("192.168.1.20", 1234)),
        base_url="https://192.168.1.10:8443",
    ) as c:
        assert (await c.post("/api/v1/stealth-cloud/intake")).status_code == 503
        assert (await c.post("/api/v1/stealth-cloud/signals")).status_code == 200
