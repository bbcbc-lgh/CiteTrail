from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_name: str = "自动化深度研究智能体"
    api_prefix: str = "/api"
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_model_id: str = "gpt-4o-mini"
    llm_timeout: float = 60.0

    search_api: str = "duckduckgo"
    tavily_api_key: str = ""
    perplexity_api_key: str = ""
    searxng_url: str = ""
    search_result_limit: int = Field(default=5, ge=1, le=10)
    search_timeout: float = 15.0

    max_tasks: int = Field(default=4, ge=3, le=5)
    max_web_research_loops: int = Field(default=1, ge=1, le=3)
    workspace_dir: Path = PROJECT_DIR / "workspace"
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=PROJECT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def has_llm(self) -> bool:
        return bool(self.llm_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
