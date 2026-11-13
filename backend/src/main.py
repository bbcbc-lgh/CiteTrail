"""暴露流式研究流程的 FastAPI 应用。"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from src.Infrastructure.Config import get_settings
from src.Infrastructure.LLM import LLMClient
from src.Infrastructure.RunRepository import SqliteRunRepository
from src.Infrastructure.SearchClient import SearchClient
from src.Infrastructure.Tools import sse_message
from src.Schema.HealthResponse import HealthResponse
from src.Schema.ResearchRequest import ResearchRequest
from src.WorkFlow.ResearchWorkFlow import ResearchWorkFlow

settings = get_settings()
cancel_events: dict[str, asyncio.Event] = {}
logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))

app = FastAPI(title=settings.app_name, version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=False, allow_methods=["*"], allow_headers=["*"])


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Report configuration *and* what the configured model can actually do."""
    capabilities = LLMClient(settings).capabilities
    return HealthResponse(
        status="正常",
        llm_configured=settings.has_llm,
        llm_model=capabilities.model_id,
        llm_capabilities=capabilities.as_dict(),
        search_backend=settings.search_api,
        search_backends=SearchClient(settings).available_backends(),
    )


@app.post(f"{settings.api_prefix}/research/stream")
async def research_stream(request: ResearchRequest, research_id: str | None = Header(default=None, alias="X-Research-ID", pattern=r"^[A-Za-z0-9-]{1,80}$")) -> StreamingResponse:
    """启动研究任务，并在每个流程节点发送具名 SSE 事件。"""
    workflow = ResearchWorkFlow(settings)
    research_id = research_id or uuid4().hex
    cancel_event = asyncio.Event()
    cancel_events[research_id] = cancel_event

    async def event_stream():
        try:
            async for event in workflow.research(request.topic, cancel_event, research_id):
                yield sse_message(event.type, event.model_dump(mode="json"))
        finally:
            cancel_events.pop(research_id, None)

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})


@app.post(f"{settings.api_prefix}/research/{{research_id}}/cancel")
async def cancel_research(research_id: str) -> dict[str, str]:
    event = cancel_events.get(research_id)
    if event is None:
        raise HTTPException(status_code=404, detail="研究任务不存在或已结束")
    event.set()
    return {"status": "cancelling", "research_id": research_id}


@app.post(f"{settings.api_prefix}/research/{{research_id}}/report")
async def regenerate_report(research_id: str) -> StreamingResponse:
    workflow = ResearchWorkFlow(settings)

    async def event_stream():
        async for event in workflow.regenerate(research_id):
            yield sse_message(event.type, event.model_dump(mode="json"))

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})


def get_run_repository() -> SqliteRunRepository:
    """Separate seam so tests can point at a temporary database."""
    return SqliteRunRepository(settings.workspace_dir / "runs.db")


@app.get(f"{settings.api_prefix}/research")
def list_research(limit: int = 50, repository: SqliteRunRepository = Depends(get_run_repository)) -> list[dict]:
    """History index, newest first."""
    return [run.as_dict() for run in repository.list(limit)]


@app.get(f"{settings.api_prefix}/research/{{research_id}}")
def get_research(research_id: str, repository: SqliteRunRepository = Depends(get_run_repository)) -> dict:
    """One run plus its saved report, so a reload can restore the page."""
    run = repository.get(research_id)
    if run is None:
        raise HTTPException(status_code=404, detail="研究不存在")
    payload = run.as_dict()
    payload["markdown"] = _read_report(run.report_path)
    return payload


def _read_report(report_path: str) -> str:
    if not report_path:
        return ""
    path = Path(report_path)
    try:
        return path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError:
        return ""
