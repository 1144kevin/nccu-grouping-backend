"""Run local FastAPI against the explicitly scoped Supabase development project."""

import argparse
import os
import sys
from pathlib import Path

import uvicorn

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Validate settings without listening")
    args = parser.parse_args()
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    from dotenv import dotenv_values

    config = dotenv_values(ROOT / ".local" / "supabase-dev.env")
    for name in ("SUPABASE_URL", "SUPABASE_PUBLISHABLE_KEY", "CORE_GOOGLE_IOS_ENABLED"):
        if config.get(name):
            os.environ[name] = config[name]
    local_ai = dotenv_values(ROOT / ".local" / "local-ai.env")
    media = dotenv_values(ROOT / ".local" / "media-signing.env")
    for name in ("CORE_MEDIA_ENABLED", "CORE_MEDIA_SIGNING_KEY"):
        if media.get(name):
            os.environ[name] = media[name]
    for name in (
        "STEALTH_AI_PROVIDER",
        "STEALTH_OLLAMA_MODEL",
        "STEALTH_OLLAMA_LOCAL_ONLY_CONFIRMED",
    ):
        if local_ai.get(name):
            os.environ[name] = local_ai[name]
    os.environ["ENVIRONMENT"] = "local"
    os.environ["CORE_LOCAL_ENABLED"] = "false"
    os.environ["CORE_SUPABASE_ENABLED"] = "true"
    os.environ["API_V1_PREFIX"] = "/api/v1"
    os.environ["BACKEND_CORS_ORIGINS"] = '["http://localhost:8081","http://127.0.0.1:8081"]'
    os.environ["DEBUG"] = "false"
    os.environ["CORE_DEVICE_TEST_ENABLED"] = "false"
    from app.core.config import get_settings
    from app.core_cloud.store import SupabaseCoreStore

    SupabaseCoreStore(get_settings())  # Fail before listening if configuration is invalid.
    if args.check:
        print("Scoped Supabase configuration valid. No listener or network requests started.")
        return
    print("Local API / Supabase development Auth + database. No deployment or paid AI.")
    print("API: http://127.0.0.1:8000/api/v1 ; Ctrl+C stops the server.")
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, proxy_headers=False)


if __name__ == "__main__":
    main()
