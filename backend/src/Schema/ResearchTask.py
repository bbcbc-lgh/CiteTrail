from uuid import uuid4

from pydantic import BaseModel, Field


class ResearchTask(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex[:8])
    title: str
    intent: str
    query: str
