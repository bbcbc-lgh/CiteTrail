from src.Agent.BaseAgent import BaseAgent
from src.Agent.PromptTemplates import REPORT_WRITER_PROMPT
from src.Infrastructure.LLMClient import LLMClient
from src.Schema.TaskSummary import TaskSummary


class ReporterAgent(BaseAgent):
    def __init__(self, llm: LLMClient):
        super().__init__("报告生成智能体", llm, "你负责撰写简洁、证据可追溯的 Markdown 报告。")

    async def generate(self, topic: str, summaries: list[TaskSummary]) -> str | None:
        if not self.is_configured:
            return None
        prompt = REPORT_WRITER_PROMPT.format(topic=topic, summaries=self._format_summaries(summaries))
        try:
            response = await self.run(prompt, temperature=0.3)
            return response.strip() or None
        except Exception:
            return None

    @staticmethod
    def _format_summaries(summaries: list[TaskSummary]) -> str:
        parts: list[str] = []
        for summary in summaries:
            source_lines = "\n".join(
                f"- {source.title}: {source.url}\n  {source.snippet}" for source in summary.sources
            ) or "- 没有可用来源"
            parts.append(
                f"### {summary.task.title}\n任务目标：{summary.task.intent}\n"
                f"总结：{summary.summary}\n关键要点：\n"
                + "\n".join(f"- {point}" for point in summary.key_points)
                + f"\n来源：\n{source_lines}"
            )
        return "\n\n".join(parts)
