import json
from datetime import datetime, timezone
from pathlib import Path

from src.Schema.TaskSummary import TaskSummary


class NoteStore:
    """File-system persistence for research notes and reports."""

    def __init__(self, notes_dir: Path):
        self.notes_dir = notes_dir
        self.notes_dir.mkdir(parents=True, exist_ok=True)

    def save_task_summary(self, research_id: str, summary: TaskSummary) -> Path:
        file_path = self.notes_dir / f"{research_id}-{summary.task.id}.json"
        payload = {"research_id": research_id, "created_at": datetime.now(timezone.utc).isoformat(), "summary": summary.model_dump()}
        file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return file_path

    def save_report(self, research_id: str, markdown: str) -> Path:
        file_path = self.notes_dir.parent / "reports" / f"{research_id}.md"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(markdown, encoding="utf-8")
        return file_path
