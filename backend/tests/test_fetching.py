import asyncio
import logging
import unittest
from unittest.mock import AsyncMock, patch

from src.Agent.SummarizerAgent import SummarizerAgent
from src.Infrastructure.Chunking import select_chunks, split_text
from src.Infrastructure.Config import Settings
from src.Infrastructure.Fetcher import (
    PageFetcher,
    build_inline_documents,
    excerpts_by_source,
    extract_readable,
    group_chunks_by_source,
)
from src.Schema.Chunk import Chunk
from src.Schema.Document import Document
from src.Schema.Source import Source


def setUpModule() -> None:
    logging.disable(logging.CRITICAL)


def tearDownModule() -> None:
    logging.disable(logging.NOTSET)


class SplitTextTest(unittest.TestCase):
    def test_short_text_stays_one_chunk(self):
        self.assertEqual(split_text("只有一句话。", size=700), ["只有一句话。"])

    def test_empty_text_yields_no_chunks(self):
        self.assertEqual(split_text("   "), [])

    def test_long_text_is_split_below_the_limit(self):
        text = "这是一个测试句子，用来凑长度。" * 200

        chunks = split_text(text, size=300, overlap=0)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= 300 for chunk in chunks))

    def test_overlap_repeats_the_tail_of_the_previous_chunk(self):
        text = "".join(f"第{i}句内容。" for i in range(1, 60))

        chunks = split_text(text, size=100, overlap=20)

        self.assertGreater(len(chunks), 2)
        for previous, following in zip(chunks, chunks[1:]):
            tail = previous[-20:].lstrip()
            self.assertTrue(following.startswith(tail), f"未找到重叠：{following[:40]!r}")

    def test_a_single_oversized_sentence_is_hard_split(self):
        text = "无标点" * 500

        chunks = split_text(text, size=200, overlap=0)

        self.assertTrue(all(len(chunk) <= 200 for chunk in chunks))


class SelectChunksTest(unittest.TestCase):
    def test_chunks_matching_the_query_are_preferred(self):
        texts = [
            "本段讨论菜谱与烹饪步骤。",
            "SSE 断线后需要心跳与 Last-Event-ID 才能正确重连。",
            "本段讨论园艺与浇水。",
        ]

        selected = select_chunks("SSE 断线重连", texts, limit=1)

        self.assertEqual(selected, [1])

    def test_selection_keeps_document_order(self):
        texts = ["SSE 重连", "无关内容", "SSE 心跳"]

        selected = select_chunks("SSE 重连", texts, limit=2)

        self.assertEqual(selected, sorted(selected))

    def test_limit_larger_than_corpus_returns_everything(self):
        self.assertEqual(select_chunks("x", ["a", "b"], limit=5), [0, 1])

    def test_no_matching_terms_falls_back_to_the_first_chunks(self):
        self.assertEqual(select_chunks("完全不相关", ["甲乙丙", "丁戊己"], limit=1), [0])


class ExtractReadableTest(unittest.TestCase):
    def test_navigation_and_scripts_are_dropped(self):
        html = (
            "<html><head><title>x</title><style>body{}</style></head><body>"
            "<nav>首页 关于我们</nav>"
            "<article><p>SSE 断线后应立即释放服务端连接。</p>"
            "<p>客户端会按 retry 间隔自动重连。</p></article>"
            "<script>var x = 1;</script>"
            "<footer>版权所有</footer></body></html>"
        )

        text = extract_readable(html, "https://example.com/a")

        self.assertIn("释放服务端连接", text)
        self.assertIn("自动重连", text)
        self.assertNotIn("版权所有", text)
        self.assertNotIn("var x", text)


class FakeResponse:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None, charset: str = "utf-8"):
        self._body = body
        self.headers = headers if headers is not None else {"content-type": "text/html; charset=utf-8"}
        self.charset_encoding = charset

    def raise_for_status(self) -> None:
        pass

    async def aiter_bytes(self):
        yield self._body


class FakeStream:
    def __init__(self, response):
        self.response = response

    async def __aenter__(self):
        return self.response

    async def __aexit__(self, *args):
        return False


class FakeClient:
    def __init__(self, responses: dict):
        self.responses = responses

    def stream(self, method: str, url: str):
        response = self.responses.get(url)
        if response is None:
            raise httpx_error()
        return FakeStream(response)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


def httpx_error():
    import httpx

    return httpx.ConnectError("boom")


def make_fetcher(responses: dict) -> PageFetcher:
    settings = Settings(_env_file=None, fetch_pages=True, fetch_max_sources=4, chunks_per_source=2)
    return PageFetcher(settings, client_factory=lambda: FakeClient(responses))


