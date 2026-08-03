from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


class Event(BaseModel):
    type: Literal["progress", "plan", "task_started", "search_results", "task_summary", "report", "error", "done"]
    research_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    stage: str | None = None
    percentage: int | None = Field(default=None, ge=0, le=100)
    text: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
