from typing import Any

from src.Agent.BaseAgent import BaseAgent
from src.Agent.Prompts import CRITIC_PROMPT
from src.Agent.Schemas import QUERY_SCHEMA
from src.Infrastructure.LLM import LLMClient
from src.Schema.Summary import Summary
from src.Schema.Task import Task

# Long, synonym-stuffed queries return nothing from search engines.
MAX_QUERY_WORDS = 12


class CriticAgent(BaseAgent):
    """Proposes follow-up searches when the gathered evidence is thin.

    It never decides *whether* to loop — that is a cheap deterministic call in
    :mod:`src.Infrastructure.Reflection`. This agent only answers "what should I
    look for next", which genuinely benefits from a model.
    """

    def __init__(self, llm: LLMClient):
        super().__init__("证据审查智能体", llm, "你负责判断证据缺口，并给出可以直接检索的新查询。")

    async def propose_queries(
        self,
        topic: str,
        task: Task,
        summary: Summary,
        used_queries: list[str],
        limit: int,
    ) -> list[str]:
        if not self.is_configured:
            return []
        claims = "\n".join(f"- {claim.text}" for claim in summary.claims[:10]) or "- （尚未形成结论）"
        payload = await self.run_json(
            CRITIC_PROMPT.format(
                topic=topic,
                task_title=task.title,
                task_intent=task.intent,
                claims=claims,
                used="\n".join(f"- {query}" for query in used_queries) or "- （无）",
                limit=limit,
            ),
            schema=QUERY_SCHEMA,
        )
        return self._clean(payload, used_queries, limit)

    @staticmethod
    def _clean(payload: dict[str, Any] | None, used_queries: list[str], limit: int) -> list[str]:
        """Drop blanks, repeats of the queries already run, and overlong output."""
        if not payload:
            return []
        raw = payload.get("queries")
        if not isinstance(raw, list):
            return []
        seen = {query.strip().lower() for query in used_queries}
        proposals: list[str] = []
        for item in raw:
            query = str(item or "").strip()
            if not query or len(query) > 200:
                continue
            # A search engine that gets 15 words of stacked synonyms returns
            # nothing; cap the length rather than trusting the prompt alone.
            if len(query.split()) > MAX_QUERY_WORDS:
                continue
            if query.lower() in seen:
                continue
            seen.add(query.lower())
            proposals.append(query)
            if len(proposals) >= limit:
                break
        return proposals
