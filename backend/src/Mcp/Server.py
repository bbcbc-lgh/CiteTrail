"""Minimal MCP server exposing CiteTrail's retrieval to other AI hosts.

The transport is newline-delimited JSON-RPC over stdio, which is what Claude
Desktop / Cursor expect. Protocol handling lives in :class:`McpServer` so it can
be unit tested without a real pipe.
"""

from __future__ import annotations

import json
from typing import Any

from src.Infrastructure.Config import Settings
from src.Infrastructure.RunRepository import SqliteRunRepository
from src.Infrastructure.SearchClient import SearchClient

PROTOCOL_VERSION = "2024-11-05"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_web",
        "description": "用 CiteTrail 配置的多个搜索引擎检索网页，返回标题、链接、摘要与命中的引擎。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索式，3-8 个关键词为宜"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10, "description": "返回条数，默认 5"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "list_research",
        "description": "列出本地已完成的研究记录，按时间倒序。",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}},
        },
    },
    {
        "name": "get_report",
        "description": "按研究编号取回已保存的 Markdown 报告。",
        "inputSchema": {
            "type": "object",
            "properties": {"research_id": {"type": "string"}},
            "required": ["research_id"],
        },
    },
]


class McpServer:
    def __init__(self, settings: Settings, search_client: Any | None = None, repository: Any | None = None):
        self.settings = settings
        self._search = search_client
        self._repository = repository

    @property
    def search(self):
        if self._search is None:
            self._search = SearchClient(self.settings)
        return self._search

    @property
    def repository(self):
        if self._repository is None:
            self._repository = SqliteRunRepository(self.settings.workspace_dir / "runs.db")
        return self._repository

    async def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        """Dispatch one JSON-RPC message. Returns ``None`` for notifications."""
        method = request.get("method")
        request_id = request.get("id")
        if request_id is None:
            # Notifications (e.g. notifications/initialized) never get a reply.
            return None

        try:
            if method == "initialize":
                result = self._initialize(request.get("params") or {})
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                result = await self._call_tool(request.get("params") or {})
            elif method == "ping":
                result = {}
            else:
                return _error(request_id, -32601, f"未知方法：{method}")
        except Exception as exc:  # noqa: BLE001 - protocol errors must not crash the loop
            return _error(request_id, -32603, f"{type(exc).__name__}: {exc}")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            # Echo the client's version when it asked for one; the three tools
            # here use nothing version specific.
            "protocolVersion": params.get("protocolVersion") or PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "citetrail", "version": "0.1.0"},
        }

    async def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name == "search_web":
            return await self._search_web(arguments)
        if name == "list_research":
            return self._list_research(arguments)
        if name == "get_report":
            return self._get_report(arguments)
        return {"content": [{"type": "text", "text": f"未知工具：{name}"}], "isError": True}

    async def _search_web(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query") or "").strip()
        if not query:
            return {"content": [{"type": "text", "text": "缺少 query"}], "isError": True}
        limit = int(arguments.get("limit") or self.settings.search_result_limit)
        limit = max(1, min(limit, 10))
        sources = await self.search.search(query)
        payload = [
            {
                "title": source.title,
                "url": source.url,
                "snippet": source.snippet,
                "found_by": source.found_by or [source.provider],
            }
            for source in sources[:limit]
        ]
        return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}]}

    def _list_research(self, arguments: dict[str, Any]) -> dict[str, Any]:
        limit = max(1, min(int(arguments.get("limit") or 20), 50))
        runs = [run.as_dict() for run in self.repository.list(limit)]
        return {"content": [{"type": "text", "text": json.dumps(runs, ensure_ascii=False, indent=2)}]}

    def _get_report(self, arguments: dict[str, Any]) -> dict[str, Any]:
        research_id = str(arguments.get("research_id") or "").strip()
        run = self.repository.get(research_id)
        if run is None:
            return {"content": [{"type": "text", "text": f"找不到研究：{research_id}"}], "isError": True}
        from pathlib import Path

        path = Path(run.report_path) if run.report_path else None
        markdown = path.read_text(encoding="utf-8") if path and path.is_file() else ""
        return {"content": [{"type": "text", "text": markdown or "该研究还没有生成报告。"}]}


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
