from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_name: str = "CiteTrail"
    api_prefix: str = "/api"
    host: str = "127.0.0.1"
    port: int = 8000
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    llm_api_key: str = ""
    llm_base_url: str = ""
    # Both spellings are in the wild in this project's own fixture files; accept
    # either rather than silently falling back to the default model.
    llm_model_id: str = Field(
        default="gpt-4o-mini",
        validation_alias=AliasChoices("LLM_MODEL_ID", "LLM_MODEL"),
    )
    llm_timeout: float = 60.0

    # Model capability overrides. "auto" infers from the model id and lets the
    # client correct itself at runtime; "true"/"false" pin the value and are
    # only needed when a provider lies about what it supports.
    llm_supports_json_schema: str = "auto"
    llm_supports_temperature: str = "auto"
    llm_is_reasoning: str = "auto"
    llm_context_window: int = Field(default=32_000, ge=1_000)
    llm_max_output_tokens: int = Field(default=4_096, ge=1)

    # Search. ``fusion`` queries every configured domestic provider in parallel
    # and merges the results; a page returned by two independent indexes is
    # stronger evidence than one returned by a single index. ``fallback`` keeps
    # the old behaviour of trying providers in order until one answers.
    search_mode: str = "fusion"
    search_fusion_backends: str = "bocha,zhipu,baidu,deepseek,metaso"
    # Used only when fusion yields nothing (or is disabled).
    search_api: str = "tavily"
    bocha_api_key: str = ""
    zhipu_api_key: str = ""
    baidu_api_key: str = ""
    baidu_search_endpoint: str = "https://qianfan.baidubce.com/v2/ai_search/web_search"
    # DeepSeek's search API returns rerank_score / authority / a 600–800 char
    # summary, which is richer than any other provider here.
    deepseek_api_key: str = ""
    deepseek_search_endpoint: str = "https://api.deepseek.com/v1/tools/search"
    # Asking DeepSeek for the page body removes a whole fetch from the pipeline
    # and reads JS-rendered pages that our own GET cannot.
    deepseek_need_content: bool = True
    metaso_api_key: str = ""
    metaso_search_endpoint: str = "https://metaso.cn/api/v1/search"
    tavily_api_key: str = ""
    search_result_limit: int = Field(default=5, ge=1, le=10)
    search_timeout: float = 15.0

    @property
    def fusion_backend_list(self) -> list[str]:
        return [item.strip().lower() for item in self.search_fusion_backends.split(",") if item.strip()]

    max_tasks: int = Field(default=4, ge=3, le=5)
    # Sub-tasks mostly wait on network and model latency, so running them
    # concurrently cuts wall time almost linearly. 1 restores sequential order.
    task_concurrency: int = Field(default=3, ge=1, le=5)
    # Extra retrieval rounds per task when the evidence is plainly too thin.
    # Each round costs a search, a fetch, a summary and a verification.
    max_web_research_loops: int = Field(default=1, ge=0, le=3)
    reflection_max_queries: int = Field(default=2, ge=1, le=4)
    # Full-page retrieval. Search snippets are a few hundred characters; fetching
    # the body is what makes the quoted evidence actually checkable.
    fetch_pages: bool = True
    fetch_timeout: float = 12.0
    fetch_max_sources: int = Field(default=4, ge=1, le=10)
    fetch_max_bytes: int = Field(default=2_000_000, ge=100_000)
    chunks_per_source: int = Field(default=2, ge=1, le=6)
    # Ask the model to score each claim against its cited sources. Costs one
    # extra call per task; disable to trade evidence checking for latency.
    verify_claims: bool = True
    verify_max_claims: int = Field(default=12, ge=1, le=40)
    workspace_dir: Path = PROJECT_DIR / "workspace"
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=PROJECT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
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

