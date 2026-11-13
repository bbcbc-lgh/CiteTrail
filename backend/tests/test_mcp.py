import asyncio
import json
import logging
import tempfile
import unittest
from pathlib import Path

from src.Infrastructure.Config import Settings
from src.Infrastructure.RunRepository import RunRecord, SqliteRunRepository
from src.Mcp.Server import McpServer
from src.Schema.Source import Source


def setUpModule() -> None:
    logging.disable(logging.CRITICAL)


def tearDownModule() -> None:
    logging.disable(logging.NOTSET)


class FakeSearch:
    def __init__(self, sources):
        self.sources = sources
        self.queries: list[str] = []

    async def search(self, query: str):
        self.queries.append(query)
        return self.sources


def build_server(directory: str, sources=None, repository=None) -> McpServer:
    settings = Settings(_env_file=None, workspace_dir=Path(directory))
    return McpServer(
        settings,
        search_client=FakeSearch(sources or []),
        repository=repository or SqliteRunRepository(Path(directory) / "runs.db"),
    )


class InitializeTest(unittest.TestCase):
    def test_initialize_advertises_the_tools_capability(self):
        with tempfile.TemporaryDirectory() as directory:
            server = build_server(directory)
            response = asyncio.run(server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}))

        result = response["result"]
        self.assertEqual(result["serverInfo"]["name"], "citetrail")
        self.assertIn("tools", result["capabilities"])

    def test_client_protocol_version_is_echoed(self):
        with tempfile.TemporaryDirectory() as directory:
            server = build_server(directory)
            response = asyncio.run(
                server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}})
            )

        self.assertEqual(response["result"]["protocolVersion"], "2025-03-26")


class NotificationTest(unittest.TestCase):
    def test_notifications_get_no_reply(self):
        with tempfile.TemporaryDirectory() as directory:
            server = build_server(directory)
            response = asyncio.run(server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))

        self.assertIsNone(response)


class ToolsTest(unittest.TestCase):
    def test_tools_list_exposes_the_three_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            server = build_server(directory)
            response = asyncio.run(server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))

        names = [tool["name"] for tool in response["result"]["tools"]]
        self.assertEqual(names, ["search_web", "list_research", "get_report"])

    def test_search_web_returns_structured_results(self):
        source = Source(title="标题", url="https://example.com/a", snippet="摘要", provider="bocha")
        with tempfile.TemporaryDirectory() as directory:
            server = build_server(directory, sources=[source])
            response = asyncio.run(
                server.handle(
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {"name": "search_web", "arguments": {"query": "预制菜"}},
                    }
                )
            )

        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload[0]["url"], "https://example.com/a")
        self.assertEqual(payload[0]["found_by"], ["bocha"])

    def test_search_web_rejects_an_empty_query(self):
        with tempfile.TemporaryDirectory() as directory:
            server = build_server(directory)
            response = asyncio.run(
                server.handle(
                    {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "search_web", "arguments": {}}}
                )
            )

        self.assertTrue(response["result"]["isError"])

    def test_list_research_reads_the_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = SqliteRunRepository(Path(directory) / "runs.db")
            repository.save(RunRecord(research_id="run-a", topic="主题"))
            server = build_server(directory, repository=repository)

            response = asyncio.run(
                server.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "list_research", "arguments": {}}})
            )

        payload = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(payload[0]["research_id"], "run-a")

    def test_get_report_returns_markdown_or_a_clear_error(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "r.md"
            report.write_text("# 报告", encoding="utf-8")
            repository = SqliteRunRepository(Path(directory) / "runs.db")
            repository.save(RunRecord(research_id="run-b", topic="t", report_path=str(report)))
            server = build_server(directory, repository=repository)

            found = asyncio.run(
                server.handle(
                    {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "get_report", "arguments": {"research_id": "run-b"}}}
                )
            )
            missing = asyncio.run(
                server.handle(
                    {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "get_report", "arguments": {"research_id": "nope"}}}
                )
            )

        self.assertEqual(found["result"]["content"][0]["text"], "# 报告")
        self.assertTrue(missing["result"]["isError"])

    def test_unknown_tool_and_method_are_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            server = build_server(directory)
            unknown_tool = asyncio.run(
                server.handle({"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "nope", "arguments": {}}})
            )
            unknown_method = asyncio.run(server.handle({"jsonrpc": "2.0", "id": 9, "method": "does/not/exist"}))

        self.assertTrue(unknown_tool["result"]["isError"])
        self.assertEqual(unknown_method["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main()
