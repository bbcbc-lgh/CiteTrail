"""Split page text into retrievable chunks and pick the ones worth sending.

The old pipeline handed the summariser a 700-character search snippet. A real
page is 10–50x that, so the job here is to keep the answer-bearing parts and
drop the rest — feeding a whole page would blow the prompt budget and bury the
relevant paragraph.
"""

from __future__ import annotations

import math
import re
from uuid import uuid4

from src.Infrastructure.Tools import lexical_terms, term_present
from src.Schema.Chunk import Chunk

SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？!?])\s*|(?<=\.)\s+|\n+")

DEFAULT_CHUNK_SIZE = 700
DEFAULT_OVERLAP = 80


def split_text(text: str, size: int = DEFAULT_CHUNK_SIZE, overlap: int = DEFAULT_OVERLAP) -> list[str]:
    """Pack sentences into chunks of at most ``size`` characters."""
    normalized = re.sub(r"[ \t]+", " ", (text or "")).strip()
    if not normalized:
        return []
    if len(normalized) <= size:
        return [normalized]

    sentences: list[str] = []
    for raw in SENTENCE_BOUNDARY.split(normalized):
        sentence = (raw or "").strip()
        if not sentence:
            continue
        if len(sentence) <= size:
            sentences.append(sentence)
        else:
            # A single oversized sentence (tables, unbroken text) still has to fit.
            sentences.extend(sentence[start:start + size] for start in range(0, len(sentence), size))

    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if not current:
            current = sentence
            continue
        if len(current) + 1 + len(sentence) <= size:
            current = f"{current} {sentence}"
            continue
        chunks.append(current)
        tail = current[-overlap:] if overlap > 0 else ""
        current = f"{tail} {sentence}".strip() if tail else sentence
    if current:
        chunks.append(current)
    return chunks


def select_chunks(query: str, texts: list[str], limit: int) -> list[int]:
    """Return indices of the ``limit`` chunks that best match ``query``.

    Selection keeps document order so the model reads the extract in the order
    the author wrote it, not in score order.
    """
    if not texts or limit <= 0:
        return []
    if limit >= len(texts):
        return list(range(len(texts)))

    terms = lexical_terms(query)
    if not terms:
        return list(range(limit))

    scored: list[tuple[float, int]] = []
    for index, text in enumerate(texts):
        lowered = text.lower()
        hits = sum(1 for term in terms if term_present(term, lowered))
        if not hits:
            continue
        # Normalise by chunk length so a long chunk cannot win on sheer size.
        scored.append((hits / math.sqrt(max(len(text), 1)), index))
    if not scored:
        return list(range(limit))

    scored.sort(key=lambda item: (-item[0], item[1]))
    chosen = sorted(index for _, index in scored[:limit])
    return chosen


def build_chunks(document_id: str, texts: list[str], selected: list[int]) -> list[Chunk]:
    """Materialise the selected chunks against their document, in reading order."""
    return [
        Chunk(id=uuid4().hex[:12], document_id=document_id, ordinal=ordinal, text=texts[ordinal])
        for ordinal in selected
    ]
