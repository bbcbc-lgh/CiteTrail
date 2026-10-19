import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from src.Infrastructure.Config import Settings
from src.Infrastructure.LLMClient import LLMClient
from src.Schema.Source import Source
from src.Schema.Summary import Summary
from src.Schema.Task import Task
from src.WorkFlow.ResearchWorkFlow import ResearchWorkFlow


class ResearchWorkflowTest(unittest.IsolatedAsyncioTestCase):
    async def test_model_can_recover_on_same_client_after_transient_failure(self):
        client = LLMClient(Settings(_env_file=None, llm_api_key="test"))
        failure = RuntimeError("unavailable")
        failure.status_code = 503
        create = AsyncMock(side_effect=[failure, SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Recovered"))])])
        client._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        with self.assertRaises(RuntimeError):
            await client.invoke("system", "prompt")
        self.assertTrue(client.is_configured)
        self.assertIn("暂时不可用", client.last_error)
        self.assertEqual(await client.invoke("system", "prompt"), "Recovered")
        self.assertIsNone(client.last_error)

    async def test_cancel_stops_after_current_search_without_writing_report(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = ResearchWorkFlow(Settings(_env_file=None, workspace_dir=Path(directory), llm_api_key=""))
            entered = asyncio.Event()
            interrupted = asyncio.Event()

            async def search(_query, _topic=None):
                entered.set()
                try:
                    await asyncio.Future()
                finally:
                    interrupted.set()

            workflow.search_tool.run = search
            cancel_event = asyncio.Event()

            async def consume():
                return [event async for event in workflow.research("研究一个主题", cancel_event, "test-id")]

            task = asyncio.create_task(consume())
            await entered.wait()
            cancel_event.set()
            events = await task

            self.assertEqual(events[-1].type, "cancelled")
            self.assertTrue(interrupted.is_set())
            self.assertNotIn("done", [event.type for event in events])
            self.assertFalse(list((Path(directory) / "reports").glob("*.md")))

    def test_fallback_report_uses_one_global_citation_numbering(self):
        task1 = Task(title="第一项", intent="目标一", query="问题一")
        task2 = Task(title="第二项", intent="目标二", query="问题二")
        source = Source(title="Example", url="https://example.com", snippet="证据")
        report = ResearchWorkFlow._fallback_report(
            "研究主题",
            [
                Summary(task=task1, summary="摘要一", sources=[source]),
                Summary(task=task2, summary="摘要二", sources=[source]),
            ],
        )

        self.assertEqual(report.count("1. [Example](https://example.com)"), 1)
        self.assertNotIn("[2]", report)
        self.assertEqual(report.count("[1]"), 2)
        self.assertIn("当前使用本地回退逻辑", report)

    async def test_regeneration_reuses_evidence_and_keeps_old_report_on_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = ResearchWorkFlow(Settings(_env_file=None, workspace_dir=Path(directory), llm_api_key="test"))
            source = Source(title="Evidence", url="https://example.com", snippet="A supported statement.")
            summary = Summary(task=Task(title="Scope", intent="Explain", query="topic"), summary="Evidence", sources=[source])
            workflow.note_tool.store.save_evidence("saved-id", "topic", [summary])
            report_path = workflow.note_tool.save_report("saved-id", "Old evidence summary")

            async def generate(topic, summaries):
                self.assertEqual(topic, "topic")
                self.assertEqual(summaries[0].sources[0].url, source.url)
                return None

            workflow.reporter.generate = generate
            events = [event async for event in workflow.regenerate("saved-id")]
            self.assertEqual(events[-1].type, "error")
            self.assertEqual(report_path.read_text(encoding="utf-8"), "Old evidence summary")

            async def generate_success(topic, summaries):
                return "# Report\nConclusion supported by evidence [1]."

            workflow.reporter.generate = generate_success
            events = [event async for event in workflow.regenerate("saved-id")]
            self.assertEqual(events[-1].type, "done")
            self.assertFalse(events[-2].data["degraded"])
            self.assertIn("Conclusion supported", report_path.read_text(encoding="utf-8"))

    async def test_invalid_saved_id_does_not_read_outside_notes(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = ResearchWorkFlow(Settings(_env_file=None, workspace_dir=Path(directory)))
            events = [event async for event in workflow.regenerate("../../outside")]
            self.assertEqual(events[-1].type, "error")


if __name__ == "__main__":
    unittest.main()
