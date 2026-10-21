from urllib.parse import urlsplit

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core_cloud.email_confirmation import PATH, email_redirect
from app.main import create_app


def test_confirmation_page_requires_no_login_and_sets_no_cookies_or_external_requests():
    settings = Settings(
        _env_file=None,
        core_device_test_enabled=True,
        core_supabase_enabled=True,
        core_device_test_host="192.168.1.10",
        core_device_test_clients=["192.168.1.20"],
    )
    with TestClient(
        create_app(settings), base_url="https://192.168.1.10:8443", client=("192.168.1.20", 50000)
    ) as client:
        response = client.get(PATH)
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "set-cookie" not in response.headers
        assert "default-src 'none'" in response.headers["content-security-policy"]
        assert "history.replaceState" in response.text
        assert "localStorage" not in response.text and "fetch(" not in response.text
        assert "回 Expo Go" in response.text
        assert client.get(PATH + "?error_description=PRIVATE-DETAILS").text == response.text


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/api/v1/auth/email-confirmed",
        "http://192.168.1.10:8443" + PATH,
        "https://192.168.1.10:8443" + PATH + "?next=evil",
        "https://192.168.1.10:8443" + PATH + "#secret",
    ],
)
def test_redirect_rejects_public_origins_http_and_any_parameters(url):
    with pytest.raises(HTTPException):
        email_redirect(Settings(_env_file=None, core_email_redirect_url=url))


def test_redirect_device_host_overrides_stale_setting():
    settings = Settings(
        _env_file=None,
        core_device_test_enabled=True,
        core_device_test_host="192.168.1.10",
        core_email_redirect_url="https://stale.invalid",
    )
    assert urlsplit(email_redirect(settings)).netloc == "192.168.1.10:8443"
