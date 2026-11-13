import json
import re
from datetime import datetime, timezone
from pathlib import Path

from src.Schema.Claim import Claim
from src.Schema.Source import Source
from src.Schema.Summary import Summary
from src.Schema.Task import Task

# 1: key_points only. 2: claims with explicit source ids, plus document/chunk slots.
EVIDENCE_SCHEMA_VERSION = 2


class NoteStore:
    """File-system persistence for research notes and reports."""

    def __init__(self, notes_dir: Path):
        self.notes_dir = notes_dir
        self.notes_dir.mkdir(parents=True, exist_ok=True)

    def save_task_summary(self, research_id: str, summary: Summary) -> Path:
        file_path = self.notes_dir / f"{research_id}-{summary.task.id}.json"
        payload = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "research_id": research_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "summary": summary.model_dump(),
        }
        file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return file_path

    def save_report(self, research_id: str, markdown: str) -> Path:
        file_path = self.notes_dir.parent / "reports" / f"{research_id}.md"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(markdown, encoding="utf-8")
        return file_path

    def save_evidence(
        self,
        research_id: str,
        topic: str,
        summaries: list[Summary],
        documents: list[dict] | None = None,
        chunks: list[dict] | None = None,
    ) -> None:
        """Persist the evidence bundle.

        ``documents`` and ``chunks`` have no producer yet (page fetching is a
        later step) but are part of the on-disk contract from now on, so filling
        them in later needs no migration.
        """
        path = self.notes_dir / f"{research_id}-evidence.json"
        payload = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "research_id": research_id,
            "topic": topic,
            "summaries": [item.model_dump() for item in summaries],
            "documents": documents or [],
            "chunks": chunks or [],
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    def load_evidence(self, research_id: str) -> tuple[str, list[Summary]]:
        if not re.fullmatch(r"[A-Za-z0-9-]{1,80}", research_id):
            raise FileNotFoundError("无效的研究编号")
        payload = json.loads((self.notes_dir / f"{research_id}-evidence.json").read_text(encoding="utf-8"))
        summaries = [self._migrate_summary(item) for item in payload.get("summaries", [])]
        return payload["topic"], summaries

    @staticmethod
    def _migrate_summary(item: dict) -> Summary:
        """Read any known evidence version into the current model."""
        if "claims" in item:
            return Summary.model_validate(item)
        # v1 stored plain key_point strings carrying [n] markers; recover what we can.
        sources = [Source.model_validate(source) for source in item.get("sources", [])]
        numbering = {index: source.id for index, source in enumerate(sources, start=1)}
        claims = []
        for text in item.get("key_points", []) or []:
            text = str(text).strip()
            if not text:
                continue
            cited = [numbering[int(number)] for number in re.findall(r"\[(\d+)\]", text) if int(number) in numbering]
            claims.append(Claim(text=text, cited_source_ids=cited))
        return Summary(
            task=Task.model_validate(item["task"]),
            summary=item.get("summary", ""),
            claims=claims,
            sources=sources,
        )
