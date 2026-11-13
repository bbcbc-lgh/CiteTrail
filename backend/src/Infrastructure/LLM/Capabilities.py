"""What the configured model can actually do.

Capabilities are *declared* (from configuration and model-id heuristics) and
then *corrected at runtime* by the client whenever a provider rejects a feature.
Nothing downstream should ever branch on the model id itself.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from src.Infrastructure.Config import Settings

# Substrings that reliably identify reasoning-family models across vendors.
# These only change defaults; they never change the request shape by themselves.
_REASONING_MARKERS = ("o1", "o3", "o4", "gpt-5", "reasoner", "reasoning", "thinking", "qwq", "deepseek-r")


@dataclass(frozen=True)
class ModelCapabilities:
    model_id: str = ""
    supports_json_schema: bool = True
    supports_tool_calling: bool = True
    supports_streaming: bool = True
    supports_temperature: bool = True
    is_reasoning: bool = False
    context_window: int = 32_000
    max_output_tokens: int = 4_096

    def as_dict(self) -> dict[str, object]:
        return {
            "model_id": self.model_id,
            "supports_json_schema": self.supports_json_schema,
            "supports_tool_calling": self.supports_tool_calling,
            "supports_streaming": self.supports_streaming,
            "supports_temperature": self.supports_temperature,
            "is_reasoning": self.is_reasoning,
            "context_window": self.context_window,
            "max_output_tokens": self.max_output_tokens,
        }

    def without(self, feature: str) -> ModelCapabilities:
        """Return a copy with one boolean capability switched off."""
        if feature not in self.as_dict():
            raise KeyError(f"未知的模型能力：{feature}")
        return replace(self, **{feature: False})


def _parse_override(value: str) -> bool | None:
    """Parse an ``auto``/``true``/``false`` style setting into a tri-state."""
    normalized = (value or "").strip().lower()
    if normalized in {"", "auto"}:
        return None
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def detect_capabilities(settings: Settings) -> ModelCapabilities:
    """Build the initial capability set for the configured model."""
    model_id = (settings.llm_model_id or "").strip()
    normalized = model_id.lower()

    is_reasoning = _parse_override(settings.llm_is_reasoning)
    if is_reasoning is None:
        is_reasoning = any(marker in normalized for marker in _REASONING_MARKERS)

    supports_temperature = _parse_override(settings.llm_supports_temperature)
    if supports_temperature is None:
        # Reasoning models usually ignore or reject temperature, so default to
        # omitting it rather than sending a parameter that will not apply.
        supports_temperature = not is_reasoning

    supports_json_schema = _parse_override(settings.llm_supports_json_schema)
    if supports_json_schema is None:
        supports_json_schema = True

    return ModelCapabilities(
        model_id=model_id,
        supports_json_schema=supports_json_schema,
        supports_tool_calling=True,
        supports_streaming=True,
        supports_temperature=supports_temperature,
        is_reasoning=is_reasoning,
        context_window=settings.llm_context_window,
        max_output_tokens=settings.llm_max_output_tokens,
    )
