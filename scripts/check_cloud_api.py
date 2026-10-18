"""URL check by default; explicit --probe checks public HTTPS liveness/configuration only."""

import argparse
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.cloud_runtime import https_address


def probe(api_url: str, *, transport=None):
    parsed = https_address(api_url, "/api/v1")
    origin = f"https://{parsed.netloc}"
    with httpx.Client(
        base_url=origin, timeout=5, trust_env=False, follow_redirects=False, transport=transport
    ) as client:
        for path, expected in (("/health", "ok"), ("/ready", "configured")):
            result = client.get(path)
            if result.status_code != 200:
                raise ValueError("Liveness/configuration probe did not return HTTP 200")
            try:
                payload = result.json()
            except ValueError:
                raise ValueError("Liveness/configuration probe returned non-JSON") from None
            if not isinstance(payload, dict) or payload.get("status") != expected:
                raise ValueError("Liveness/configuration response did not match the contract")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--probe", action="store_true")
    args = parser.parse_args()
    try:
        https_address(args.url, "/api/v1")
        if args.probe:
            probe(args.url)
            print(
                "HTTPS liveness/configuration passed. Auth, database and Storage were not tested."
            )
        else:
            print(
                "URL format valid. No network request made; "
                "add --probe after an authorized deployment."
            )
    except (ValueError, httpx.HTTPError):
        raise SystemExit(
            "Cloud check failed. Verify URL, TLS and ingress; response details not printed."
        ) from None


if __name__ == "__main__":
    main()
