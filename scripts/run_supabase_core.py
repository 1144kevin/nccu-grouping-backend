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
    parser.add_argument(
        "--ai-provider", choices=("gemini", "openai"), default="gemini",
        help="Select the AI provider. OpenAI is a local prototype only.",
    )
    args = parser.parse_args()
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    from dotenv import dotenv_values

    config = dotenv_values(ROOT / ".local" / "supabase-dev.env")
    for name in (
        "SUPABASE_URL",
        "SUPABASE_PUBLISHABLE_KEY",
        "CORE_GOOGLE_IOS_ENABLED",
        "CORE_GOOGLE_PUBLIC_WEB_CALLBACK",
    ):
        if config.get(name):
            os.environ[name] = config[name]
    local_ai = dotenv_values(ROOT / ".local" / "local-ai.env")
    # Entered by the user locally. Never print, expose to the frontend, or commit.
    gemini = dotenv_values(ROOT / ".local" / "gemini.env")
    for name in ("STEALTH_CONVERSATION_PROVIDER", "STEALTH_GEMINI_MODEL", "STEALTH_GEMINI_API_KEY"):
        if gemini.get(name):
            os.environ[name] = gemini[name]
    moderation = dotenv_values(ROOT / ".local" / "moderation.env")
    enabled = moderation.get("CORE_MODERATION_ENABLED", "false").lower()
    if enabled not in ("true", "false"):
        raise ValueError("CORE_MODERATION_ENABLED must be true or false")
    os.environ["CORE_MODERATION_ENABLED"] = enabled
    media = dotenv_values(ROOT / ".local" / "media-signing.env")
    for name in ("CORE_MEDIA_ENABLED", "CORE_MEDIA_SIGNING_KEY"):
        if media.get(name):
            os.environ[name] = media[name]
    for name in (
        "STEALTH_AI_PROVIDER",
        "STEALTH_OLLAMA_MODEL",
        "STEALTH_OLLAMA_LOCAL_ONLY_CONFIRMED",
        "STEALTH_SEMANTIC_ENABLED",
        "STEALTH_EMBEDDING_MODEL",
    ):
        if local_ai.get(name):
            os.environ[name] = local_ai[name]
    # Cloud AI is configured server-side; the existing key is only read by this process.
    os.environ["STEALTH_AI_PROVIDER"] = args.ai_provider
    os.environ["STEALTH_CONVERSATION_PROVIDER"] = args.ai_provider
    os.environ["STEALTH_SEMANTIC_ENABLED"] = "true"
    if args.ai_provider == "openai":
        if not args.check:
            raise RuntimeError(
                "OpenAI local prototype cannot start against the shared Supabase project. "
                "Use isolated tests until a separate test database is configured."
            )
        private_config = dotenv_values(ROOT / ".local" / "openai-local.env")
        os.environ["LOCAL_OPENAI_API_KEY"] = private_config.get("OPENAI_API_KEY", "") or ""
    os.environ["ENVIRONMENT"] = "local"
    os.environ["CORE_LOCAL_ENABLED"] = "false"
    os.environ["CORE_SUPABASE_ENABLED"] = "true"
    os.environ["API_V1_PREFIX"] = "/api/v1"
    os.environ["BACKEND_CORS_ORIGINS"] = (
        '["http://localhost:8081","http://127.0.0.1:8081","http://localhost:8082"]'
    )
    os.environ["DEBUG"] = "false"
    # Share the current device landing page with desktop registrations too.
    device = dotenv_values(ROOT / ".local" / "device-test.env")
    from app.core.device_test import private_ipv4

    host = device.get("DEVICE_HOST", "")
    os.environ["CORE_EMAIL_REDIRECT_URL"] = (
        f"https://{host}:8443/api/v1/auth/email-confirmed" if private_ipv4(host) else ""
    )
    os.environ["CORE_DEVICE_TEST_ENABLED"] = "false"
    os.environ["CORE_CLOUD_ENABLED"] = "false"
    from app.core.config import get_settings
    from app.core_cloud.store import SupabaseCoreStore

    SupabaseCoreStore(get_settings())  # Fail before listening if configuration is invalid.
    if args.check:
        print("Scoped Supabase configuration valid. No listener or network requests started.")
        return
    settings = get_settings()
    if settings.stealth_ai_provider == "ollama_local":
        from scripts.local_model_runtime import ensure_local_model

        if ensure_local_model():
            print("Existing local model service is ready (127.0.0.1:11434).")
        else:
            print("Local models unavailable. Run scripts/run_local_model.py; no models downloaded.")
    print("Local API / Supabase Auth + database. Guided chat uses the configured provider.")
    print("API: http://127.0.0.1:8000/api/v1 ; Ctrl+C stops the server.")
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, proxy_headers=False)


if __name__ == "__main__":
    main()
