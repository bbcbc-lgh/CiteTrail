import re
from typing import Any

from src.Agent.BaseAgent import BaseAgent
from src.Agent.Prompts import TASK_SUMMARIZER_PROMPT
from src.Agent.Schemas import SUMMARY_SCHEMA
from src.Infrastructure.LLM import LLMClient
from src.Infrastructure.Tools import format_sources_for_prompt
from src.Schema.Claim import Claim
from src.Schema.Source import Source
from src.Schema.Summary import Summary
from src.Schema.Task import Task


class SummarizerAgent(BaseAgent):
    def __init__(self, llm: LLMClient):
        super().__init__("任务总结智能体", llm, "你只能总结提供的证据，不能编造事实。")

    async def summarize(
        self,
        topic: str,
        task: Task,
        sources: list[Source],
        chunks_by_source: dict[str, list[Any]] | None = None,
    ) -> Summary | None:
        usable_sources = [source for source in sources if source.url]
        if not self.is_configured or not usable_sources:
            return None
        prompt = TASK_SUMMARIZER_PROMPT.format(
            topic=topic,
            task_title=task.title,
            task_intent=task.intent,
            sources=format_sources_for_prompt(self.build_entries(usable_sources, chunks_by_source)),
        )
        payload = await self.run_json(prompt, schema=SUMMARY_SCHEMA)
        if not payload:
            return None
        summary_text = str(payload.get("summary", "")).strip()
        if not summary_text:
            return None
        return Summary(
            task=task,
            summary=summary_text,
            claims=self.build_claims(payload, usable_sources),
            sources=usable_sources,
        )

    @staticmethod
    def build_entries(sources: list[Source], chunks_by_source: dict[str, list[Any]] | None = None) -> list[dict]:
        """Attach fetched excerpts to each source for the prompt.

        A source whose page could not be fetched keeps its search snippet, so a
        single failed download never removes a citation from the run.
        """
        entries: list[dict] = []
        for source in sources:
            entry = source.model_dump()
            chosen = (chunks_by_source or {}).get(source.id) or []
            if chosen:
                entry["excerpt"] = "\n\n".join(chunk.text for chunk in chosen)
            entries.append(entry)
        return entries

    @staticmethod
    def build_claims(payload: dict, sources: list[Source]) -> list[Claim]:
        """Turn model output into claims with resolved source identities.

        Accepts both the current ``claims`` shape and the legacy ``key_points``
        list of strings, so an older prompt or a provider that drops structured
        output still yields traceable citations.
        """
        numbering = {index: source.id for index, source in enumerate(sources, start=1)}
        claims: list[Claim] = []

        raw_claims = payload.get("claims")
        if isinstance(raw_claims, list):
            for item in raw_claims:
                if isinstance(item, dict):
                    text = str(item.get("text", "")).strip()
                    declared = item.get("cites") or []
                else:
                    text = str(item).strip()
                    declared = []
                if not text:
                    continue
                claims.append(Claim(text=text, cited_source_ids=_resolve_citations(text, declared, numbering)))
            return claims

        for item in payload.get("key_points", []) or []:
            text = str(item).strip()
            if text:
                claims.append(Claim(text=text, cited_source_ids=_resolve_citations(text, [], numbering)))
        return claims


def _resolve_citations(text: str, declared: object, numbering: dict[int, str]) -> list[str]:
    """Merge ``[n]`` markers found in the text with the model's declared numbers."""
    numbers = {int(match) for match in re.findall(r"\[(\d+)\]", text)}
    if isinstance(declared, list):
        for value in declared:
            try:
                numbers.add(int(value))
            except (TypeError, ValueError):
                continue
    return [numbering[number] for number in sorted(numbers) if number in numbering]
