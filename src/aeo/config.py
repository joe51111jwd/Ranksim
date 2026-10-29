"""Environment-backed settings."""

from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./data/aeo.db"
    search_provider: str = "auto"
    llm_provider: str = "auto"
    embedding_provider: str = "auto"
    brave_api_key: str | None = None
    serpapi_key: str | None = None
    dataforseo_login: str | None = None
    dataforseo_password: str | None = None
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 64
    process_jobs_inline: bool = False
    worker_poll_seconds: float = 1.0
    crawl_delay_seconds: float = 0.25
    crawl_user_agent: str = "AEOSimulatorBot/0.1 (+https://example.com/aeo-simulator)"
    crawl_allow_private: bool = False
    request_timeout_seconds: float = 20.0
    rrf_k: int = 60
    final_passages: int = 8
    ranksim_database_url: str = "sqlite:///./data/ranksim.db"
    ranksim_serp_depth: int = 10
    ranksim_max_pages: int = 1000
    ranksim_authority: str = "off"

    @field_validator(
        "brave_api_key",
        "openai_api_key",
        "serpapi_key",
        "dataforseo_login",
        "dataforseo_password",
        mode="before",
    )
    @classmethod
    def blank_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("search_provider", "llm_provider", "embedding_provider")
    @classmethod
    def lower_name(cls, value: str) -> str:
        return value.strip().lower()


def get_settings() -> Settings:
    return Settings()
