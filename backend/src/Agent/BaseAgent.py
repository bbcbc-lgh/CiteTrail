from typing import Any

from src.Infrastructure.LLMClient import LLMClient


class BaseAgent:
    """Common prompt and model boundary for all research agents."""

    def __init__(self, name: str, llm: LLMClient, system_prompt: str):
        self.name = name
        self.llm = llm
        self.system_prompt = system_prompt

    @property
    def is_configured(self) -> bool:
        return self.llm.is_configured

    async def run(self, input_text: str, **kwargs: Any) -> str:
        return await self.llm.invoke(self.system_prompt, input_text, **kwargs)
