from typing import Any

from src.Infrastructure.LLM import LLMClient


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
        return await self.llm.invoke(self.system_prompt, input_text, span_attributes={"agent": self.name}, **kwargs)

    async def run_json(self, input_text: str, *, schema: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any] | None:
        """Run the agent and return a parsed JSON object when the model produced one."""
        return await self.llm.invoke_json(
            self.system_prompt,
            input_text,
            schema=schema,
            span_attributes={"agent": self.name},
            **kwargs,
        )
