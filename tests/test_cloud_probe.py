import httpx
import pytest

from scripts.check_cloud_api import probe


def test_cloud_probe_only_reads_liveness_and_config_without_auth():
    calls = []

    def handle(request):
        calls.append(request.url.path)
        assert "authorization" not in request.headers and "apikey" not in request.headers
        return httpx.Response(
            200, json={"status": "ok" if request.url.path == "/health" else "configured"}
        )

    probe("https://api.team-domain.org/api/v1", transport=httpx.MockTransport(handle))
    assert calls == ["/health", "/ready"]


def test_cloud_probe_refuses_redirects_and_does_not_follow_them():
    def handle(request):
        return httpx.Response(302, headers={"location": "http://localhost:8000/private"})

    with pytest.raises(ValueError):
        probe("https://api.team-domain.org/api/v1", transport=httpx.MockTransport(handle))
