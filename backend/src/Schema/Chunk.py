from pydantic import BaseModel, Field


class Chunk(BaseModel):
    """A retrievable slice of a :class:`~src.Schema.Document.Document`."""

    id: str
    document_id: str
    ordinal: int = 0
    text: str
    score: float | None = None
    meta: dict[str, str] = Field(default_factory=dict)
