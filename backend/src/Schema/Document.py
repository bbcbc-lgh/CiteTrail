from pydantic import BaseModel, Field


class Document(BaseModel):
    """Full text retrieved from a :class:`~src.Schema.Source.Source`.

    Empty until page fetching exists; the model is declared now so that adding
    retrieval later does not require a schema migration.
    """

    id: str
    source_id: str
    text: str = ""
    lang: str | None = None
    word_count: int = 0
    meta: dict[str, str] = Field(default_factory=dict)
