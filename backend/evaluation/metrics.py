"""Metrics for the research evaluation set.

These are **lexical proxies**, not semantic judgements. They are deliberately
cheap, deterministic and dependency-free so that a regression is visible within
minutes of a change. Anything they report as "supported" still deserves a human
spot check; the point is to catch direction, not to certify truth.

Definitions
-----------
groundedness        supported claims / *measurable* claims. Uncited and unmeasurable claims are excluded.
citation_precision  supported claims / measurable cited claims.
cited_claim_rate    cited claims / all claims.
coverage            expected points found in the report / all expected points.
citation_recall     expected points matched by a *supported claim* / all expected points.
measurable_rate     measurable claims / all claims. Low values mean the lexical check
                    could not judge most claims, not that the claims were wrong.

Support is taken from ``Claim.support_score`` when a verifier has filled it in
(semantic judge); otherwise it falls back to lexical term overlap. Semantic
scores are comparable across languages, which is why they are preferred.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

LATIN_TERM = re.compile(r"[a-z][a-z0-9+#.-]{1,}")
CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")

STOPWORDS = frozenset({
    "the", "and", "for", "with", "how", "does", "what", "why", "when", "which",
    "are", "is", "was", "were", "in", "of", "to", "a", "an", "on", "at", "by",
    "be", "that", "this", "it", "as", "from", "or", "not", "do", "can", "should",
})

# A claim is treated as backed when at least this share of its terms appears in
# its cited sources. Tuned by hand; exposed so runs can record what was used.
DEFAULT_SUPPORT_THRESHOLD = 0.35
# An expected point counts as covered when this share of its terms appears in
# the text being searched.
DEFAULT_POINT_THRESHOLD = 0.6
# A claim needs at least this many checkable terms before lexical support means
# anything. Below it, the honest answer is "cannot judge", not "not supported".
MIN_MEASURABLE_TERMS = 2
# Verifier scores are on a 0–1 scale with 0.6 meaning "mostly supported", so a
# claim needs at least a partial backing to count.
SEMANTIC_SUPPORT_THRESHOLD = 0.5


def terms_of(text: str) -> set[str]:
    """Latin words plus CJK bigrams — the same shape used by the search ranker."""
    lowered = (text or "").lower()
    latin = {term for term in LATIN_TERM.findall(lowered) if term not in STOPWORDS}
    cjk = {run[index:index + 2] for run in CJK_RUN.findall(lowered) for index in range(len(run) - 1)}
    return latin | cjk


def _present(term: str, lowered_text: str) -> bool:
    if term.isascii():
        return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", lowered_text) is not None
    return term in lowered_text


def _script(text: str) -> str:
    return "cjk" if CJK_RUN.search(text or "") else "latin"


def checkable_terms(claim_text: str, corpus: str) -> set[str]:
    """Terms that lexical matching can actually compare across the two texts.

    Research frequently answers a Chinese question from English sources. After
    translation only identifiers, product names and numbers survive verbatim, so
    for a script mismatch the check is restricted to latin tokens. A claim with
    fewer than two such anchors is reported as unmeasurable rather than as
    unsupported — otherwise every good cross-language answer would score zero.
    """
    terms = terms_of(claim_text)
    if _script(claim_text) != _script(corpus):
        terms = {term for term in terms if term.isascii()}
    return terms


def match_ratio(terms: Iterable[str], text: str) -> float:
    """Share of ``terms`` that appear in ``text``."""
    terms = set(terms)
    if not terms:
        return 0.0
    lowered = (text or "").lower()
    hits = sum(1 for term in terms if _present(term, lowered))
    return hits / len(terms)


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


@dataclass
class CaseMetrics:
    claims: int = 0
    cited_claims: int = 0
    measurable_claims: int = 0
    semantic_claims: int = 0
    supported_claims: int = 0
    groundedness: float = 0.0
    citation_precision: float = 0.0
    cited_claim_rate: float = 0.0
    measurable_rate: float = 0.0
    coverage: float = 0.0
    citation_recall: float = 0.0
    matched_points: list[str] = field(default_factory=list)
    missing_points: list[str] = field(default_factory=list)
    claim_details: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "claims": self.claims,
            "cited_claims": self.cited_claims,
            "measurable_claims": self.measurable_claims,
            "semantic_claims": self.semantic_claims,
            "supported_claims": self.supported_claims,
            "groundedness": self.groundedness,
            "citation_precision": self.citation_precision,
            "cited_claim_rate": self.cited_claim_rate,
            "measurable_rate": self.measurable_rate,
            "coverage": self.coverage,
            "citation_recall": self.citation_recall,
            "matched_points": self.matched_points,
            "missing_points": self.missing_points,
            "claim_details": self.claim_details,
        }


def evaluate(
    report: str,
    summaries: Iterable[Any],
    expected_points: list[str],
    support_threshold: float = DEFAULT_SUPPORT_THRESHOLD,
    point_threshold: float = DEFAULT_POINT_THRESHOLD,
    semantic_threshold: float = SEMANTIC_SUPPORT_THRESHOLD,
) -> CaseMetrics:
    """Score one research run against its expected points."""
    details: list[dict[str, Any]] = []
    for summary in summaries:
        by_id = {source.id: source for source in summary.sources}
        for claim in summary.claims:
            cited = [by_id[source_id] for source_id in claim.cited_source_ids if source_id in by_id]
            corpus = " ".join(f"{source.title} {source.snippet}" for source in cited) if cited else ""

            if claim.support_score is not None:
                support = max(0.0, min(1.0, float(claim.support_score)))
                measurable = True
                method = "semantic"
                threshold = semantic_threshold
            else:
                terms = checkable_terms(claim.text, corpus) if cited else set()
                measurable = len(terms) >= MIN_MEASURABLE_TERMS
                support = match_ratio(terms, corpus) if measurable else None
                method = "lexical"
                threshold = support_threshold

            details.append(
                {
                    "text": claim.text,
                    "cited": bool(cited),
                    "measurable": measurable,
                    "method": method,
                    "support": round(support, 4) if support is not None else None,
                    "supported": bool(measurable and support is not None and support >= threshold),
                }
            )

    total_claims = len(details)
    cited_claims = sum(1 for item in details if item["cited"])
    measurable_claims = sum(1 for item in details if item["measurable"])
    semantic_claims = sum(1 for item in details if item["method"] == "semantic")
    measurable_cited = sum(1 for item in details if item["measurable"] and item["cited"])
    supported_claims = sum(1 for item in details if item["supported"])

    matched: list[str] = []
    attributed: list[str] = []
    missing: list[str] = []
    for point in expected_points:
        point_terms = terms_of(point)
        if match_ratio(point_terms, report) >= point_threshold:
            matched.append(point)
        else:
            missing.append(point)
        if any(
            item["supported"] and match_ratio(point_terms, item["text"]) >= point_threshold
            for item in details
        ):
            attributed.append(point)

    return CaseMetrics(
        claims=total_claims,
        cited_claims=cited_claims,
        measurable_claims=measurable_claims,
        semantic_claims=semantic_claims,
        supported_claims=supported_claims,
        groundedness=_ratio(supported_claims, measurable_claims),
        citation_precision=_ratio(supported_claims, measurable_cited),
        cited_claim_rate=_ratio(cited_claims, total_claims),
        measurable_rate=_ratio(measurable_claims, total_claims),
        coverage=_ratio(len(matched), len(expected_points)),
        citation_recall=_ratio(len(attributed), len(expected_points)),
        matched_points=matched,
        missing_points=missing,
        claim_details=details,
    )


MEAN_FIELDS = (
    "groundedness",
    "citation_precision",
    "cited_claim_rate",
    "measurable_rate",
    "coverage",
    "citation_recall",
)


def aggregate(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Average the ratio metrics over runs that a model actually produced.

    Degraded runs are excluded on purpose: the deterministic fallback builds
    claims straight from snippets, so it always cites them and would score a
    perfect groundedness while telling us nothing about model quality.
    """
    rows = list(rows)
    degraded = sum(1 for row in rows if row.get("degraded"))
    usable = [
        row
        for row in rows
        if row.get("error") is None and row.get("report_chars") and not row.get("degraded")
    ]
    if not usable:
        return {
            "cases": 0,
            "degraded_excluded": degraded,
            "failed": sum(1 for row in rows if row.get("error")),
            **{name: 0.0 for name in MEAN_FIELDS},
        }
    return {
        "cases": len(usable),
        "degraded_excluded": degraded,
        "failed": sum(1 for row in rows if row.get("error")),
        **{
            name: round(sum(row["metrics"].get(name, 0.0) for row in usable) / len(usable), 4)
            for name in MEAN_FIELDS
        },
        "total_prompt_tokens": sum(row.get("tokens", {}).get("prompt_tokens", 0) for row in usable),
        "total_completion_tokens": sum(row.get("tokens", {}).get("completion_tokens", 0) for row in usable),
        "mean_duration_s": round(sum(row.get("duration_s", 0) for row in usable) / len(usable), 1),
    }
