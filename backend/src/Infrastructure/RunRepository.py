"""Persistent index of research runs.

Evidence and reports remain plain files under ``workspace/``; this module only
answers "what runs exist and how did they go". Without it the UI cannot list
history, and a closed tab means the result is effectively lost.

SQLite is used because it ships with Python: a single-file store that supports
ordered listing without adding a dependency.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RunRecord:
    research_id: str
    topic: str
    status: str = STATUS_RUNNING
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    degraded: bool = False
    task_count: int = 0
    source_count: int = 0
    claim_count: int = 0
    report_path: str = ""
    reason: str = ""

    def as_dict(self) -> dict:
        data = asdict(self)
        data["degraded"] = bool(self.degraded)
        return data


class _Connection:
    """Context manager that commits/rolls back **and closes** the connection."""

    def __init__(self, path: Path):
        self._path = path
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> sqlite3.Connection:
        self._conn = sqlite3.connect(self._path, timeout=5)
        self._conn.row_factory = sqlite3.Row
        return self._conn

    def __exit__(self, exc_type, exc, traceback) -> bool:
        assert self._conn is not None
        try:
            self._conn.commit() if exc_type is None else self._conn.rollback()
        finally:
            self._conn.close()
        return False


class RunRepository(Protocol):
    """Minimal contract so the storage engine can be swapped."""

    def save(self, run: RunRecord) -> None: ...

    def get(self, research_id: str) -> RunRecord | None: ...

    def list(self, limit: int = 50) -> list[RunRecord]: ...


class SqliteRunRepository:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._session() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    research_id TEXT PRIMARY KEY,
                    topic TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    degraded INTEGER NOT NULL DEFAULT 0,
                    task_count INTEGER NOT NULL DEFAULT 0,
                    source_count INTEGER NOT NULL DEFAULT 0,
                    claim_count INTEGER NOT NULL DEFAULT 0,
                    report_path TEXT NOT NULL DEFAULT '',
                    reason TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_created ON runs(created_at DESC)")

    def _session(self):
        """Open a connection that is committed and *closed* on exit.

        ``sqlite3.Connection`` used directly as a context manager commits but
        never closes, which leaks file handles and blocks deletion on Windows.
        """
        return _Connection(self.path)

    def save(self, run: RunRecord) -> None:
        """Upsert: a run is written once when it starts and again when it ends."""
        run.updated_at = _now()
        with self._session() as conn:
            conn.execute(
                """
                INSERT INTO runs (research_id, topic, status, created_at, updated_at,
                                  degraded, task_count, source_count, claim_count, report_path, reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(research_id) DO UPDATE SET
                    status=excluded.status,
                    updated_at=excluded.updated_at,
                    degraded=excluded.degraded,
                    task_count=excluded.task_count,
                    source_count=excluded.source_count,
                    claim_count=excluded.claim_count,
                    report_path=excluded.report_path,
                    reason=excluded.reason
                """,
                (
                    run.research_id,
                    run.topic,
                    run.status,
                    run.created_at,
                    run.updated_at,
                    int(run.degraded),
                    run.task_count,
                    run.source_count,
                    run.claim_count,
                    run.report_path,
                    run.reason,
                ),
            )

    def get(self, research_id: str) -> RunRecord | None:
        # Same guard as the file store: an id from a request must never become a path.
        if not research_id or not all(char.isalnum() or char == "-" for char in research_id):
            return None
        with self._session() as conn:
            row = conn.execute("SELECT * FROM runs WHERE research_id = ?", (research_id,)).fetchone()
        return self._to_record(row) if row else None

    def list(self, limit: int = 50) -> list[RunRecord]:
        limit = max(1, min(int(limit), 200))
        with self._session() as conn:
            rows = conn.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self._to_record(row) for row in rows]

    @staticmethod
    def _to_record(row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            research_id=row["research_id"],
            topic=row["topic"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            degraded=bool(row["degraded"]),
            task_count=row["task_count"],
            source_count=row["source_count"],
            claim_count=row["claim_count"],
            report_path=row["report_path"],
            reason=row["reason"],
        )

