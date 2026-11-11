import json
import os
from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "政缺一咖 API"
    environment: str = "local"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/nccu_grouping"
    backend_cors_origins: list[str] = ["http://localhost:8081", "http://127.0.0.1:8081"]

    # Deliberately local-only: email/password development accounts, never Supabase credentials.
    core_local_enabled: bool = False
    core_local_db_path: str = ".local/core.sqlite3"
    core_supabase_enabled: bool = False
    # Enable only after adding the exact native callback to the development Auth allowlist.
    core_google_ios_enabled: bool = False
    # Exact HTTPS callback for the public web gateway; browser input cannot override it.
    core_google_public_web_callback: str = ""
    core_email_redirect_url: str = ""
    core_media_enabled: bool = False
    core_media_signing_key: SecretStr = SecretStr("")
    # Explicit opt-in for direct TLS on a private LAN; never enabled by the local runner.
    core_device_test_enabled: bool = False
    core_device_test_host: str = ""
    core_device_test_clients: list[str] = []
    core_device_ai_enabled: bool = False
    core_moderation_enabled: bool = False
    # Separate cloud runtime: TLS terminates at one explicitly trusted ingress hop.
    core_cloud_enabled: bool = False
    cloud_public_api_url: str = ""
    cloud_proxy_mode: Literal["strict", "render"] = "strict"
    cloud_proxy_networks: list[str] = []
    cloud_workers: int = 1
    cloud_instances: int = 1
    cloud_body_limit: int = 6 * 1024 * 1024
    cloud_request_limit: int = 240
    cloud_auth_limit: int = 20
    cloud_google_web_callback: str = ""

    supabase_url: str = ""
    supabase_publishable_key: str = ""
    supabase_jwks_url: str = ""
    supabase_jwt_audience: str = "authenticated"
    supabase_service_role_key: str = ""
    supabase_storage_bucket: str = "post-media"
    openai_api_key: str = ""
    local_openai_api_key: SecretStr = SecretStr("")
    openai_budget_database_url: SecretStr = SecretStr("")
    cloud_openai_schema_ready: bool = False

    # Local-only prototype. Never enable this adapter in a deployed environment.
    stealth_local_enabled: bool = False
    stealth_local_db_path: str = ".local/stealth.sqlite3"
    stealth_ai_provider: Literal["offline_rules", "ollama_local", "gemini", "openai"] = (
        "offline_rules"
    )
    stealth_ollama_model: str = ""
    stealth_ollama_local_only_confirmed: bool = False
    stealth_semantic_enabled: bool = False
    stealth_embedding_model: str = "qwen3-embedding:0.6b"
    # Separate opt-in: only the guided conversation may leave this server.
    stealth_conversation_provider: Literal["local", "gemini", "openai"] = "local"
    stealth_gemini_model: Literal["gemini-3.5-flash-lite"] = "gemini-3.5-flash-lite"
    stealth_gemini_api_key: SecretStr = SecretStr("")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        hide_input_in_errors=True,
        enable_decoding=False,
    )

    @field_validator(
        "backend_cors_origins",
        "cloud_proxy_networks",
        "core_device_test_clients",
        mode="before",
    )
    @classmethod
    def _parse_str_list(cls, value: object) -> object:
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            if text.startswith("["):
                return json.loads(text)
            return [item.strip() for item in text.split(",") if item.strip()]
        return value


def cloud_settings_from_env() -> Settings:
    overrides: dict[str, object] = {}
    if "BACKEND_CORS_ORIGINS" not in os.environ:
        overrides["backend_cors_origins"] = []
    if os.environ.get("RENDER", "").lower() == "true":
        if "CLOUD_PROXY_MODE" not in os.environ:
            overrides["cloud_proxy_mode"] = "render"
        if "CLOUD_PROXY_NETWORKS" not in os.environ:
            overrides["cloud_proxy_networks"] = [
                "10.0.0.0/8",
                "172.16.0.0/12",
                "192.168.0.0/16",
                "127.0.0.1/32",
                "::1/128",
                "fc00::/7",
            ]
    return Settings(_env_file=None, **overrides)


@lru_cache
def get_settings() -> Settings:
    if (
        os.environ.get("ENVIRONMENT") == "cloud"
        or os.environ.get("CORE_CLOUD_ENABLED", "").lower() == "true"
    ):
        return cloud_settings_from_env()
    return Settings()
