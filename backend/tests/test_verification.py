import asyncio
import logging
import unittest

from src.Infrastructure.Verification import ClaimVerifier
from src.Schema.Claim import Claim
from src.Schema.Source import Source
from src.Schema.Summary import Summary
from src.Schema.Task import Task


def setUpModule() -> None:
    logging.disable(logging.CRITICAL)


def tearDownModule() -> None:
    logging.disable(logging.NOTSET)


class FakeLLM:
    def __init__(self, payloads, configured: bool = True):
        self.payloads = list(payloads)
        self.is_configured = configured
        self.calls = 0
        self.last_prompt = ""

    async def invoke_json(self, system_prompt, user_prompt, *, schema=None, temperature=0.0, span_attributes=None):
        self.calls += 1
        self.last_prompt = user_prompt
        return self.payloads.pop(0) if self.payloads else None


def build_summary(claims: list[Claim], sources: list[Source]) -> Summary:
    return Summary(task=Task(title="任务", intent="目标", query="查询"), summary="", claims=claims, sources=sources)


class ApplyVerdictsTest(unittest.TestCase):
    def test_verdicts_are_matched_by_normalized_id_and_clamped(self):
        claims = [
            Claim(id="aaaa", text="x", cited_source_ids=["s1"]),
            Claim(id="bbbb", text="y", cited_source_ids=["s1"]),
        ]

        applied = ClaimVerifier.apply_verdicts(
            claims,
            {
                "verdicts": [
                    {"claim_id": "[AAAA]", "score": 1.7, "reason": " 直接支持 "},
                    {"claim_id": "bbbb", "score": 0.2, "reason": "较弱"},
                ]
            },
        )

        self.assertEqual(applied, 2)
        self.assertEqual(claims[0].support_score, 1.0)
        self.assertEqual(claims[0].support_reason, "直接支持")
        self.assertEqual(claims[1].support_score, 0.2)

    def test_unknown_or_malformed_verdicts_are_ignored(self):
        claims = [Claim(id="aaaa", text="x", cited_source_ids=["s1"])]

        applied = ClaimVerifier.apply_verdicts(
            claims,
            {"verdicts": [{"claim_id": "zzzz", "score": 1.0}, {"claim_id": "aaaa", "score": "high"}, "nope"]},
        )

        self.assertEqual(applied, 0)
        self.assertIsNone(claims[0].support_score)

    def test_a_non_list_payload_is_ignored(self):
        claims = [Claim(id="aaaa", text="x", cited_source_ids=["s1"])]

        self.assertEqual(ClaimVerifier.apply_verdicts(claims, {"verdicts": "bad"}), 0)


class VerifySummaryTest(unittest.TestCase):
    def test_only_cited_claims_are_sent_and_scored(self):
        source = Source(title="S", url="https://example.com", snippet="正文内容")
        cited = Claim(id="c1", text="结论一", cited_source_ids=[source.id])
        uncited = Claim(id="c2", text="结论二")
        summary = build_summary([cited, uncited], [source])
        llm = FakeLLM([{"verdicts": [{"claim_id": "c1", "score": 0.8, "reason": "ok"}]}])

        result = asyncio.run(ClaimVerifier(llm).verify_summary("主题", summary))

        self.assertEqual(result.claims[0].support_score, 0.8)
        self.assertIsNone(result.claims[1].support_score)
        self.assertEqual(llm.calls, 1)
        self.assertIn("结论一", llm.last_prompt)
        self.assertNotIn("结论二", llm.last_prompt)

    def test_failed_verification_leaves_claims_unchecked_not_zero(self):
        source = Source(title="S", url="https://example.com", snippet="正文")
        claim = Claim(id="c1", text="结论", cited_source_ids=[source.id])
        llm = FakeLLM([None])

        result = asyncio.run(ClaimVerifier(llm).verify_summary("主题", build_summary([claim], [source])))

        self.assertIsNone(result.claims[0].support_score)

    def test_max_claims_caps_how_many_are_checked(self):
        source = Source(title="S", url="https://example.com", snippet="正文")
        claims = [Claim(id=f"c{i}", text=f"结论{i}", cited_source_ids=[source.id]) for i in range(5)]
        llm = FakeLLM([{"verdicts": [{"claim_id": "c0", "score": 1.0, "reason": "ok"}]}, {"verdicts": []}])

        asyncio.run(ClaimVerifier(llm, max_claims=2, batch_size=2).verify_summary("主题", build_summary(claims, [source])))

        self.assertEqual(llm.calls, 1)  # two claims, one batch

    def test_verifier_reads_the_fetched_excerpt_not_the_search_snippet(self):
        source = Source(title="S", url="https://example.com", snippet="只有一句搜索摘要")
        claim = Claim(id="c1", text="结论", cited_source_ids=[source.id])
        llm = FakeLLM([None])

        asyncio.run(
            ClaimVerifier(llm).verify_summary(
                "主题", build_summary([claim], [source]), {source.id: "这是网页正文里的一段话。"}
            )
        )

        # Judging a page-derived claim against a snippet it never came from is
        # exactly the bug that made full-page retrieval look like a regression.
        self.assertIn("这是网页正文里的一段话。", llm.last_prompt)
        self.assertNotIn("只有一句搜索摘要", llm.last_prompt)

    def test_snippet_is_used_when_no_excerpt_was_fetched(self):
        source = Source(title="S", url="https://example.com", snippet="只有一句搜索摘要")
        claim = Claim(id="c1", text="结论", cited_source_ids=[source.id])
        llm = FakeLLM([None])

        asyncio.run(ClaimVerifier(llm).verify_summary("主题", build_summary([claim], [source]), {}))

        self.assertIn("只有一句搜索摘要", llm.last_prompt)

    def test_citation_markers_are_stripped_from_the_judge_prompt(self):
        source = Source(title="S", url="https://example.com", snippet="正文")
        claim = Claim(id="c1", text="来源[2]报告的结论", cited_source_ids=[source.id])
        llm = FakeLLM([None])

        asyncio.run(ClaimVerifier(llm).verify_summary("主题", build_summary([claim], [source])))

        # Per-task numbering would contradict the excerpts shown to the judge.
        self.assertNotIn("[2]", llm.last_prompt)
        self.assertIn("来源报告的结论", llm.last_prompt)

    def test_disabled_llm_skips_verification_entirely(self):
        source = Source(title="S", url="https://example.com", snippet="正文")
        claim = Claim(id="c1", text="结论", cited_source_ids=[source.id])
        llm = FakeLLM([], configured=False)

        asyncio.run(ClaimVerifier(llm).verify_summary("主题", build_summary([claim], [source])))

        self.assertEqual(llm.calls, 0)


if __name__ == "__main__":
    unittest.main()
