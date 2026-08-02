from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    llm_configured: bool
    search_backend: str
