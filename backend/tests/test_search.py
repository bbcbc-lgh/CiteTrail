import asyncio
import logging
import unittest
from unittest.mock import patch

import httpx

from src.Infrastructure.Config import Settings
from src.Infrastructure.SearchClient import SearchClient
from src.Schema.Source import Source


def setUpModule() -> None:
    logging.disable(logging.CRITICAL)


def tearDownModule() -> None:
    logging.disable(logging.NOTSET)


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self):
        return self._payload


class FakeHttpClient:
    def __init__(self, payload):
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, *args, **kwargs):
        return FakeResponse(self.payload)


def search_with_payload(coroutine, payload):
    with patch("src.Infrastructure.SearchClient.httpx.AsyncClient", return_value=FakeHttpClient(payload)):
        return asyncio.run(coroutine)


class BackendOrderTest(unittest.TestCase):
    def test_domestic_backends_come_first(self):
        client = SearchClient(Settings(_env_file=None, search_api=""))

        self.assertEqual(
            client.backends(),
            ("bocha", "zhipu", "baidu", "deepseek", "metaso", "tavily"),
        )

    def test_preferred_backend_moves_to_front(self):
        client = SearchClient(Settings(_env_file=None, search_api="tavily"))

        self.assertEqual(
            client.backends(),
            ("tavily", "bocha", "zhipu", "baidu", "deepseek", "metaso"),
        )

    def test_removed_backends_are_gone(self):
        client = SearchClient(Settings(_env_file=None, search_api=""))

        for retired in ("duckduckgo", "perplexity", "searxng"):
            self.assertNotIn(retired, client.backends())


class FusionTest(unittest.TestCase):
    def _client(self, **settings) -> SearchClient:
        base = dict(
            _env_file=None,
            search_mode="fusion",
            search_fusion_backends="bocha,zhipu,baidu",
            bocha_api_key="k1",
            zhipu_api_key="k2",
            baidu_api_key="k3",
            tavily_api_key="k4",
            search_api="tavily",
        )
        base.update(settings)
        return SearchClient(Settings(**base))

    def test_same_page_from_two_indexes_is_merged_and_boosted(self):
        client = self._client()

        async def fake(backend: str, query: str):
            if backend == "bocha":
                return [Source(title="共用页面", url="https://example.com/a")]
            if backend == "zhipu":
                # Tracking parameters must not create a duplicate entry.
                return [Source(title="共用页面", url="https://example.com/a?utm_source=x")]
            return [Source(title="共用页面的另一篇", url="https://example.com/b")]

        client._search_with = fake
        results = asyncio.run(client.search("共用页面"))

        shared = next(source for source in results if "example.com/a" in source.url)
        self.assertEqual(sorted(shared.found_by), ["bocha", "zhipu"])
        # Agreement outranks a page only one index returned.
        self.assertEqual(results[0].url, shared.url)
        self.assertEqual(len([s for s in results if "example.com/a" in s.url]), 1)

    def test_fusion_falls_back_to_tavily_when_domestic_yields_nothing(self):
        client = self._client()
        called: list[str] = []

        async def fake(backend: str, query: str):
            called.append(backend)
            if backend == "tavily":
                return [Source(title="查询 兜底结果", url="https://tavily.example/x")]
            return []

        client._search_with = fake
        results = asyncio.run(client.search("查询"))

        self.assertIn("tavily", called)
        self.assertEqual([source.url for source in results], ["https://tavily.example/x"])

    def test_unconfigured_fusion_backends_are_never_called(self):
        client = self._client(zhipu_api_key="", baidu_api_key="")
        called: list[str] = []

        async def fake(backend: str, query: str):
            called.append(backend)
            return [Source(title="查询结果", url="https://example.com/t")]

        client._search_with = fake
        asyncio.run(client.search("查询"))

        self.assertEqual(called, ["bocha"])

    def test_fallback_mode_keeps_the_sequential_behaviour(self):
        client = self._client(search_mode="fallback")
        called: list[str] = []

        async def fake(backend: str, query: str):
            called.append(backend)
            return [Source(title="查询结果", url="https://example.com/t")] if backend == "tavily" else []

        client._search_with = fake
        results = asyncio.run(client.search("查询"))

        self.assertEqual(results[0].url, "https://example.com/t")
        # Tavily is preferred in fallback mode, and it answered first.
        self.assertEqual(called, ["tavily"])


class BackendFailureTest(unittest.TestCase):
    def test_a_permanently_failed_backend_is_not_retried_in_the_same_run(self):
        client = SearchClient(Settings(_env_file=None, search_api="bocha", bocha_api_key="k", tavily_api_key=""))
        calls: list[str] = []

        async def fake_search(backend: str, query: str):
            calls.append(backend)
            if backend == "bocha":
                raise httpx.ConnectError("connection refused")
            return []

        client._search_with = fake_search

        asyncio.run(client.search("q1"))
        asyncio.run(client.search("q2"))

        self.assertEqual(calls.count("bocha"), 1)

    def test_transient_failure_is_retried_on_the_next_query(self):
        client = SearchClient(Settings(_env_file=None, search_api="tavily", tavily_api_key="k"))
        calls: list[str] = []

        async def fake_search(backend: str, query: str):
            calls.append(backend)
            if backend == "tavily":
                raise httpx.ReadTimeout("slow")
            return []

        client._search_with = fake_search

        asyncio.run(client.search("q1"))
        asyncio.run(client.search("q2"))

        self.assertEqual(calls.count("tavily"), 2)

    def test_unconfigured_backends_are_reported_as_such(self):
        client = SearchClient(Settings(_env_file=None, search_api="tavily", tavily_api_key="k"))

        status = {item["name"]: item["configured"] for item in client.available_backends()}

        self.assertTrue(status["tavily"])
        self.assertFalse(status["bocha"])
        self.assertFalse(status["zhipu"])


