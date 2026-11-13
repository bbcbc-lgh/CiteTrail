"""Tracing shaped like OpenTelemetry so the exporter can be swapped later.

Spans nest through :mod:`contextvars`, which means instrumentation is a single
``with span(...)`` at each boundary and no call signature has to carry a parent
around. Replacing this module with ``opentelemetry-sdk`` should be a mechanical
substitution: the same span names, the same attribute keys, the same nesting.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

_active_span: ContextVar["SpanRecord | None"] = ContextVar("citrail_active_span", default=None)
_active_trace: ContextVar["Trace | None"] = ContextVar("citrail_active_trace", default=None)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return str(value)


@dataclass
class SpanRecord:
    name: str
    span_id: str
    parent_id: str | None
    started_iso: str
    started_at: float
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    children: list["SpanRecord"] = field(default_factory=list)
    status: str = "ok"
    error: str | None = None
    duration_ms: float | None = None

    def set(self, key: str, value: Any) -> "SpanRecord":
        self.attributes[key] = value
        return self

    def set_many(self, values: dict[str, Any]) -> "SpanRecord":
        self.attributes.update(values)
        return self

    def add_event(self, name: str, **attributes: Any) -> "SpanRecord":
        self.events.append({"name": name, "at": _now_iso(), "attributes": attributes})
        return self

    def mark_error(self, message: str) -> "SpanRecord":
        self.status = "error"
        self.error = message
        return self

    def mark_cancelled(self) -> "SpanRecord":
        if self.status == "ok":
            self.status = "cancelled"
        return self

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "started_at": self.started_iso,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "error": self.error,
            "attributes": _jsonable(self.attributes),
            "events": _jsonable(self.events),
            "children": [child.to_dict() for child in self.children],
        }


@dataclass
class Trace:
    trace_id: str
    spans: list[SpanRecord] = field(default_factory=list)
    root_span: SpanRecord | None = None

    def add(self, record: SpanRecord) -> None:
        self.spans.append(record)

    def summary(self) -> dict[str, Any]:
        llm_spans = [span for span in self.spans if span.name == "llm_call"]
        retrieval_spans = [span for span in self.spans if span.name == "retrieval"]
        return {
            "span_count": len(self.spans),
            "llm_calls": len(llm_spans),
            "retrieval_calls": len(retrieval_spans),
            "prompt_tokens": sum(int(span.attributes.get("llm.prompt_tokens") or 0) for span in llm_spans),
            "completion_tokens": sum(int(span.attributes.get("llm.completion_tokens") or 0) for span in llm_spans),
            "duration_ms": self.root_span.duration_ms if self.root_span else None,
            "errors": [span.name for span in self.spans if span.status == "error"],
            "cancelled": any(span.status == "cancelled" for span in self.spans),
        }

    def to_dict(self) -> dict[str, Any]:
        root = self.root_span.to_dict() if self.root_span else {"children": []}
        return {"trace_id": self.trace_id, **root, "summary": self.summary()}

    def export(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.trace_id}.json"
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path


class Tracer:
    """Creates spans and, for a trace, writes one JSON file per run."""

    def __init__(self, log_spans: bool = True):
        self.log_spans = log_spans
        # Exception types that mean "the caller cancelled", not "the code broke".
        # Registering them keeps cancellation out of error dashboards.
        self.cancellation_types: tuple[type[BaseException], ...] = ()

    @contextmanager
    def start_trace(
        self,
        trace_id: str,
        name: str,
        export_dir: Path | None = None,
        **attributes: Any,
    ) -> Iterator[SpanRecord]:
        trace = Trace(trace_id=trace_id)
        trace_token = _active_trace.set(trace)
        try:
            with self.span(name, **attributes) as root:
                trace.root_span = root
                yield root
        finally:
            _active_trace.reset(trace_token)
            if export_dir is not None:
                try:
                    path = trace.export(export_dir)
                    logger.info("研究链路已导出 | trace=%s path=%s", trace_id, path)
                except OSError as exc:  # exporting must never fail a run
                    logger.warning("研究链路导出失败 | trace=%s error=%s", trace_id, exc)

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Iterator[SpanRecord]:
        trace = _active_trace.get()
        parent = _active_span.get()
        record = SpanRecord(
            name=name,
            span_id=uuid4().hex[:16],
            parent_id=parent.span_id if parent else None,
            started_iso=_now_iso(),
            started_at=time.perf_counter(),
            attributes=dict(attributes),
        )
        token = _active_span.set(record)
        try:
            yield record
        except GeneratorExit:
            record.mark_cancelled()
            raise
        except Exception as exc:
            if isinstance(exc, self.cancellation_types):
                record.mark_cancelled()
            else:
                record.mark_error(f"{type(exc).__name__}: {exc}")
            raise
        finally:
            record.duration_ms = round((time.perf_counter() - record.started_at) * 1000, 2)
            _active_span.reset(token)
            if parent is not None:
                parent.children.append(record)
            if trace is not None:
                trace.add(record)
            self._log(record)

    def _log(self, record: SpanRecord) -> None:
        if not self.log_spans:
            return
        attrs = " ".join(f"{key}={value}" for key, value in record.attributes.items() if value not in (None, "", []))
        suffix = f" {attrs}" if attrs else ""
        message = "span.end name=%s duration_ms=%s status=%s%s"
        if record.status == "error":
            logger.warning(message + " error=%s", record.name, record.duration_ms, record.status, suffix, record.error)
        else:
            logger.info(message, record.name, record.duration_ms, record.status, suffix)


tracer = Tracer()


def span(name: str, **attributes: Any):
    """Open a span on the shared tracer (works with or without an active trace)."""
    return tracer.span(name, **attributes)


def start_trace(trace_id: str, name: str, export_dir: Path | None = None, **attributes: Any):
    return tracer.start_trace(trace_id, name, export_dir=export_dir, **attributes)


def current_span() -> SpanRecord | None:
    return _active_span.get()


def record(**attributes: Any) -> None:
    """Attach attributes to the innermost active span, if there is one."""
    active = _active_span.get()
    if active is not None:
        active.set_many(attributes)
