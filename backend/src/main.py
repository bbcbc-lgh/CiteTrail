"""暴露流式研究流程的 FastAPI 应用。"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from src.Infrastructure.Config import get_settings
from src.Infrastructure.Tools import sse_message
from src.Schema.HealthResponse import HealthResponse
from src.Schema.ResearchRequest import ResearchRequest
from src.WorkFlow.ResearchWorkFlow import ResearchWorkFlow

settings = get_settings()
logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))

app = FastAPI(title=settings.app_name, version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=False, allow_methods=["*"], allow_headers=["*"])


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="正常", llm_configured=settings.has_llm, search_backend=settings.search_api)


@app.post(f"{settings.api_prefix}/research/stream")
async def research_stream(request: ResearchRequest) -> StreamingResponse:
    """启动研究任务，并在每个流程节点发送具名 SSE 事件。"""
    workflow = ResearchWorkFlow(settings)

    async def event_stream():
        async for event in workflow.research(request.topic):
            yield sse_message(event.type, event.model_dump(mode="json"))

    return StreamingResponse(event_stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})
