"""Unit/API tests must not spend credits or contact real HTTP services."""

import httpx
import pytest


@pytest.fixture(autouse=True)
def forbid_real_http_network(monkeypatch):
    def deny_sync(*_args, **_kwargs):
        raise AssertionError("Real HTTP requests are disabled in automated tests")

    async def deny_async(*_args, **_kwargs):
        raise AssertionError("Real HTTP requests are disabled in automated tests")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", deny_sync)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", deny_async)
