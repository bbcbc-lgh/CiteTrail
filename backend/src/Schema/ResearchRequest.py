from pydantic import Field, field_validator

from src.Schema.BaseRequest import BaseRequest


class ResearchRequest(BaseRequest):
    topic: str = Field(min_length=3, max_length=500, description="需要研究的主题。")

    @field_validator("topic")
    @classmethod
    def normalize_topic(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("研究主题不能为空")
        return normalized
