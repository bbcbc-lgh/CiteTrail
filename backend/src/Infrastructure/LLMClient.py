from typing import Any

from src.Infrastructure.Config import Settings


class LLMClient:
    """Async client for OpenAI-compatible language model endpoints."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client: Any | None = None
        self.last_error: str | None = None

    @property
    def is_configured(self) -> bool:
        # A transient failure must not make a configured client unusable for a later retry.
        return bool(self.settings.llm_api_key)

    async def invoke(self, system_prompt: str, user_prompt: str, *, temperature: float = 0.2) -> str:
        if not self.is_configured:
            raise RuntimeError(self.last_error or "尚未配置大语言模型")
        self.last_error = None
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as exc:
                raise RuntimeError("已配置大语言模型，但尚未安装 openai 依赖包") from exc
            self._client = AsyncOpenAI(
                api_key=self.settings.llm_api_key,
                base_url=self.settings.llm_base_url or None,
                timeout=min(self.settings.llm_timeout, 20.0),
                max_retries=1,
            )
        try:
            response = await self._client.chat.completions.create(
                model=self.settings.llm_model_id,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=temperature,
            )
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            if status in {401, 403}:
                self.last_error = "模型鉴权失败，请检查服务配置"
            elif status in {400, 404, 422}:
                self.last_error = "模型或请求配置不可用，请检查模型配置"
            elif status == 429 or status is not None and status >= 500 or isinstance(exc, (TimeoutError, ConnectionError)) or "timeout" in type(exc).__name__.lower() or "connection" in type(exc).__name__.lower():
                self.last_error = "模型服务暂时不可用，有限重试后仍失败"
            else:
                self.last_error = "模型响应失败，请稍后重试"
            raise
        return response.choices[0].message.content or ""
