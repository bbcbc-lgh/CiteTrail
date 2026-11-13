import asyncio
import logging
import tempfile
import unittest
from pathlib import Path

from src.Infrastructure.Config import Settings
from src.Schema.Source import Source
from src.Schema.Summary import Summary
from src.Schema.Task import Task
from src.WorkFlow.ResearchWorkFlow import ResearchWorkFlow


def setUpModule() -> None:
    logging.disable(logging.CRITICAL)


def tearDownModule() -> None:
    logging.disable(logging.NOTSET)


class ConcurrencyTest(unittest.TestCase):
    def _run(self, concurrency: int) -> tuple[int, list[str]]:
        active = 0
        peak = 0

        with tempfile.TemporaryDirectory() as directory:
            workflow = ResearchWorkFlow(
                Settings(
                    _env_file=None,
                    workspace_dir=Path(directory),
                    llm_api_key="test",
                    max_tasks=3,
                    task_concurrency=concurrency,
                    max_web_research_loops=0,
                    fetch_pages=False,
                    verify_claims=False,
                )
            )

            async def search(query, topic=None):
                return [Source(title=query, url=f"https://example.com/{query}", snippet="证据")]

            async def plan(topic, count):
                return [Task(title=f"任务{i}", intent="意图", query=f"q{i}") for i in range(3)]

            async def summarize(topic, task, sources, chunks=None):
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                try:
                    await asyncio.sleep(0.02)
                    return Summary(task=task, summary="摘要", claims=[], sources=sources)
                finally:
                    active -= 1

            async def report(topic, summaries):
                return "# 报告"

            workflow.search_tool.run = search
            workflow.planner.plan = plan
            workflow.summarizer.summarize = summarize
            workflow.reporter.generate = report

            events = asyncio.run(self._drain(workflow))

        return peak, [event.type for event in events]

    @staticmethod
    async def _drain(workflow):
        return [event async for event in workflow.research("主题", None, "conc-1")]

    def test_tasks_overlap_up_to_the_configured_limit(self):
        peak, event_types = self._run(concurrency=3)

        self.assertEqual(peak, 3)
        self.assertEqual(event_types.count("task_summary"), 3)
        self.assertEqual(event_types[-1], "done")

    def test_concurrency_one_restores_sequential_behaviour(self):
        peak, event_types = self._run(concurrency=1)

        self.assertEqual(peak, 1)
        self.assertEqual(event_types.count("task_summary"), 3)


if __name__ == "__main__":
    unittest.main()
