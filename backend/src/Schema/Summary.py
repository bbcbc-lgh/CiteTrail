from pydantic import BaseModel, Field

from src.Schema.Claim import Claim
from src.Schema.Source import Source
from src.Schema.Task import Task


class Summary(BaseModel):
    task: Task
    summary: str
    claims: list[Claim] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
