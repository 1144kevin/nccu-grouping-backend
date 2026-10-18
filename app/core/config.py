from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
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
    core_media_enabled: bool = False
    core_media_signing_key: SecretStr = SecretStr("")

    supabase_url: str = ""
    supabase_publishable_key: str = ""
    supabase_jwks_url: str = ""
    supabase_jwt_audience: str = "authenticated"
    supabase_service_role_key: str = ""
    supabase_storage_bucket: str = "post-media"
    openai_api_key: str = ""

    # Local-only prototype. Never enable this adapter in a deployed environment.
    stealth_local_enabled: bool = False
    stealth_local_db_path: str = ".local/stealth.sqlite3"
    stealth_ai_provider: Literal["offline_rules", "ollama_local"] = "offline_rules"
    stealth_ollama_model: str = ""
    stealth_ollama_local_only_confirmed: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
