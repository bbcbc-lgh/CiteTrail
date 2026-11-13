from pydantic import BaseModel, Field


class Citation(BaseModel):
    """A source's position in the report-wide citation numbering."""

    index: int = Field(ge=1)
    source_id: str
    title: str
    url: str
    claim_ids: list[str] = Field(default_factory=list)
