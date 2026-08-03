from datetime import date

from src.Agent.BaseAgent import BaseAgent
from src.Agent.Prompts import TODO_PLANNER_PROMPT
from src.Infrastructure.Tools import parse_json_object
from src.Infrastructure.LLMClient import LLMClient
from src.Schema.Task import Task


class PlannerAgent(BaseAgent):
    def __init__(self, llm: LLMClient):
        super().__init__("研究规划智能体", llm, "你负责制定清晰且互不重叠的研究计划。")

    async def plan(self, topic: str, task_count: int) -> list[Task]:
        if not self.is_configured:
            return []
        prompt = TODO_PLANNER_PROMPT.format(
            current_date=date.today().isoformat(), topic=topic, task_count=task_count
        )
        try:
            response = await self.run(prompt)
            payload = parse_json_object(response) or {}
            return self._validate_tasks(payload.get("tasks"), task_count)
        except Exception:
            return []

    @staticmethod
    def _validate_tasks(raw_tasks: object, task_count: int) -> list[Task]:
        if not isinstance(raw_tasks, list):
            return []
        tasks: list[Task] = []
        for item in raw_tasks[:task_count]:
            if not isinstance(item, dict):
                continue
            try:
                tasks.append(
                    Task(
                        title=str(item["title"]).strip(),
                        intent=str(item["intent"]).strip(),
                        query=str(item["query"]).strip(),
                    )
                )
            except (KeyError, ValueError):
                continue
        return tasks
