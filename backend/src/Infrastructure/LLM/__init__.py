"""Model-agnostic access layer for OpenAI-compatible endpoints.

Callers depend on :class:`ModelCapabilities`, never on a vendor or model id, so
switching providers is a configuration change rather than a code change.
"""

from src.Infrastructure.LLM.Capabilities import ModelCapabilities, detect_capabilities
from src.Infrastructure.LLM.Client import LLMClient

__all__ = ["LLMClient", "ModelCapabilities", "detect_capabilities"]
