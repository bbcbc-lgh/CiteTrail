import json
import re
from collections.abc import Iterable
from typing import Any


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
        entries.append(
            f"[{index}] {source.get('title', '未命名来源')}\n"
            f"链接：{source.get('url', '')}\n"
            f"来源类型：{source.get('source_type', 'article')}\n"
            f"摘要：{source.get('snippet', '')}\n"
            f"完整代码示例：{source.get('code', '')}"
        )
    return "\n\n".join(entries) or "未返回可用来源。"


def sse_message(event_name: str, payload: dict[str, Any]) -> str:
    return f"event: {event_name}\ndata: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
