"""Merge results from several search providers into one ranked pool.

Fusion is the only reason to run more than one search backend: the same page
turning up in two independent indexes is evidence that it is authoritative,
whereas chaining backends (try A, else B) only uses the second one when the
first fails.
"""

from __future__ import annotations

from urllib.parse import urlparse

from src.Schema.Source import Source


def url_key(url: str) -> str:
    """Identity used for de-duplication across providers.

    Drops the scheme and query string so tracking parameters and http/https
    variants collapse onto one entry.
    """
    parsed = urlparse(url or "")
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").rstrip("/")
    return f"{host}{path}" if host else (url or "").strip().lower()


def merge_results(groups: list[tuple[str, list[Source]]]) -> list[Source]:
    """Combine per-provider result lists, remembering who found what.

    The longest snippet wins, because providers differ in how much text they
    return and the summariser benefits from the richer one.
    """
    merged: dict[str, Source] = {}
    for provider, sources in groups:
        for source in sources:
            if not source.url:
                continue
            key = url_key(source.url)
            existing = merged.get(key)
            if existing is None:
                merged[key] = source.model_copy(update={"found_by": [provider]})
                continue

            found_by = list(existing.found_by)
            if provider not in found_by:
                found_by.append(provider)
            merged[key] = existing.model_copy(
                update={
                    "found_by": found_by,
                    "title": existing.title or source.title,
                    "snippet": source.snippet if len(source.snippet or "") > len(existing.snippet or "") else existing.snippet,
                }
            )
    return list(merged.values())


def agreement_bonus(sources: list[Source]) -> dict[str, int]:
    """How much extra rank each source earns for being found by several providers."""
    return {source.id: max(1, len(source.found_by)) for source in sources}
