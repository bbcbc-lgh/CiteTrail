import json
import logging
import tempfile
import unittest
from pathlib import Path

from src.Infrastructure.Observability import record, span, start_trace


def setUpModule() -> None:
    logging.disable(logging.CRITICAL)


def tearDownModule() -> None:
    logging.disable(logging.NOTSET)


class ObservabilityTest(unittest.TestCase):
    def test_spans_nest_and_attributes_reach_the_export(self):
        with tempfile.TemporaryDirectory() as directory:
            with start_trace("t1", "research", export_dir=Path(directory), **{"research.topic": "t"}):
                with span("task", **{"task.id": "a"}):
                    with span("llm_call", agent="任务总结智能体"):
                        record(**{"llm.prompt_tokens": 10, "llm.completion_tokens": 2})

            payload = json.loads((Path(directory) / "t1.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["trace_id"], "t1")
        self.assertEqual(payload["name"], "research")
        self.assertEqual(payload["attributes"]["research.topic"], "t")
        task = payload["children"][0]
        self.assertEqual(task["name"], "task")
        self.assertEqual(task["attributes"]["task.id"], "a")
        llm_call = task["children"][0]
        self.assertEqual(llm_call["attributes"]["agent"], "任务总结智能体")
        self.assertEqual(payload["summary"]["llm_calls"], 1)
        self.assertEqual(payload["summary"]["prompt_tokens"], 10)
        self.assertEqual(payload["summary"]["completion_tokens"], 2)

    def test_failures_are_marked_on_the_span(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                with start_trace("t2", "research", export_dir=Path(directory)):
                    with span("llm_call"):
                        raise ValueError("boom")

            payload = json.loads((Path(directory) / "t2.json").read_text(encoding="utf-8"))

        self.assertEqual(payload["children"][0]["status"], "error")
        self.assertIn("boom", payload["children"][0]["error"])
        # The root span fails with its child; the trace records both.
        self.assertEqual(sorted(payload["summary"]["errors"]), ["llm_call", "research"])

    def test_export_failure_never_breaks_the_run(self):
        with tempfile.TemporaryDirectory() as directory:
            blocked = Path(directory) / "file"
            blocked.write_text("not a directory", encoding="utf-8")

            with start_trace("t3", "research", export_dir=blocked / "traces") as root:
                root.set("ok", True)

        self.assertTrue(True)  # reaching here means the OSError was swallowed

    def test_span_without_an_active_trace_still_works(self):
        with span("standalone") as standalone:
            standalone.set("value", 1)

        self.assertEqual(standalone.attributes["value"], 1)
        self.assertIsNotNone(standalone.duration_ms)


if __name__ == "__main__":
    unittest.main()
