from pydantic import BaseModel


class BaseRequest(BaseModel):
    """Common Pydantic base for API request models."""
