"""Async client for OpenAI-compatible language model endpoints."""

from __future__ import annotations

import logging
from typing import Any

from src.Infrastructure.Config import Settings
from src.Infrastructure.LLM.Capabilities import ModelCapabilities, detect_capabilities
from src.Infrastructure.Observability import SpanRecord, span
from src.Infrastructure.Tools import parse_json_object

logger = logging.getLogger(__name__)

# Provider error text that means "you asked for a feature I do not implement",
# as opposed to a transient failure or a bad credential.
_UNSUPPORTED_FEATURE_STATUSES = {400, 404, 422}
_UNSUPPORTED_FEATURE_TOKENS = (
    "response_format",
    "json_schema",
    "unsupported_parameter",
    "unknown_parameter",
    "unrecognized request argument",
    "extra fields not permitted",
)


class LLMClient:
    """Async client for OpenAI-compatible language model endpoints.

    The client owns a :class:`ModelCapabilities` snapshot. When a provider
    rejects an optional feature (structured output being the common one), the
    capability is switched off and the request is retried once without it. That
    keeps every caller free of vendor- and model-specific branching.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client: Any | None = None
        self._capabilities = detect_capabilities(settings)
        self.last_error: str | None = None

    @property
    def capabilities(self) -> ModelCapabilities:
        return self._capabilities

    @property
    def is_configured(self) -> bool:
        # A transient failure must not make a configured client unusable for a later retry.
        return bool(self.settings.llm_api_key)

    def _sdk_client(self) -> Any:
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as exc:
                raise RuntimeError("已配置大语言模型，但尚未安装 openai 依赖包") from exc
            self._client = AsyncOpenAI(
                api_key=self.settings.llm_api_key,
                base_url=self.settings.llm_base_url or None,
                timeout=self.settings.llm_timeout,
                max_retries=1,
            )
        return self._client

    async def invoke(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float = 0.2,
        json_schema: dict[str, Any] | None = None,
        span_attributes: dict[str, Any] | None = None,
    ) -> str:
        """Send one chat completion and return the raw text response.

        ``json_schema`` is a ``{"name": str, "schema": dict}`` pair. When the
        model supports structured output the schema is enforced by the provider;
        otherwise the prompt is still expected to describe the JSON shape and
        callers fall back to :func:`parse_json_object`.
        """
        if not self.is_configured:
            raise RuntimeError(self.last_error or "尚未配置大语言模型")
        self.last_error = None

        use_schema = bool(json_schema) and self._capabilities.supports_json_schema
        attempts = 2 if use_schema else 1
        with span(
            "llm_call",
            **{
                "llm.model": self.settings.llm_model_id,
                "llm.structured_output": use_schema,
                **(span_attributes or {}),
            },
        ) as llm_span:
            for attempt in range(attempts):
                payload = self._build_payload(system_prompt, user_prompt, temperature, json_schema)
                try:
                    response = await self._sdk_client().chat.completions.create(**payload)
                    self._record_usage(llm_span, response)
                    return response.choices[0].message.content or ""
                except Exception as exc:
                    if attempt + 1 < attempts and self._is_unsupported_feature(exc):
                        self._capabilities = self._capabilities.without("supports_json_schema")
                        llm_span.set("llm.downgraded_structured_output", True)
                        logger.warning(
                            "模型 %s 拒绝了结构化输出，已降级为文本 JSON 解析后重试一次", self._capabilities.model_id
                        )
                        continue
                    self._record_error(exc)
                    raise
            raise RuntimeError("模型调用未完成")

    @staticmethod
    def _record_usage(llm_span: SpanRecord, response: Any) -> None:
        """Copy token accounting onto the span so cost is attributable per call."""
        usage = response.get("usage") if isinstance(response, dict) else getattr(response, "usage", None)
        if usage is None:
            return

        def read(name: str) -> Any:
            if isinstance(usage, dict):
                return usage.get(name)
            return getattr(usage, name, None)

        prompt_tokens, completion_tokens = read("prompt_tokens"), read("completion_tokens")
        if prompt_tokens is not None:
            llm_span.set("llm.prompt_tokens", int(prompt_tokens))
        if completion_tokens is not None:
            llm_span.set("llm.completion_tokens", int(completion_tokens))
        details = read("prompt_tokens_details")
        cached = details.get("cached_tokens") if isinstance(details, dict) else getattr(details, "cached_tokens", None)
        if cached:
            llm_span.set("llm.cached_tokens", int(cached))

    async def invoke_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        schema: dict[str, Any] | None = None,
        temperature: float = 0.2,
        span_attributes: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Invoke the model and return the parsed JSON object, or ``None``.

        Failures are recorded on :attr:`last_error`; callers decide what to do
        about a missing payload rather than handling exceptions at every site.
        """
        try:
            text = await self.invoke(
                system_prompt,
                user_prompt,
                temperature=temperature,
                json_schema=schema,
                span_attributes=span_attributes,
            )
        except Exception:
            return None
        return parse_json_object(text)

    def _build_payload(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        json_schema: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.settings.llm_model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if self._capabilities.supports_temperature:
            payload["temperature"] = temperature
        if json_schema and self._capabilities.supports_json_schema:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": json_schema.get("name", "result"),
                    "strict": True,
                    "schema": json_schema["schema"],
                },
            }
        return payload

    @staticmethod
    def _is_unsupported_feature(exc: Exception) -> bool:
        status = getattr(exc, "status_code", None)
        if status not in _UNSUPPORTED_FEATURE_STATUSES:
            return False
        text = str(exc).lower()
        return any(token in text for token in _UNSUPPORTED_FEATURE_TOKENS)

    def _record_error(self, exc: Exception) -> None:
        status = getattr(exc, "status_code", None)
        if status in {401, 403}:
            self.last_error = "模型鉴权失败，请检查服务配置"
        elif status in {400, 404, 422}:
            self.last_error = "模型或请求配置不可用，请检查模型配置"
        elif status == 429 or (status is not None and status >= 500) or isinstance(exc, (TimeoutError, ConnectionError)) or "timeout" in type(exc).__name__.lower() or "connection" in type(exc).__name__.lower():
            self.last_error = "模型服务暂时不可用，有限重试后仍失败"
        else:
            self.last_error = "模型响应失败，请稍后重试"
        logger.warning("模型调用失败：%s（status=%s）", self.last_error, status)
