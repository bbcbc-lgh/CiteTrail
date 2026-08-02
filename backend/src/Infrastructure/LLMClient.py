from typing import Any

from src.Infrastructure.Config import Settings


class LLMClient:
    """Async client for OpenAI-compatible language model endpoints."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._client: Any | None = None

    @property
    def is_configured(self) -> bool:
        return bool(self.settings.llm_api_key)

    async def invoke(self, system_prompt: str, user_prompt: str, *, temperature: float = 0.2) -> str:
        if not self.is_configured:
            raise RuntimeError("尚未配置大语言模型")
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as exc:
                raise RuntimeError("已配置大语言模型，但尚未安装 openai 依赖包") from exc
            self._client = AsyncOpenAI(
                api_key=self.settings.llm_api_key,
                base_url=self.settings.llm_base_url or None,
                timeout=self.settings.llm_timeout,
            )
        response = await self._client.chat.completions.create(
            model=self.settings.llm_model_id,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=temperature,
        )
        return response.choices[0].message.content or ""
