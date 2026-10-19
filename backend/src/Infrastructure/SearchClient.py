from urllib.parse import urlparse
import re

import httpx

from src.Infrastructure.Config import Settings
from src.Infrastructure.Tools import truncate
from src.Infrastructure.Evidence import clean_source
from src.Schema.Source import Source


class SearchClient:
    """Search provider adapter with deterministic fallback ordering."""

    FALLBACK_BACKENDS = ("tavily", "perplexity", "duckduckgo", "searxng")

    def __init__(self, settings: Settings):
        self.settings = settings

    async def search(self, query: str, topic: str | None = None) -> list[Source]:
        errors: list[str] = []
        for backend in self.backends():
            try:
                results = await self._search_with(backend, query)
                if results:
                    normalized = self._normalize(results, backend)
                    # Rank against the actual search query. The planner may translate
                    # a Chinese topic into English terms before searching.
                    relevant = self._rank_relevant(normalized, query)
                    if relevant:
                        return relevant[:self.settings.search_result_limit]
            except Exception as exc:
                errors.append(f"{backend}: {exc}")
        return [
            Source(
                title="未获得搜索结果",
                url="",
                snippet="请检查网络连接或搜索服务配置后重试。",
                provider="fallback",
            )
        ]

    def backends(self) -> tuple[str, ...]:
        preferred = self.settings.search_api.lower().strip()
        if preferred in self.FALLBACK_BACKENDS:
            return (preferred,) + tuple(name for name in self.FALLBACK_BACKENDS if name != preferred)
        return self.FALLBACK_BACKENDS

    async def _search_with(self, backend: str, query: str) -> list[Source]:
        if backend == "tavily" and self.settings.tavily_api_key:
            return await self._tavily(query)
        if backend == "perplexity" and self.settings.perplexity_api_key:
            return await self._perplexity(query)
        if backend == "duckduckgo":
            return await self._duckduckgo(query)
        if backend == "searxng" and self.settings.searxng_url:
            return await self._searxng(query)
        return []

    async def _tavily(self, query: str) -> list[Source]:
        async with httpx.AsyncClient(timeout=self.settings.search_timeout) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={"api_key": self.settings.tavily_api_key, "query": query, "max_results": self.settings.search_result_limit},
            )
            response.raise_for_status()
            payload = response.json()
        return [
            Source(title=item.get("title", "未命名结果"), url=item.get("url", ""), snippet=item.get("content", ""), provider="tavily")
            for item in payload.get("results", []) or []
        ]

    async def _perplexity(self, query: str) -> list[Source]:
        async with httpx.AsyncClient(timeout=self.settings.search_timeout) as client:
            response = await client.post(
                "https://api.perplexity.ai/v1/sonar",
                headers={"Authorization": f"Bearer {self.settings.perplexity_api_key}"},
                json={"model": "sonar", "messages": [{"role": "user", "content": query}]},
            )
            response.raise_for_status()
            payload = response.json()

        results = [
            Source(
                title=item.get("title", "未命名结果"),
                url=item.get("url", ""),
                snippet=item.get("snippet", ""),
                provider="perplexity",
            )
            for item in payload.get("search_results", []) or []
            if item.get("url")
        ]
        if results:
            return results
        return [
            Source(title=urlparse(url).netloc or url, url=url, provider="perplexity")
            for url in payload.get("citations", []) or []
            if isinstance(url, str) and url
        ]

    async def _duckduckgo(self, query: str) -> list[Source]:
        async with httpx.AsyncClient(timeout=self.settings.search_timeout, follow_redirects=True) as client:
            response = await client.get(
                "https://api.duckduckgo.com/",
                params={"q": query, "format": "json", "no_html": 1, "skip_disambig": 1},
                headers={"User-Agent": "CiteTrail/0.1"},
            )
            response.raise_for_status()
            payload = response.json()
        results: list[Source] = []
        if payload.get("AbstractURL"):
            results.append(Source(title=payload.get("Heading") or query, url=payload["AbstractURL"], snippet=payload.get("AbstractText", ""), provider="duckduckgo"))
        for item in self._flatten_related(payload.get("RelatedTopics", [])):
            url, text = item.get("FirstURL", ""), item.get("Text", "")
            if url and text:
                results.append(Source(title=text.split(" - ", 1)[0], url=url, snippet=text, provider="duckduckgo"))
            if len(results) >= self.settings.search_result_limit:
                break
        return results

    async def _searxng(self, query: str) -> list[Source]:
        endpoint = self.settings.searxng_url.rstrip("/") + "/search"
        async with httpx.AsyncClient(timeout=self.settings.search_timeout) as client:
            response = await client.get(endpoint, params={"q": query, "format": "json", "language": "auto"})
            response.raise_for_status()
            payload = response.json()
        return [
            Source(title=item.get("title", "未命名结果"), url=item.get("url", ""), snippet=item.get("content", ""), provider="searxng")
            for item in payload.get("results", []) or []
        ]

    @staticmethod
    def _flatten_related(items: list[object]) -> list[dict[str, str]]:
        flattened: list[dict[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if "Topics" in item and isinstance(item["Topics"], list):
                flattened.extend(SearchClient._flatten_related(item["Topics"]))
            else:
                flattened.append(item)
        return flattened

    def _normalize(self, sources: list[Source], provider: str) -> list[Source]:
        seen: set[str] = set()
        normalized: list[Source] = []
        for source in sources:
            key = source.url.rstrip("/") or source.title.lower()
            if key in seen:
                continue
            seen.add(key)
            title = truncate(source.title or urlparse(source.url).netloc or "未命名结果", 160)
            if urlparse(source.url).scheme not in {"http", "https"}:
                continue
            normalized.append(clean_source(source.model_copy(update={"title": title, "provider": source.provider or provider})))
        return normalized

    @staticmethod
    def _rank_relevant(sources: list[Source], query: str) -> list[Source]:
        stopwords = {"the", "and", "for", "with", "how", "does", "what", "are", "is", "in", "of", "to", "a", "an", "on", "best", "practices", "practice", "用户体验", "用户验收"}
        terms = set(re.findall(r"[a-z][a-z0-9+#.-]{1,}", query.lower())) - stopwords
        chinese = set()
        for phrase in re.findall(r"[\u4e00-\u9fff]+", query):
            if phrase not in stopwords:
                chinese.update(phrase[index:index + 2] for index in range(len(phrase) - 1))
        scored = []
        for source in sources:
            text = f"{source.title} {source.snippet} {source.url}".lower()
            latin_hits = sum(bool(re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text)) for term in terms)
            chinese_hits = sum(term in text for term in chinese)
            required_latin_hits = max(2, (len(terms) + 2) // 3)
            if terms and latin_hits < required_latin_hits:
                continue
            if "fastapi" in terms and not re.search(r"(?<![a-z0-9])fastapi(?![a-z0-9])", text):
                continue
            if "sse" in terms and not re.search(r"(?<![a-z0-9])sse(?![a-z0-9])", text):
                continue
            if not terms and chinese and chinese_hits < max(1, len(chinese) // 4):
                continue
            if not terms and not chinese:
                continue
            # Mixed-language technical questions also need their intent, not just framework names.
            intent = next((word for word in ("断线", "断连", "取消", "重连") if word in query), None)
            if intent and not any(word in text for word in (intent, "disconnect", "cancel", "reconnect", "断开", "断连", "断线", "重连")):
                continue
            score = latin_hits + chinese_hits + (2 if source.source_type == "official" else -1 if source.source_type == "community" else 0)
            scored.append((score, source))
        return [source for _, source in sorted(scored, key=lambda item: item[0], reverse=True)]
