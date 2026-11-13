from uuid import uuid4

from pydantic import BaseModel, Field


class Claim(BaseModel):
    """One verifiable statement and the sources that are supposed to back it.

    Claims exist so citation checking can be mechanical instead of trusting a
    prompt. ``support_score`` stays ``None`` until a verifier fills it in;
    ``None`` means "not checked", which is different from "score zero".
    """

    id: str = Field(default_factory=lambda: uuid4().hex[:8])
    text: str
    cited_source_ids: list[str] = Field(default_factory=list)
    cited_chunk_ids: list[str] = Field(default_factory=list)
    support_score: float | None = None
    support_reason: str | None = None
