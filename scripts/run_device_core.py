"""Prepare/check a direct TLS development listener. Default is check-only, no network bind."""

import argparse
import json
import os
import ssl
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from dotenv import dotenv_values

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--serve", action="store_true", help="Explicitly start the configured TLS listener"
    )
    args = parser.parse_args()
    os.chdir(ROOT)
    profile = dotenv_values(ROOT / ".local/device-test.env")
    cloud = dotenv_values(ROOT / ".local/supabase-dev.env")
    host = profile.get("DEVICE_HOST", "")
    clients = [
        value.strip() for value in profile.get("DEVICE_CLIENTS", "").split(",") if value.strip()
    ]
    os.environ.update(
        {
            "ENVIRONMENT": "local",
            "CORE_LOCAL_ENABLED": "false",
            "CORE_SUPABASE_ENABLED": "true",
            "CORE_DEVICE_TEST_ENABLED": "true",
            "CORE_DEVICE_TEST_HOST": host,
            "CORE_DEVICE_TEST_CLIENTS": json.dumps(clients),
            "DEBUG": "false",
            "API_V1_PREFIX": "/api/v1",
            "BACKEND_CORS_ORIGINS": '["http://localhost:8081","http://127.0.0.1:8081"]',
            "STEALTH_AI_PROVIDER": "offline_rules",
            "CORE_MEDIA_ENABLED": "false",
        }
    )
    for name in ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "CORE_GOOGLE_IOS_ENABLED"):
        if cloud.get(name):
            os.environ[name] = cloud[name]
    media = dotenv_values(ROOT / ".local/media-signing.env")
    for name in ("CORE_MEDIA_ENABLED", "CORE_MEDIA_SIGNING_KEY"):
        if media.get(name):
            os.environ[name] = media[name]
    from app.core.config import get_settings
    from app.core.device_test import validate_device_settings
    from app.core_cloud.store import SupabaseCoreStore

    settings = get_settings()
    validate_device_settings(settings)
    SupabaseCoreStore(settings)
    cert = Path(profile.get("DEVICE_TLS_CERT", ""))
    key = Path(profile.get("DEVICE_TLS_KEY", ""))
    if not cert.is_file() or not key.is_file():
        raise ValueError("Configure existing certificate/key files in .local/device-test.env")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert, key)
    print(
        "Device profile valid. Certificate trust/SAN and actual iPhone connectivity "
        "require manual verification."
    )
    print(
        "Default: no listening, firewall changes, certificate installation, "
        "tunnel, deployment or paid AI."
    )
    if args.serve:
        import uvicorn

        print(f"Starting explicit private-LAN TLS test API at https://{host}:8443/api/v1")
        uvicorn.run(
            "app.main:app",
            host=host,
            port=8443,
            ssl_certfile=str(cert),
            ssl_keyfile=str(key),
            ssl_version=ssl.PROTOCOL_TLS_SERVER,
            proxy_headers=False,
            access_log=False,
        )


if __name__ == "__main__":
    try:
        main()
    except (ValueError, ssl.SSLError) as error:
        raise SystemExit(str(error)) from None
