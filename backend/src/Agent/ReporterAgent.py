from src.Agent.BaseAgent import BaseAgent
from src.Agent.Prompts import REPORT_WRITER_PROMPT
from src.Infrastructure.LLMClient import LLMClient
from src.Schema.Summary import Summary


class ReporterAgent(BaseAgent):
    def __init__(self, llm: LLMClient):
        super().__init__("报告生成智能体", llm, "你负责撰写简洁、证据可追溯的 Markdown 报告。")

    async def generate(self, topic: str, summaries: list[Summary]) -> str | None:
        if not self.is_configured:
            return None
        prompt = REPORT_WRITER_PROMPT.format(topic=topic, summaries=self._format_summaries(summaries))
        try:
            response = await self.run(prompt, temperature=0.3)
            return response.strip() or None
        except Exception:
            return None

    @staticmethod
    def _format_summaries(summaries: list[Summary]) -> str:
        source_index: dict[str, int] = {}
        next_number = 1
        parts: list[str] = []
        for summary in summaries:
            source_lines: list[str] = []
            for source in summary.sources:
                if source.url not in source_index:
                    source_index[source.url] = next_number
                    next_number += 1
                source_lines.append(f"- [{source_index[source.url]}] {source.title}: {source.url}\n  来源类型：{source.source_type}\n  {source.snippet}\n{source.code}")
            parts.append(
                f"### {summary.task.title}\n任务目标：{summary.task.intent}\n"
                f"总结：{summary.summary}\n关键要点：\n"
                + "\n".join(f"- {point}" for point in summary.key_points)
                + "\n来源：\n" + "\n".join(source_lines or ["- 没有可用来源"])
            )
        return "\n\n".join(parts)
