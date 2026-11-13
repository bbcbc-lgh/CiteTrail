from typing import Any

from src.Schema.BaseResponse import BaseResponse


class HealthResponse(BaseResponse):
    status: str
    llm_configured: bool
    llm_model: str
    llm_capabilities: dict[str, Any]
    search_backend: str
    search_backends: list[dict[str, Any]]
