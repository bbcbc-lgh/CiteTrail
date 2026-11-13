import unittest

from evaluation.metrics import aggregate, evaluate, match_ratio, terms_of
from src.Schema.Claim import Claim
from src.Schema.Source import Source
from src.Schema.Summary import Summary
from src.Schema.Task import Task


def build_summary(claims: list[Claim], sources: list[Source]) -> Summary:
    return Summary(task=Task(title="T", intent="I", query="Q"), summary="", claims=claims, sources=sources)


class TermsTest(unittest.TestCase):
    def test_latin_terms_keep_hyphenated_identifiers(self):
        self.assertIn("last-event-id", terms_of("Last-Event-ID"))
        self.assertIn("hnsw", terms_of("HNSW index"))

    def test_cjk_is_split_into_bigrams_not_single_chars(self):
        terms = terms_of("事务隔离")

        self.assertEqual(terms, {"事务", "务隔", "隔离"})

    def test_stopwords_are_ignored(self):
        self.assertEqual(terms_of("what is the best practice"), {"best", "practice"})

    def test_match_ratio_is_zero_for_empty_terms(self):
        self.assertEqual(match_ratio(set(), "anything"), 0.0)


class EvaluateTest(unittest.TestCase):
    def test_groundedness_and_precision_come_from_citation_support(self):
        source = Source(title="远程办公研究", url="https://example.com/a", snippet="远程办公 降低了 沟通成本")
        supported = Claim(text="远程办公 降低了 沟通成本", cited_source_ids=[source.id])
        uncited = Claim(text="知识共享 反而 提升")

        metrics = evaluate(
            "报告讨论 远程办公 与 沟通成本。",
            [build_summary([supported, uncited], [source])],
            ["沟通成本", "知识共享"],
        )

        self.assertEqual(metrics.claims, 2)
        self.assertEqual(metrics.cited_claims, 1)
        self.assertEqual(metrics.measurable_claims, 1)
        self.assertEqual(metrics.supported_claims, 1)
        # The uncited claim has nothing to check against, so it is reported
        # through cited_claim_rate rather than diluting groundedness.
        self.assertEqual(metrics.groundedness, 1.0)
        self.assertEqual(metrics.citation_precision, 1.0)
        self.assertEqual(metrics.cited_claim_rate, 0.5)
        self.assertEqual(metrics.measurable_rate, 0.5)
        self.assertEqual(metrics.coverage, 0.5)
        self.assertEqual(metrics.matched_points, ["沟通成本"])
        self.assertEqual(metrics.missing_points, ["知识共享"])

    def test_citation_to_an_unknown_source_counts_as_uncited(self):
        metrics = evaluate(
            "报告",
            [build_summary([Claim(text="某结论", cited_source_ids=["deadbeef"])], [])],
            [],
        )

        self.assertEqual(metrics.cited_claims, 0)
        self.assertEqual(metrics.groundedness, 0.0)

    def test_citation_recall_requires_the_matching_claim_to_be_supported(self):
        source = Source(title="a", url="https://example.com/a", snippet="完全不同 的 内容")
        claim = Claim(text="能耗 成本 很高", cited_source_ids=[source.id])

        metrics = evaluate("能耗 成本 很高", [build_summary([claim], [source])], ["能耗 成本"])

        self.assertEqual(metrics.coverage, 1.0)
        # The claim covers the point but its cited source does not support it.
        self.assertEqual(metrics.citation_recall, 0.0)


