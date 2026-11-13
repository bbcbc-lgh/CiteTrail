from src.Agent.BaseAgent import BaseAgent
from src.Agent.Prompts import REPORT_WRITER_PROMPT
from src.Infrastructure.Citations import build_citation_index, renumber_markers
from src.Infrastructure.LLM import LLMClient
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
        citations = build_citation_index(summaries)
        number_of = {citation.source_id: citation.index for citation in citations}
        parts: list[str] = []
        for summary in summaries:
            source_lines: list[str] = []
            for source in summary.sources:
                number = number_of.get(source.id)
                if number is None:
                    continue
                source_lines.append(
                    f"- [{number}] {source.title}: {source.url}\n"
                    f"  来源类型：{source.source_type}\n  {source.snippet}\n{source.code}"
                )
            claim_lines: list[str] = []
            # Claims were written against this task's own numbering; rewrite them
            # so every marker in the prompt means the same source everywhere.
            local_to_global = {
                index: number_of[source.id]
                for index, source in enumerate(summary.sources, start=1)
                if source.id in number_of
            }
            for claim in summary.claims:
                text = renumber_markers(claim.text, local_to_global)
                markers = [f"[{number_of[sid]}]" for sid in claim.cited_source_ids if sid in number_of]
                suffix = f"（引用 {' '.join(markers)}）" if markers else ""
                if claim.support_score is not None and claim.support_score < 0.5:
                    suffix += "（证据支持较弱，表述需谨慎）"
                claim_lines.append(f"- {text}{suffix}")
            parts.append(
                f"### {summary.task.title}\n任务目标：{summary.task.intent}\n"
                f"总结：{summary.summary}\n关键要点：\n"
                + "\n".join(claim_lines or ["- 没有可用要点"])
                + "\n来源：\n" + "\n".join(source_lines or ["- 没有可用来源"])
            )
        return "\n\n".join(parts)
