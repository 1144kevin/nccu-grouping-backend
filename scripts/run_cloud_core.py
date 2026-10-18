"""Check-only by default; --serve is an explicit future cloud startup action."""

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from dotenv import dotenv_values
    from pydantic import ValidationError

    from app.core.cloud_runtime import validate_cloud_settings
    from app.core.config import Settings, get_settings

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, help="Optional explicit private environment file")
    parser.add_argument("--serve", action="store_true", help="Start the validated cloud API")
    parser.add_argument(
        "--write-client-profile",
        action="store_true",
        help="Write public URL-only Apple profile to .local/apple-release.json",
    )
    args = parser.parse_args()
    if args.env_file:
        if not args.env_file.is_file():
            raise SystemExit("Cloud environment file does not exist")
        allowed = {name.upper() for name in Settings.model_fields} | {"PORT", "CLOUD_BIND_HOST"}
        values = dotenv_values(args.env_file)
        if values.keys() - allowed:
            raise SystemExit("Unknown environment variable names; no values were printed")
        os.environ.update({k: v for k, v in values.items() if v is not None})
    try:
        settings = Settings(_env_file=None)
        validate_cloud_settings(settings)
        for name in ("WEB_CONCURRENCY", "UVICORN_WORKERS"):
            if os.environ.get(name, "1") != "1":
                raise ValueError("Exactly one Uvicorn worker is required")
        if os.environ.get("UVICORN_RELOAD", "false").lower() not in ("false", "0", ""):
            raise ValueError("Cloud autoreload must remain disabled")
        try:
            port = int(os.environ.get("PORT", "8000"))
        except ValueError:
            raise ValueError("PORT must be a valid integer") from None
        host = os.environ.get("CLOUD_BIND_HOST", "127.0.0.1")
        if not 1 <= port <= 65535 or host not in ("127.0.0.1", "0.0.0.0"):
            raise ValueError("Invalid explicit bind host or port")
    except ValidationError as error:
        fields = ", ".join(str(item["loc"][0]) for item in error.errors())
        raise SystemExit("Invalid cloud setting fields: " + fields) from None
    except ValueError as error:
        raise SystemExit("Cloud preflight failed: " + str(error)) from None
    get_settings.cache_clear()
    print(
        "Cloud configuration checks passed. Scoped Supabase; one worker/instance; free rules only."
    )
    print("No remote probes, migrations, deployment, paid AI or listener started by checks.")
    if args.write_client_profile:
        target = ROOT / ".local" / "apple-release.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps({"profile": "release", "api": settings.cloud_public_api_url}, indent=2)
            + "\n",
            encoding="utf-8",
        )
        print("Wrote .local/apple-release.json (public API URL only; no keys).")
    if not args.serve:
        return
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        workers=1,
        proxy_headers=False,
        access_log=False,
        server_header=False,
        timeout_keep_alive=5,
        limit_concurrency=64,
        h11_max_incomplete_event_size=16384,
    )


if __name__ == "__main__":
    main()
