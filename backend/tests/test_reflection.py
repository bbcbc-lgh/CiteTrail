import asyncio
import logging
import tempfile
import unittest
from pathlib import Path

from src.Agent.CriticAgent import CriticAgent
from src.Infrastructure.Config import Settings
from src.Infrastructure.Reflection import assess
from src.Schema.Claim import Claim
from src.Schema.Source import Source
from src.Schema.Summary import Summary
from src.Schema.Task import Task
from src.WorkFlow.ResearchWorkFlow import ResearchWorkFlow


def setUpModule() -> None:
    logging.disable(logging.CRITICAL)


def tearDownModule() -> None:
    logging.disable(logging.NOTSET)


def build_summary(claims: list[Claim], sources: list[Source]) -> Summary:
    return Summary(task=Task(title="T", intent="I", query="Q"), summary="", claims=claims, sources=sources)


class AssessTest(unittest.TestCase):
    def test_no_usable_source_needs_another_round(self):
        decision = assess(build_summary([], []), [])

        self.assertTrue(decision.needed)
        self.assertIn("没有可用来源", decision.reason)

    def test_no_claims_needs_another_round(self):
        source = Source(title="a", url="https://example.com/a")

        decision = assess(build_summary([], [source]), [source])

        self.assertTrue(decision.needed)

    def test_too_few_claims_needs_another_round(self):
        source = Source(title="a", url="https://example.com/a")
        claims = [Claim(text=f"结论{i}") for i in range(2)]

        self.assertTrue(assess(build_summary(claims, [source]), [source]).needed)

    def test_majority_unsupported_needs_another_round(self):
        sources = [Source(title="a", url="https://example.com/a"), Source(title="b", url="https://example.com/b")]
        claims = [Claim(text=f"结论{i}", support_score=0.1) for i in range(4)]

        decision = assess(build_summary(claims, sources), sources)

        self.assertTrue(decision.needed)
        self.assertIn("缺乏支撑", decision.reason)

    def test_supported_evidence_stops_the_loop(self):
        sources = [Source(title="a", url="https://example.com/a"), Source(title="b", url="https://example.com/b")]
        claims = [Claim(text=f"结论{i}", support_score=0.9) for i in range(4)]

        decision = assess(build_summary(claims, sources), sources)

        self.assertFalse(decision.needed)

    def test_a_single_source_is_not_enough_even_with_claims(self):
        source = Source(title="a", url="https://example.com/a")
        claims = [Claim(text=f"结论{i}", support_score=0.9) for i in range(5)]

        decision = assess(build_summary(claims, [source]), [source])

        self.assertTrue(decision.needed)
        self.assertIn("可用来源过少", decision.reason)

    def test_two_sources_with_supported_claims_stop_the_loop(self):
        sources = [Source(title="a", url="https://example.com/a"), Source(title="b", url="https://example.com/b")]
        claims = [Claim(text=f"结论{i}", support_score=0.9) for i in range(4)]

        self.assertFalse(assess(build_summary(claims, sources), sources).needed)

    def test_unverified_claims_do_not_trigger_the_support_check(self):
        sources = [Source(title="a", url="https://example.com/a"), Source(title="b", url="https://example.com/b")]
        claims = [Claim(text=f"结论{i}") for i in range(4)]

        self.assertFalse(assess(build_summary(claims, sources), sources).needed)


class QueryCleaningTest(unittest.TestCase):
    def test_duplicates_of_used_queries_are_dropped(self):
        payload = {"queries": ["SSE 重连", "SSE 重连", "sse 重连 " ]}

        result = CriticAgent._clean(payload, ["SSE 重连"], limit=3)

        self.assertEqual(result, [])

    def test_blank_and_overlong_queries_are_dropped(self):
        payload = {"queries": ["", "   ", "x" * 300, "有效查询"]}

        self.assertEqual(CriticAgent._clean(payload, [], limit=3), ["有效查询"])

    def test_overlong_queries_are_dropped(self):
        payload = {"queries": [" ".join(["word"] * 20), "SSE 重连"]}

        self.assertEqual(CriticAgent._clean(payload, [], limit=3), ["SSE 重连"])

    def test_limit_is_respected(self):
        payload = {"queries": ["a", "b", "c", "d"]}

        self.assertEqual(CriticAgent._clean(payload, [], limit=2), ["a", "b"])

    def test_malformed_payload_returns_nothing(self):
        self.assertEqual(CriticAgent._clean(None, [], 2), [])
        self.assertEqual(CriticAgent._clean({"queries": "nope"}, [], 2), [])


class ReflectionLoopTest(unittest.TestCase):
    def _build(self, directory: str, loops: int) -> tuple[ResearchWorkFlow, list[str]]:
        workflow = ResearchWorkFlow(
            Settings(
                _env_file=None,
                workspace_dir=Path(directory),
                llm_api_key="test",
                max_tasks=3,
                max_web_research_loops=loops,
                fetch_pages=False,
                verify_claims=False,
            )
        )
        seen: list[str] = []

        async def search(query, topic=None):
            seen.append(query)
            return [Source(title="t", url=f"https://example.com/{len(seen)}", snippet="证据")]

        async def plan(topic, count):
            return [Task(title=f"T{i}", intent="I", query=f"q{i}") for i in range(3)]

        async def summarize(topic, task, sources, chunks=None):
            return Summary(task=task, summary="s", claims=[], sources=sources)

        async def propose(topic, task, summary, used, limit):
            return ["followup"]

        async def report(topic, summaries):
            return "# R"

        workflow.search_tool.run = search
        workflow.planner.plan = plan
        workflow.summarizer.summarize = summarize
        workflow.critic.propose_queries = propose
        workflow.reporter.generate = report
        return workflow, seen

    def test_thin_evidence_triggers_exactly_one_followup_per_task(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow, seen = self._build(directory, loops=1)
            events = asyncio.run(self._drain(workflow, "rid-1"))

        self.assertEqual(len([query for query in seen if query == "followup"]), 3)
        self.assertTrue(any(event.type == "task_summary" for event in events))
        self.assertTrue(any(event.data.get("reflection") for event in events))

    def test_reflection_is_skipped_when_the_budget_is_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow, seen = self._build(directory, loops=0)
            asyncio.run(self._drain(workflow, "rid-2"))

        self.assertNotIn("followup", seen)

    @staticmethod
    async def _drain(workflow, research_id: str):
        return [event async for event in workflow.research("主题", None, research_id)]


if __name__ == "__main__":
    unittest.main()
