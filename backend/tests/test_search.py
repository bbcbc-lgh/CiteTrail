import asyncio
import unittest
from unittest.mock import patch

from src.Infrastructure.Config import Settings
from src.Infrastructure.SearchClient import SearchClient


class SearchClientTest(unittest.TestCase):
    def test_backends_follow_fixed_order_when_preference_is_empty(self):
        client = SearchClient(Settings(search_api=""))

        self.assertEqual(client.backends(), ("tavily", "perplexity", "duckduckgo", "searxng"))

    def test_preferred_backend_moves_to_front(self):
        client = SearchClient(Settings(search_api="searxng"))

        self.assertEqual(client.backends(), ("searxng", "tavily", "perplexity", "duckduckgo"))

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
