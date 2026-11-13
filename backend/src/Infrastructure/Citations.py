"""Report-wide citation numbering shared by the model and fallback paths."""

import re
from collections.abc import Iterable

from src.Schema.Citation import Citation
from src.Schema.Summary import Summary


def renumber_markers(text: str, mapping: dict[int, int]) -> str:
    """Rewrite ``[n]`` markers from per-task numbering to global numbering.

    Claims are written against the numbering of their own task, but the report
    numbers every source once. Leaving the original numbers in place makes a
    claim cite ``[2]`` while pointing at a completely different source. Numbers
    that cannot be mapped are dropped rather than left dangling.

    Passing an empty mapping strips all markers.
    """
    def replace(match: re.Match[str]) -> str:
        target = mapping.get(int(match.group(1)))
        return f"[{target}]" if target else ""

    return re.sub(r"\[(\d+)\]", replace, text)


def build_citation_index(summaries: Iterable[Summary]) -> list[Citation]:
    """Number every distinct source once, in first-appearance order.

    Both the model prompt and the deterministic fallback report use this, so a
    claim's ``[n]`` means the same thing in either path.
    """
    ordered: list[Citation] = []
    by_source: dict[str, Citation] = {}
    for summary in summaries:
        for source in summary.sources:
            if not source.url:
                continue
            citation = by_source.get(source.id)
            if citation is None:
                citation = Citation(index=len(ordered) + 1, source_id=source.id, title=source.title, url=source.url)
                by_source[source.id] = citation
                ordered.append(citation)
            for claim in summary.claims:
                if source.id in claim.cited_source_ids and claim.id not in citation.claim_ids:
                    citation.claim_ids.append(claim.id)
    return ordered
