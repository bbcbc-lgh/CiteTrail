import json
import re
from collections.abc import Iterable
from typing import Any

LATIN_TERM = re.compile(r"[a-z][a-z0-9+#.-]{1,}")
CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")

# Function words only. Domain vocabulary must never live here.
TERM_STOPWORDS = frozenset({
    "the", "and", "for", "with", "how", "does", "what", "why", "when", "which",
    "are", "is", "was", "were", "in", "of", "to", "a", "an", "on", "at", "by",
    "be", "that", "this", "it", "as", "from", "or", "not", "do", "can", "should",
})


def lexical_terms(text: str) -> set[str]:
    """Latin words plus CJK bigrams, minus function words.

    One shared term model for anything that needs to compare a query against a
    body of text. ``SearchClient`` keeps its own split version on purpose: it
    needs separate latin/CJK coverage ratios to handle mixed-language queries.
    """
    lowered = (text or "").lower()
    latin = {term for term in LATIN_TERM.findall(lowered) if term not in TERM_STOPWORDS}
    cjk = {run[index:index + 2] for run in CJK_RUN.findall(lowered) for index in range(len(run) - 1)}
    return latin | cjk


def term_present(term: str, lowered_text: str) -> bool:
    """Latin terms match on word boundaries; CJK terms match as substrings."""
    if term.isascii():
        return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", lowered_text) is not None
    return term in lowered_text


def parse_json_object(text: str) -> dict[str, Any] | None:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate, flags=re.IGNORECASE)
    start, end = candidate.find("{"), candidate.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        value = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def keywords(text: str, limit: int = 6) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", text.lower())
    seen: list[str] = []
    for word in words:
        if word not in seen:
            seen.append(word)
        if len(seen) == limit:
            break
    return seen


def truncate(value: str, limit: int) -> str:
    normalized = " ".join(value.split())
    return normalized if len(normalized) <= limit else f"{normalized[:limit - 1]}…"


def format_sources_for_prompt(sources: Iterable[dict[str, str]]) -> str:
    entries = []
    for index, source in enumerate(sources, start=1):
        evidence = (source.get("excerpt") or source.get("snippet") or "").strip()
        kind = "正文摘录" if source.get("excerpt") else "搜索摘要"
        entries.append(
            f"[{index}] {source.get('title', '未命名来源')}\n"
            f"链接：{source.get('url', '')}\n"
            f"来源类型：{source.get('source_type', 'article')}\n"
            f"{kind}：{evidence}\n"
            f"完整代码示例：{source.get('code', '')}"
        )
    return "\n\n".join(entries) or "未返回可用来源。"


def sse_message(event_name: str, payload: dict[str, Any]) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
