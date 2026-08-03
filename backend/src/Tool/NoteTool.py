from pathlib import Path

from src.Infrastructure.NoteStore import NoteStore
from src.Schema.Summary import Summary
from src.Tool.BaseTool import BaseTool


class NoteTool(BaseTool):
    name = "note"
    description = "保存研究任务的阶段性总结和最终报告。"

    def __init__(self, store: NoteStore):
        self.store = store

    def save_task_summary(self, research_id: str, summary: Summary) -> Path:
        return self.store.save_task_summary(research_id, summary)

    def save_report(self, research_id: str, markdown: str) -> Path:
        return self.store.save_report(research_id, markdown)
