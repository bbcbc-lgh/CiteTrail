import hashlib
from datetime import datetime
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, model_validator


def source_id(url: str) -> str:
    """Stable identity for a source so the same URL is always the same source."""
    normalized = (url or "").strip().rstrip("/").lower()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


class Source(BaseModel):
    """A citable origin.

    ``snippet`` is the search provider's raw excerpt. It is a *search-time*
    field used only by the degraded path; real evidence lives in ``Document``
    and ``Chunk`` once the page body has been fetched.
    """

    id: str = ""
    title: str
    url: str
    domain: str = ""
    provider: str = "search"
    source_type: Literal["official", "article", "community"] = "article"
    # Which search providers returned this page. More than one means independent
    # indexes agreed on it, which is a real (if weak) authority signal.
    found_by: list[str] = Field(default_factory=list)
    snippet: str = ""
    code: str = ""
    # Some search APIs (DeepSeek with need_content, Jina) return the page body
    # inline. When present it is preferred over fetching the URL ourselves: it
    # survives JS-rendered pages that a plain HTTP GET cannot read.
    content: str = ""
    fetched_at: datetime | None = None

    @model_validator(mode="after")
    def _derive_identity(self) -> "Source":
        if not self.id:
            self.id = source_id(self.url)
        if not self.domain:
            self.domain = (urlparse(self.url).hostname or "").lower()
        return self
