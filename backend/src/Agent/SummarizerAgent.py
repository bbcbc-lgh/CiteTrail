from src.Agent.BaseAgent import BaseAgent
from src.Agent.Prompts import TASK_SUMMARIZER_PROMPT
from src.Infrastructure.Tools import format_sources_for_prompt, parse_json_object
from src.Infrastructure.LLMClient import LLMClient
from src.Schema.Task import Task
from src.Schema.Source import Source
from src.Schema.Summary import Summary


class SummarizerAgent(BaseAgent):
    def __init__(self, llm: LLMClient):
        super().__init__("任务总结智能体", llm, "你只能总结提供的证据，不能编造事实。")

    async def summarize(self, topic: str, task: Task, sources: list[Source]) -> Summary | None:
        usable_sources = [source for source in sources if source.url]
        if not self.is_configured or not usable_sources:
            return None
        prompt = TASK_SUMMARIZER_PROMPT.format(
            topic=topic,
            task_title=task.title,
            task_intent=task.intent,
            sources=format_sources_for_prompt([source.model_dump() for source in usable_sources]),
        )
        try:
            payload = parse_json_object(await self.run(prompt)) or {}
            summary = str(payload.get("summary", "")).strip()
            key_points = [str(point).strip() for point in payload.get("key_points", []) if str(point).strip()]
            if summary:
                return Summary(task=task, summary=summary, key_points=key_points[:5], sources=usable_sources)
        except Exception:
            pass
        return None