class EnrichTest(unittest.TestCase):
    def test_documents_and_chunks_are_produced_for_fetched_pages(self):
        url = "https://example.com/a"
        source = Source(title="SSE", url=url)
        body = ("<html><body><p>SSE 断线后需要心跳与 Last-Event-ID。</p></body></html>").encode()
        fetcher = make_fetcher({url: FakeResponse(body)})

        with patch.object(PageFetcher, "_is_public_host", AsyncMock(return_value=True)):
            documents, chunks = asyncio.run(fetcher.enrich("SSE 断线", [source]))

        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0].source_id, source.id)
        self.assertGreaterEqual(len(chunks), 1)
        self.assertEqual(chunks[0].document_id, documents[0].id)

    def test_a_failed_page_does_not_remove_the_source(self):
        ok = Source(title="ok", url="https://example.com/ok")
        broken = Source(title="broken", url="https://example.com/broken")
        body = "<html><body><p>正文内容。</p></body></html>".encode()
        fetcher = make_fetcher({"https://example.com/ok": FakeResponse(body)})

        with patch.object(PageFetcher, "_is_public_host", AsyncMock(return_value=True)):
            documents, chunks = asyncio.run(fetcher.enrich("query", [broken, ok]))

        self.assertEqual([document.source_id for document in documents], [ok.id])

    def test_private_hosts_are_refused(self):
        fetcher = make_fetcher({})
        source = Source(title="local", url="http://127.0.0.1:8080/admin")

        documents, chunks = asyncio.run(fetcher.enrich("query", [source]))

        self.assertEqual(documents, [])
        self.assertEqual(chunks, [])

    def test_unsupported_scheme_is_refused(self):
        fetcher = make_fetcher({})
        source = Source(title="files", url="file:///etc/passwd")

        documents, _ = asyncio.run(fetcher.enrich("query", [source]))

        self.assertEqual(documents, [])

    def test_only_the_configured_number_of_sources_is_fetched(self):
        sources = [Source(title=f"s{i}", url=f"https://example.com/{i}") for i in range(6)]
        body = "<html><body><p>正文内容。</p></body></html>".encode()
        responses = {source.url: FakeResponse(body) for source in sources}
        settings = Settings(_env_file=None, fetch_pages=True, fetch_max_sources=2, chunks_per_source=1)
        fetcher = PageFetcher(settings, client_factory=lambda: FakeClient(responses))

        with patch.object(PageFetcher, "_is_public_host", AsyncMock(return_value=True)):
            documents, _ = asyncio.run(fetcher.enrich("q", sources))

        self.assertEqual(len(documents), 2)


class InlineContentTest(unittest.TestCase):
    def test_body_returned_with_the_search_result_becomes_evidence(self):
        source = Source(
            title="正文直出",
            url="https://example.com/a",
            content="预制菜行业增长动力。" * 60,
        )

        documents, chunks = build_inline_documents("预制菜 增长", [source], chunks_per_source=2)

        self.assertEqual(len(documents), 1)
        self.assertEqual(documents[0].source_id, source.id)
        self.assertEqual(documents[0].meta["source"], "search_api")
        self.assertGreaterEqual(len(chunks), 1)

    def test_stub_bodies_are_ignored(self):
        source = Source(title="短", url="https://example.com/b", content="太短了")

        documents, chunks = build_inline_documents("查询", [source], chunks_per_source=2)

        self.assertEqual(documents, [])
        self.assertEqual(chunks, [])

    def test_sources_without_content_are_ignored(self):
        source = Source(title="无正文", url="https://example.com/c", snippet="只有摘要")

        self.assertEqual(build_inline_documents("查询", [source], chunks_per_source=2), ([], []))


class GroupingTest(unittest.TestCase):
    def test_chunks_are_grouped_back_to_their_source(self):
        first = Document(id="d1", source_id="s1", text="x")
        second = Document(id="d2", source_id="s2", text="y")

        chunks = [Chunk(id="c1", document_id="d1", text="a"), Chunk(id="c2", document_id="d2", text="b")]

        grouped = group_chunks_by_source([first, second], chunks)

        self.assertEqual(grouped["s1"][0].id, "c1")
        self.assertEqual(grouped["s2"][0].id, "c2")

    def test_excerpts_join_the_chunks_of_each_source(self):
        grouped = {
            "s1": [Chunk(id="c1", document_id="d", text="第一段"), Chunk(id="c2", document_id="d", text="第二段")],
        }

        self.assertEqual(excerpts_by_source(grouped), {"s1": "第一段\n\n第二段"})


class BuildEntriesTest(unittest.TestCase):
    def test_fetched_excerpt_replaces_the_snippet_but_failed_sources_keep_it(self):
        fetched = Source(title="a", url="https://a.example", snippet="摘要A")
        failed = Source(title="b", url="https://b.example", snippet="摘要B")

        from src.Schema.Chunk import Chunk

        entries = SummarizerAgent.build_entries(
            [fetched, failed], {fetched.id: [Chunk(id="c", document_id="d", text="正文A")]}
        )

        self.assertEqual(entries[0]["excerpt"], "正文A")
        self.assertNotIn("excerpt", entries[1])
        self.assertEqual(entries[1]["snippet"], "摘要B")


if __name__ == "__main__":
    unittest.main()
