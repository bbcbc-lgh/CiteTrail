from pydantic import BaseModel, Field

from src.Schema.Task import Task
from src.Schema.Source import Source


class Summary(BaseModel):
    task: Task
    summary: str
    key_points: list[str] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
