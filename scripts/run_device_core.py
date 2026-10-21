"""Prepare/check a direct TLS development listener. Default is check-only, no network bind."""

import argparse
import ssl
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--serve", action="store_true", help="Explicitly start the configured TLS listener"
    )
    args = parser.parse_args()
    from app.core.device_setup import load_device, server_chain

    settings, (cert, key, ca), tls = load_device(ROOT, apply_environment=True)
    host = settings.core_device_test_host
    print(
        "Device profile and TLS SAN/chain/key/validity checks passed. "
        "iPhone trust and actual connectivity still require device verification."
    )
    print("Public CA SHA256: " + tls["ca_sha256"])
    print(
        "Google iOS: "
        + (
            "configured; real login pending"
            if settings.core_google_ios_enabled
            else "disabled; confirm exact Supabase native redirect first"
        )
    )
    print(
        "Local AI: "
        + (
            "explicitly enabled; authenticated API only"
            if settings.core_device_ai_enabled
            else "disabled"
        )
    )
    print(
        "Default: no listening, firewall changes, certificate installation, "
        "tunnel, deployment or paid AI."
    )
    if args.serve:
        if sys.platform == "win32":
            from scripts.device_link import network_problem, windows_networks

            problem = network_problem(host, windows_networks())
            if problem:
                raise ValueError(problem)
        import uvicorn

        print(f"Starting explicit private-LAN TLS test API at https://{host}:8443/api/v1")
        uvicorn.run(
            "app.main:app",
            host=host,
            port=8443,
            ssl_certfile=str(server_chain(cert, ca)),
            ssl_keyfile=str(key),
            ssl_version=ssl.PROTOCOL_TLS_SERVER,
            proxy_headers=False,
            access_log=False,
            server_header=False,
            limit_concurrency=64,
            h11_max_incomplete_event_size=16_384,
        )


if __name__ == "__main__":
    try:
        main()
    except (ValueError, ssl.SSLError) as error:
        raise SystemExit(str(error)) from None
