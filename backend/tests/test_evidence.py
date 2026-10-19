import unittest

from src.Infrastructure.Evidence import clean_source
from src.Infrastructure.SearchClient import SearchClient
from src.Infrastructure.Config import Settings
from src.Schema.Source import Source
from src.Schema.Summary import Summary
from src.Schema.Task import Task
from src.WorkFlow.ResearchWorkFlow import ResearchWorkFlow


class EvidenceTest(unittest.TestCase):
    def test_clean_noise_broken_code_and_keep_complete_code_separate(self):
        source = Source(title="SSE [Bug]", url="https://github.com/example/project/issues/1", snippet=(
            "Activity @someone commented on Apr 1, 2026\n"
            "```js\nconst stream = new EventSource('/sse');\n```\n"
            "// const = broken();\n"
            "SSE disconnect cleanup releases resources promptly.\n"
            "Image 15\n当前文章被以下社区和专栏收录：\n"
            "```python\nasync def broken(…"
        ))
        result = clean_source(source)
        self.assertEqual(result.snippet, "SSE disconnect cleanup releases resources promptly.")
        self.assertEqual(result.source_type, "community")
        self.assertIn("const stream", result.code)
        self.assertNotIn("broken", result.code)

    def test_rank_requires_topic_framework_and_intent(self):
        sources = [
            Source(title="SSE reconnect in Spring Boot", url="https://example.com/spring"),
            Source(title="FastAPI SSE introduction", url="https://example.com/intro"),
            Source(title="FastAPI SSE disconnect cleanup", url="https://fastapi.tiangolo.com/sse", source_type="official"),
            Source(title="FastAPI SSE disconnect discussion", url="https://github.com/x/y/issues/1", source_type="community"),
        ]
        result = SearchClient._rank_relevant(sources, "FastAPI SSE 断线处理实践")
        self.assertEqual(result, sources[2:])
        self.assertEqual(SearchClient._rank_relevant(sources[:2], "FastAPI SSE 断线处理实践"), [])

    def test_fallback_has_direct_citations_deduplicates_and_marks_gaps(self):
        source = clean_source(Source(title="[Bug] cleanup", url="https://github.com/x/y/issues/1", snippet="SSE disconnect cleanup releases resources promptly."))
        summaries = [Summary(task=Task(title=title, intent="解释断线处理", query="FastAPI SSE"), summary="unused", sources=[source]) for title in ("原理", "应用")]
        report = ResearchWorkFlow._fallback_report("FastAPI SSE", summaries)
        self.assertEqual(report.count(source.snippet), 1)
        self.assertIn("[1]（社区观点，未经独立验证）", report)
        self.assertIn("尚未回答的问题", report)
        self.assertIn("应用：解释断线处理", report)
        self.assertNotIn("[[Bug]", report)

    def test_missing_snippet_is_a_lead_not_a_claim(self):
        source = clean_source(Source(title="FastAPI SSE", url="https://example.com"))
        summary = Summary(task=Task(title="目标", intent="解释", query="test"), summary="unused", sources=[source])
        report = ResearchWorkFlow._fallback_report("test", [summary])
        self.assertIn("缺少可读摘要", report)
        self.assertIn("没有新增的可读证据", report)

    def test_normalize_filters_unsafe_urls_and_keeps_bracket_title_readable(self):
        client = SearchClient(Settings(_env_file=None))
        sources = client._normalize([Source(title="unsafe", url="javascript:alert(1)"), Source(title="[Bug] SSE", url="https://example.com")], "test")
        self.assertEqual(len(sources), 1)

    def test_clean_source_removes_page_chrome_and_caps_merged_snippet(self):
        source = Source(title="SSE", url="https://example.com", snippet=(
            "正文说明连接断开后应释放资源。 分享复制链接 Table of Contents "
            + "很长的抓取片段。" * 100
        ))
        result = clean_source(source)
        self.assertNotIn("分享复制链接", result.snippet)
        self.assertNotIn("Table of Contents", result.snippet)
        self.assertLessEqual(len(result.snippet), 900)

    def test_clean_source_drops_embedded_article_navigation_without_cutting_sentences(self):
        source = Source(title="SSE", url="https://example.com", snippet=(
            "SSE 断线后应及时释放服务端连接和推理资源。让我展示一个完整实现。"
            " Table of Contents ·884 words·5 mins."
            " 浏览器会自动尝试重新连接并继续接收事件。"
        ))
        result = clean_source(source)
        self.assertIn("断线后应及时释放", result.snippet)
        self.assertIn("自动尝试重新连接", result.snippet)
        self.assertNotIn("让我展示", result.snippet)
        self.assertNotIn("884 words", result.snippet)

    def test_real_search_fragments_do_not_leak_code_or_section_labels(self):
        source = Source(title="FastAPI SSE", url="https://example.com", snippet=(
            "while True: 检查客户端是否断开连接 break ... 关注关注\n"
            "' event every 3 seconds for 4 hours then says 'Yes! '\"\"\"\n"
            ") yield ServerSentEvent(data=\"Are we there yet?\n"
            "Conclusion — ... Conclusion —\n"
            "SSE Handler: The handler adds/removes itself from the set:\n"
            "Without this, a disconnected client's queue grows indefinitely."
        ))
        self.assertEqual(clean_source(source).snippet, "Without this, a disconnected client's queue grows indefinitely.")


if __name__ == "__main__":
    unittest.main()
