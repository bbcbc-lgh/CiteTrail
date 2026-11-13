"""Decide whether a task's evidence is good enough to stop.

The check is deliberately deterministic and cheap: no extra model call just to
ask "should I keep going?". The model only gets involved when a follow-up is
already justified, to propose *what* to search next.

Being conservative matters here. Every extra round costs a search, a fetch, a
summarise and a verification, so the loop should only fire when the evidence is
plainly thin, not merely imperfect.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.Schema.Source import Source
from src.Schema.Summary import Summary

# Below this many claims the summary is too thin to answer a sub-question.
MIN_CLAIMS = 3
# Below this many usable sources the evidence base is too narrow, even if the
# model managed to phrase a few claims from it.
MIN_SOURCES = 2
# If fewer than this share of verified claims are supported, the evidence is bad.
MIN_SUPPORT_RATE = 0.5


@dataclass(frozen=True)
class ReflectionDecision:
    needed: bool
    reason: str


def assess(
    summary: Summary,
    sources: list[Source],
    min_claims: int = MIN_CLAIMS,
    min_sources: int = MIN_SOURCES,
    min_support_rate: float = MIN_SUPPORT_RATE,
) -> ReflectionDecision:
    """Return whether another retrieval round is worth its cost."""
    usable = [source for source in sources if source.url]
    if not usable:
        return ReflectionDecision(True, "没有可用来源")
    if len(usable) < min_sources:
        # One source can still yield claims, but not a corroborated answer.
        return ReflectionDecision(True, f"可用来源过少（{len(usable)} 条）")
    if not summary.claims:
        return ReflectionDecision(True, "没有形成任何结论")
    if len(summary.claims) < min_claims:
        return ReflectionDecision(True, f"结论过少（{len(summary.claims)} 条）")

    verified = [claim for claim in summary.claims if claim.support_score is not None]
    if verified:
        supported = sum(1 for claim in verified if claim.support_score >= min_support_rate)
        if supported / len(verified) < min_support_rate:
            return ReflectionDecision(True, f"多数结论缺乏支撑（{supported}/{len(verified)}）")

    return ReflectionDecision(False, "")
