from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.core.config import Settings
from app.core.device_test import validate_device_settings
from app.core_local.dependencies import require_local_mode


def settings(**changes):
    return Settings(
        _env_file=None,
        core_supabase_enabled=True,
        core_device_test_enabled=True,
        core_device_test_host="192.168.1.10",
        core_device_test_clients=["192.168.1.20"],
        **changes,
    )


@pytest.mark.parametrize(
    "host", ["0.0.0.0", "127.0.0.1", "8.8.8.8", "169.254.1.1", "192.168.1.0/24"]
)
def test_invalid_device_host(host):
    value = settings()
    value.core_device_test_host = host
    with pytest.raises(ValueError):
        validate_device_settings(value)


@pytest.mark.parametrize(
    "scheme,client,host,headers,allowed",
    [
        ("https", "192.168.1.20", "192.168.1.10", [], True),
        ("http", "192.168.1.20", "192.168.1.10", [], False),
        ("https", "192.168.1.21", "192.168.1.10", [], False),
        ("https", "192.168.1.20", "evil.example", [], False),
        ("https", "192.168.1.20", "192.168.1.10", [(b"x-forwarded-for", b"127.0.0.1")], False),
    ],
)
def test_exact_device_transport_gate(monkeypatch, scheme, client, host, headers, allowed):
    monkeypatch.setattr("app.core_local.dependencies.get_settings", settings)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": scheme,
            "path": "/",
            "query_string": b"",
            "client": (client, 1234),
            "server": (host, 8443),
            "headers": [(b"host", host.encode()), *headers],
        }
    )
    if allowed:
        require_local_mode(request)
    else:
        with pytest.raises(HTTPException):
            require_local_mode(request)


def test_default_profile_stays_loopback_only(monkeypatch):
    monkeypatch.setattr(
        "app.core_local.dependencies.get_settings",
        lambda: Settings(_env_file=None, core_supabase_enabled=True),
    )
    request = SimpleNamespace(client=SimpleNamespace(host="192.168.1.20"), headers={})
    with pytest.raises(HTTPException, match="loopback_only"):
        require_local_mode(request)
