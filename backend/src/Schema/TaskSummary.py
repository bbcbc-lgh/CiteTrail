from pydantic import BaseModel, Field

from src.Schema.ResearchTask import ResearchTask
from src.Schema.Source import Source


class TaskSummary(BaseModel):
    task: ResearchTask
    summary: str
    key_points: list[str] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