class BochaTest(unittest.TestCase):
    def test_documented_response_shape_maps_to_sources(self):
        payload = {
            "code": 200,
            "data": {
                "webPages": {
                    "value": [
                        {
                            "name": "预制菜行业观察",
                            "url": "https://example.com/a",
                            "snippet": "短摘要",
                            "summary": "更完整的长摘要",
                        }
                    ]
                }
            },
        }
        client = SearchClient(Settings(_env_file=None, bocha_api_key="k"))

        results = search_with_payload(client._bocha("预制菜 行业"), payload)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "预制菜行业观察")
        self.assertEqual(results[0].url, "https://example.com/a")
        self.assertEqual(results[0].snippet, "更完整的长摘要")
        self.assertEqual(results[0].provider, "bocha")

    def test_lowercase_webpages_key_is_also_accepted(self):
        payload = {"data": {"webpages": [{"name": "标题", "url": "https://example.com/b"}]}}

        items = SearchClient._bocha_items(payload)

        self.assertEqual(len(items), 1)

    def test_unrecognised_shape_returns_nothing(self):
        self.assertEqual(SearchClient._bocha_items({"unexpected": True}), [])
        self.assertEqual(SearchClient._bocha_items({}), [])

    def test_items_without_a_url_are_dropped(self):
        payload = {"data": {"webPages": {"value": [{"name": "无链接"}, {"name": "有链接", "url": "https://example.com/c"}]}}}
        client = SearchClient(Settings(_env_file=None, bocha_api_key="k"))

        results = search_with_payload(client._bocha("查询"), payload)

        self.assertEqual([result.url for result in results], ["https://example.com/c"])


class ZhipuTest(unittest.TestCase):
    def test_documented_response_shape_maps_to_sources(self):
        payload = {
            "id": "abc",
            "search_result": [
                {"title": "智谱结果", "link": "https://example.com/z", "content": "正文摘要", "publish_date": "2026-01-01"}
            ],
        }
        client = SearchClient(Settings(_env_file=None, zhipu_api_key="k"))

        results = search_with_payload(client._zhipu("查询"), payload)

        self.assertEqual(results[0].title, "智谱结果")
        self.assertEqual(results[0].url, "https://example.com/z")
        self.assertEqual(results[0].snippet, "正文摘要")
        self.assertEqual(results[0].provider, "zhipu")

    def test_unrecognised_shape_returns_nothing(self):
        client = SearchClient(Settings(_env_file=None, zhipu_api_key="k"))

        results = search_with_payload(client._zhipu("查询"), {"unexpected": True})

        self.assertEqual(results, [])


class DeepSeekTest(unittest.TestCase):
    def test_documented_shape_maps_summary_to_snippet(self):
        payload = {
            "results": [
                {
                    "rerank_score": 2.1,
                    "title": "预制菜产业观察",
                    "url": "https://example.com/ds",
                    "snippet": "短摘要",
                    "summary": "长摘要，通常六百到八百字",
                    "content": "",
                    "authority": 3,
                }
            ]
        }
        client = SearchClient(Settings(_env_file=None, deepseek_api_key="k"))

        results = search_with_payload(client._deepseek("预制菜"), payload)

        self.assertEqual(results[0].title, "预制菜产业观察")
        self.assertEqual(results[0].snippet, "长摘要，通常六百到八百字")
        self.assertEqual(results[0].provider, "deepseek")

    def test_missing_results_key_returns_nothing(self):
        client = SearchClient(Settings(_env_file=None, deepseek_api_key="k"))

        self.assertEqual(search_with_payload(client._deepseek("q"), {"unexpected": 1}), [])


class MetasoTest(unittest.TestCase):
    def test_error_envelope_on_http_200_is_treated_as_failure(self):
        client = SearchClient(Settings(_env_file=None, metaso_api_key="k"))

        results = search_with_payload(
            client._metaso("查询"), {"errCode": 2005, "errMsg": "API密钥无效"}
        )

        self.assertEqual(results, [])

    def test_results_inside_data_are_parsed(self):
        payload = {"errCode": 0, "data": {"list": [{"title": "秘塔结果", "url": "https://example.com/m", "summary": "摘要"}]}}
        client = SearchClient(Settings(_env_file=None, metaso_api_key="k"))

        results = search_with_payload(client._metaso("查询"), payload)

        self.assertEqual(results[0].title, "秘塔结果")
        self.assertEqual(results[0].url, "https://example.com/m")
        self.assertEqual(results[0].provider, "metaso")


class RankingDiversityTest(unittest.TestCase):
    def test_one_domain_cannot_fill_every_slot(self):
        sources = [
            Source(title=f"SSE 重连 方案 {index}", url=f"https://same.example/a{index}")
            for index in range(4)
        ]

        result = SearchClient._rank_relevant(sources, "SSE 重连")

        self.assertEqual(len(result), SearchClient.MAX_PER_DOMAIN)

    def test_syndicated_copies_of_the_same_title_collapse(self):
        first = Source(title="SSE 断线重连完整指南", url="https://a.example/x")
        second = Source(title="SSE 断线重连完整指南", url="https://b.example/y")

        result = SearchClient._rank_relevant([first, second], "SSE 断线重连")

        self.assertEqual(len(result), 1)

    def test_a_rare_term_outweighs_a_term_every_candidate_has(self):
        sources = [
            Source(title="SSE 入门", url="https://a.example/1"),
            Source(title="SSE 进阶", url="https://b.example/2"),
            Source(title="SSE backoff 重试", url="https://c.example/3"),
        ]

        result = SearchClient._rank_relevant(sources, "SSE backoff")

        self.assertEqual(result[0].url, "https://c.example/3")


class RankingTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
