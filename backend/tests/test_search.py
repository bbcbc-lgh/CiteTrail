import asyncio
import unittest
from unittest.mock import patch

from src.Infrastructure.Config import Settings
from src.Infrastructure.SearchClient import SearchClient
from src.Schema.Source import Source


class SearchClientTest(unittest.TestCase):
    def test_backends_follow_fixed_order_when_preference_is_empty(self):
        client = SearchClient(Settings(search_api=""))

        self.assertEqual(client.backends(), ("tavily", "perplexity", "duckduckgo", "searxng"))

    def test_preferred_backend_moves_to_front(self):
        client = SearchClient(Settings(search_api="searxng"))

        self.assertEqual(client.backends(), ("searxng", "tavily", "perplexity", "duckduckgo"))

    def test_relevant_results_rank_before_unrelated_results(self):
        relevant = Source(title="FastAPI SSE disconnect handling", url="https://example.com/sse", snippet="SSE stream cleanup")
        unrelated = Source(title="Spring Boot e-commerce project", url="https://example.com/shop", snippet="Flash sale architecture")

        results = SearchClient._rank_relevant([unrelated, relevant], "FastAPI SSE 用户体验")

        self.assertEqual(results, [relevant])

    def test_english_planner_query_is_not_filtered_by_chinese_topic(self):
        source = Source(
            title="FastAPI SSE client disconnect cleanup",
            url="https://fastapi.tiangolo.com/advanced/behind-a-proxy/",
            snippet="Handle disconnect and reconnect while cleaning up the stream.",
        )
        client = SearchClient(Settings(_env_file=None))
        with patch.object(client, "_search_with", return_value=[source]):
            results = asyncio.run(client.search(
                "FastAPI Server-Sent Events SSE disconnect cleanup reconnect",
                "FastAPI SSE 断线处理实践",
            ))
        self.assertEqual(results[0].url, source.url)

    def test_long_english_query_accepts_source_matching_core_terms(self):
        sources = [Source(
            title="FastAPI SSE client disconnect cleanup",
            url="https://example.com/sse",
            snippet="StreamingResponse cleanup handles client disconnect and cancellation.",
        )]
        query = "FastAPI Starlette Server-Sent Events SSE streaming response connection lifecycle client disconnect cancellation semantics documentation"
        self.assertEqual(SearchClient._rank_relevant(sources, query), sources)

    def test_perplexity_returns_search_results(self):
        client = SearchClient(Settings(perplexity_api_key="test-key"))

        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return {"search_results": [{"title": "Example", "url": "https://example.com", "snippet": "Example snippet"}]}

        class HttpClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, *args, **kwargs):
                return Response()

        with patch("src.Infrastructure.SearchClient.httpx.AsyncClient", return_value=HttpClient()):
            results = asyncio.run(client._perplexity("test"))

        self.assertEqual(results[0].title, "Example")
        self.assertEqual(results[0].url, "https://example.com")
        self.assertEqual(results[0].provider, "perplexity")

    def test_perplexity_uses_citations_without_search_results(self):
        client = SearchClient(Settings(perplexity_api_key="test-key"))

        class Response:
            def raise_for_status(self):
                pass

            def json(self):
                return {"citations": ["https://example.com/article"]}

        class HttpClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def post(self, *args, **kwargs):
                return Response()

        with patch("src.Infrastructure.SearchClient.httpx.AsyncClient", return_value=HttpClient()):
            results = asyncio.run(client._perplexity("test"))

        self.assertEqual(results[0].title, "example.com")
        self.assertEqual(results[0].url, "https://example.com/article")
