from src.Schema.BaseResponse import BaseResponse


class HealthResponse(BaseResponse):
    status: str
    llm_configured: bool
    search_backend: str
