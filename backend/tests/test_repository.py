import logging
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.Infrastructure.RunRepository import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
    RunRecord,
    SqliteRunRepository,
)
from src.main import app, get_run_repository


def setUpModule() -> None:
    logging.disable(logging.CRITICAL)


def tearDownModule() -> None:
    logging.disable(logging.NOTSET)


class RepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.repo = SqliteRunRepository(Path(self._dir.name) / "runs.db")

    def tearDown(self) -> None:
        self._dir.cleanup()

    def test_round_trip(self):
        self.repo.save(RunRecord(research_id="run-1", topic="预制菜", status=STATUS_RUNNING))

        record = self.repo.get("run-1")

        self.assertIsNotNone(record)
        self.assertEqual(record.topic, "预制菜")
        self.assertEqual(record.status, STATUS_RUNNING)

    def test_finishing_a_run_updates_it_without_changing_created_at(self):
        self.repo.save(RunRecord(research_id="run-2", topic="主题", status=STATUS_RUNNING))
        created = self.repo.get("run-2").created_at

        self.repo.save(
            RunRecord(
                research_id="run-2",
                topic="主题",
                status=STATUS_COMPLETED,
                task_count=4,
                source_count=9,
                claim_count=31,
                report_path="C:/tmp/report.md",
            )
        )

        record = self.repo.get("run-2")
        self.assertEqual(record.status, STATUS_COMPLETED)
        self.assertEqual(record.task_count, 4)
        self.assertEqual(record.claim_count, 31)
        self.assertEqual(record.created_at, created)
        self.assertEqual(len(self.repo.list()), 1)

    def test_list_is_newest_first_and_respects_limit(self):
        for index in range(3):
            self.repo.save(RunRecord(research_id=f"run-{index}", topic=f"t{index}", created_at=f"2026-01-0{index + 1}T00:00:00+00:00"))

        listed = self.repo.list()

        self.assertEqual([item.research_id for item in listed], ["run-2", "run-1", "run-0"])
        self.assertEqual(len(self.repo.list(limit=2)), 2)

    def test_unknown_id_returns_none(self):
        self.assertIsNone(self.repo.get("does-not-exist"))

    def test_malformed_id_is_rejected_without_querying(self):
        self.assertIsNone(self.repo.get("../etc/passwd"))

    def test_failed_run_keeps_its_reason(self):
        self.repo.save(RunRecord(research_id="run-x", topic="t", status=STATUS_FAILED, reason="模型不可用"))

        self.assertEqual(self.repo.get("run-x").reason, "模型不可用")


class HistoryApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.repo = SqliteRunRepository(Path(self._dir.name) / "runs.db")
        app.dependency_overrides[get_run_repository] = lambda: self.repo
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        self._dir.cleanup()

    def test_history_lists_runs(self):
        self.repo.save(RunRecord(research_id="api-1", topic="历史主题", status=STATUS_COMPLETED))

        response = self.client.get("/api/research")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["research_id"], "api-1")

    def test_detail_includes_the_saved_report(self):
        report = Path(self._dir.name) / "report.md"
        report.write_text("# 报告正文", encoding="utf-8")
        self.repo.save(
            RunRecord(research_id="api-2", topic="t", status=STATUS_COMPLETED, report_path=str(report))
        )

        payload = self.client.get("/api/research/api-2").json()

        self.assertEqual(payload["markdown"], "# 报告正文")

    def test_missing_run_is_404(self):
        self.assertEqual(self.client.get("/api/research/nope").status_code, 404)

    def test_missing_report_file_yields_empty_markdown(self):
        self.repo.save(
            RunRecord(research_id="api-3", topic="t", status=STATUS_COMPLETED, report_path=str(Path(self._dir.name) / "gone.md"))
        )

        self.assertEqual(self.client.get("/api/research/api-3").json()["markdown"], "")


if __name__ == "__main__":
    unittest.main()
