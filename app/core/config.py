from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "政缺一咖 API"
    environment: str = "local"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/nccu_grouping"
    backend_cors_origins: list[str] = ["http://localhost:8081"]

    supabase_url: str = ""
    supabase_jwks_url: str = ""
    supabase_jwt_audience: str = "authenticated"
    supabase_service_role_key: str = ""
    supabase_storage_bucket: str = "post-media"
    openai_api_key: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
