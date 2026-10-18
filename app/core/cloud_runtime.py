"""Fail-closed configuration for the scoped Supabase cloud runtime."""

import ipaddress
import re
from urllib.parse import urlsplit

from app.core.config import Settings

SCOPED_PROJECT = "https://axdwbfhrkbovgrbngrrh.supabase.co"


def https_address(value: str, path: str | None = None):
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        port = parsed.port
    except ValueError:
        raise ValueError("Invalid HTTPS address syntax") from None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("HTTPS DNS address required")
    if (
        parsed.scheme != "https"
        or not host
        or "." not in host
        or host.endswith((".local", ".localhost", ".test", ".invalid", ".example"))
        or host == "localhost"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or port not in (None, 443)
        or (path is not None and parsed.path != path)
        or not re.fullmatch(r"[a-z0-9.-]+", host)
    ):
        raise ValueError("Exact HTTPS DNS address required; no credentials or query")
    return parsed


def validate_cloud_settings(settings: Settings) -> None:
    if not settings.core_cloud_enabled or settings.environment != "cloud":
        raise ValueError("Explicit CORE_CLOUD_ENABLED=true and ENVIRONMENT=cloud are required")
    if (
        not settings.core_supabase_enabled
        or settings.core_local_enabled
        or settings.core_device_test_enabled
        or settings.stealth_local_enabled
        or settings.debug
    ):
        raise ValueError(
            "Cloud requires Supabase only; disable local/device/SQLite adapters and DEBUG"
        )
    if settings.api_v1_prefix != "/api/v1":
        raise ValueError("Cloud API prefix must be /api/v1")
    https_address(settings.cloud_public_api_url, "/api/v1")
    if settings.supabase_url.rstrip("/") != SCOPED_PROJECT:
        raise ValueError("Only the authorized Supabase project is supported")
    if not re.fullmatch(r"sb_publishable_[A-Za-z0-9_-]{16,}", settings.supabase_publishable_key):
        raise ValueError("Configure the scoped project's Publishable key")
    if settings.supabase_service_role_key or settings.openai_api_key:
        raise ValueError("Cloud runtime does not accept admin or paid AI keys")
    if settings.stealth_ai_provider != "offline_rules":
        raise ValueError(
            "Cloud runtime supports free rule drafts only; no local/paid model connection"
        )
    if settings.cloud_workers != 1 or settings.cloud_instances != 1:
        raise ValueError("PKCE and quotas require exactly one worker and one instance")
    if not 1 <= len(settings.cloud_proxy_networks) <= 32:
        raise ValueError("Configure exact trusted ingress IPs or narrow networks")
    for value in settings.cloud_proxy_networks:
        try:
            network = ipaddress.ip_network(value, strict=True)
        except ValueError:
            raise ValueError("Invalid proxy network format") from None
        if (
            network.prefixlen < (24 if network.version == 4 else 64)
            or network.network_address.is_unspecified
            or network.is_multicast
        ):
            raise ValueError("Proxy networks must be narrow, not wildcard/default routes")
    if not 1024 <= settings.cloud_body_limit <= 6 * 1024 * 1024:
        raise ValueError("Cloud body limit must be between 1 KiB and 6 MiB")
    if not 1 <= settings.cloud_auth_limit <= 60 or not 1 <= settings.cloud_request_limit <= 600:
        raise ValueError("Cloud request quotas must remain enabled and bounded")
    for origin in settings.backend_cors_origins:
        parsed = https_address(origin, "")
        if origin != f"https://{parsed.netloc}":
            raise ValueError("CORS requires exact HTTPS origins without paths")
    if settings.cloud_google_web_callback:
        parsed = https_address(settings.cloud_google_web_callback, "/auth/callback")
        if f"https://{parsed.netloc}" not in settings.backend_cors_origins:
            raise ValueError("Web Google callback must belong to an explicit CORS origin")
    if settings.core_media_enabled and not re.fullmatch(
        r"[0-9a-f]{64}", settings.core_media_signing_key.get_secret_value()
    ):
        raise ValueError("Enabled images require the existing private 32-byte hex signing key")
