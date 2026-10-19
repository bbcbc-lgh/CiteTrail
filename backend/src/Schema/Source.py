from pydantic import BaseModel
from typing import Literal


class Source(BaseModel):
    title: str
    url: str
    snippet: str = ""
    provider: str = "search"
    source_type: Literal["official", "article", "community"] = "article"
    code: str = ""