class CrossLanguageTest(unittest.TestCase):
    def test_cross_language_claim_is_checked_on_identifiers_only(self):
        source = Source(
            title="EventSource reconnect",
            url="https://example.com/sse",
            snippet="The EventSource object reconnects automatically using retry.",
        )
        claim = Claim(text="EventSource 会自动重连，并通过 retry 调整间隔", cited_source_ids=[source.id])

        metrics = evaluate("", [build_summary([claim], [source])], [])

        self.assertEqual(metrics.measurable_claims, 1)
        self.assertEqual(metrics.supported_claims, 1)
        self.assertEqual(metrics.groundedness, 1.0)

    def test_cross_language_claim_without_anchors_is_unmeasurable_not_unsupported(self):
        source = Source(
            title="Remote collaboration study",
            url="https://example.com/r",
            snippet="Communication and knowledge sharing suffer when everyone works remotely.",
        )
        claim = Claim(text="远程办公会降低知识共享的频率", cited_source_ids=[source.id])

        metrics = evaluate("", [build_summary([claim], [source])], [])

        self.assertEqual(metrics.measurable_claims, 0)
        self.assertEqual(metrics.groundedness, 0.0)
        self.assertEqual(metrics.measurable_rate, 0.0)

    def test_unmeasurable_claims_do_not_dilute_the_denominator(self):
        source = Source(title="SSE", url="https://example.com/a", snippet="EventSource retry reconnects automatically.")
        good = Claim(text="EventSource 通过 retry 自动重连", cited_source_ids=[source.id])
        unmeasurable = Claim(text="团队信任会逐渐下降", cited_source_ids=[source.id])

        metrics = evaluate("", [build_summary([good, unmeasurable], [source])], [])

        self.assertEqual(metrics.claims, 2)
        self.assertEqual(metrics.measurable_claims, 1)
        self.assertEqual(metrics.groundedness, 1.0)


class SemanticScoreTest(unittest.TestCase):
    def test_verifier_scores_make_cross_language_claims_measurable(self):
        source = Source(title="Study", url="https://example.com", snippet="Completely unrelated English text.")
        claim = Claim(text="远程办公降低协作效率", cited_source_ids=[source.id], support_score=0.9)

        metrics = evaluate("", [build_summary([claim], [source])], [])

        self.assertEqual(metrics.measurable_rate, 1.0)
        self.assertEqual(metrics.semantic_claims, 1)
        self.assertEqual(metrics.groundedness, 1.0)

    def test_semantic_score_is_used_over_lexical_overlap(self):
        source = Source(title="Study", url="https://example.com", snippet="远程办公 降低 协作效率")
        claim = Claim(text="远程办公降低协作效率", cited_source_ids=[source.id], support_score=0.1)

        metrics = evaluate("", [build_summary([claim], [source])], [])

        # Lexical overlap would be near perfect, but the judge said no.
        self.assertEqual(metrics.supported_claims, 0)
        self.assertEqual(metrics.claim_details[0]["method"], "semantic")

    def test_without_a_verifier_lexical_is_the_fallback(self):
        source = Source(title="Study", url="https://example.com", snippet="远程办公 降低 协作效率")
        claim = Claim(text="远程办公降低协作效率", cited_source_ids=[source.id])

        metrics = evaluate("", [build_summary([claim], [source])], [])

        self.assertEqual(metrics.claim_details[0]["method"], "lexical")
        self.assertEqual(metrics.semantic_claims, 0)


class AggregateTest(unittest.TestCase):
    def test_degraded_and_failed_runs_are_excluded_from_the_average(self):
        rows = [
            {"error": None, "report_chars": 10, "degraded": False, "metrics": {"groundedness": 1.0, "citation_precision": 1.0, "cited_claim_rate": 1.0, "coverage": 1.0, "citation_recall": 1.0}, "tokens": {"prompt_tokens": 5, "completion_tokens": 1}, "duration_s": 2.0},
            {"error": None, "report_chars": 10, "degraded": True, "metrics": {"groundedness": 0.0, "citation_precision": 0.0, "cited_claim_rate": 0.0, "coverage": 0.0, "citation_recall": 0.0}, "tokens": {"prompt_tokens": 5, "completion_tokens": 1}, "duration_s": 4.0},
            {"error": "boom", "report_chars": 0, "degraded": False, "metrics": {}, "tokens": {}, "duration_s": 0},
        ]

        summary = aggregate(rows)

        self.assertEqual(summary["cases"], 1)
        self.assertEqual(summary["degraded_excluded"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["coverage"], 1.0)
        self.assertEqual(summary["total_prompt_tokens"], 5)

    def test_all_degraded_yields_no_usable_cases(self):
        rows = [{"error": None, "report_chars": 10, "degraded": True, "metrics": {"groundedness": 1.0, "citation_precision": 1.0, "cited_claim_rate": 1.0, "coverage": 1.0, "citation_recall": 1.0}, "tokens": {}, "duration_s": 1.0}]

        summary = aggregate(rows)

        self.assertEqual(summary["cases"], 0)
        self.assertEqual(summary["degraded_excluded"], 1)

    def test_aggregate_of_nothing_is_all_zero(self):
        self.assertEqual(aggregate([])["cases"], 0)


if __name__ == "__main__":
    unittest.main()
