import asyncio
import logging
import math
import re
from urllib.parse import urlparse

import httpx

from src.Infrastructure.Config import Settings
from src.Infrastructure.Evidence import clean_source
from src.Infrastructure.Fusion import agreement_bonus, merge_results
from src.Infrastructure.Observability import span
from src.Infrastructure.Tools import term_present, truncate
from src.Schema.Source import Source

logger = logging.getLogger(__name__)


class SearchClient:
    """Search across several providers.

    Two modes, chosen by ``SEARCH_MODE``:

    ``fusion`` (default)
        Query every configured domestic provider in parallel, merge the results
        by URL, and rank the merged pool. A page returned by two independent
        indexes is a stronger source than one returned by a single index, and
        that agreement is fed into the ranking.
    ``fallback``
        Try providers in order until one answers. Kept for low-quota setups.

    Either way, if the domestic providers yield nothing, the chain falls back to
    ``SEARCH_API`` (Tavily by default), so a total outage degrades rather than
    fails.
    """

    KNOWN_BACKENDS = ("bocha", "zhipu", "baidu", "deepseek", "metaso", "tavily")

    def __init__(self, settings: Settings):
        self.settings = settings
        # Backends that failed in a way retrying will not fix (bad key, dead
        # service). Remembered for this client only, i.e. for one research run.
        self._unavailable: dict[str, str] = {}

    def _client(self, **kwargs) -> httpx.AsyncClient:
        """Build a client that gives up quickly on unreachable hosts.

        A stale ``127.0.0.1`` service used to burn the full read timeout on
        every query. Connect timeouts are short; reads keep the configured value.
        """
        timeout = httpx.Timeout(self.settings.search_timeout, connect=min(3.0, self.settings.search_timeout))
        return httpx.AsyncClient(timeout=timeout, **kwargs)

    def available_backends(self) -> list[dict[str, object]]:
        """Which backends are configured, for ``/health``."""
        return [
            {
                "name": name,
                "configured": self._configured(name),
                "role": "fusion" if name in self.settings.fusion_backend_list else "fallback",
            }
            for name in self.KNOWN_BACKENDS
        ]

    def _configured(self, backend: str) -> bool:
        configured = {
            "bocha": bool(self.settings.bocha_api_key),
            "zhipu": bool(self.settings.zhipu_api_key),
            "baidu": bool(self.settings.baidu_api_key),
            "deepseek": bool(self.settings.deepseek_api_key),
            "metaso": bool(self.settings.metaso_api_key),
            "tavily": bool(self.settings.tavily_api_key),
        }
        return configured.get(backend, False)

    def backends(self) -> tuple[str, ...]:
        preferred = self.settings.search_api.lower().strip()
        if preferred in self.KNOWN_BACKENDS:
            return (preferred,) + tuple(name for name in self.KNOWN_BACKENDS if name != preferred)
        return self.KNOWN_BACKENDS

    async def search(self, query: str, topic: str | None = None) -> list[Source]:
        with span(
            "retrieval",
            **{"retrieval.query": query, "retrieval.mode": self.settings.search_mode},
        ) as retrieval_span:
            if self.settings.search_mode == "fusion":
                fused = await self._fused(query, retrieval_span)
                if fused:
                    return fused
                retrieval_span.set("retrieval.fusion_empty", True)
                logger.warning("融合检索未产出可用结果，降级到回退链 | query=%r", query)
            return await self._fallback(query, retrieval_span)

    async def _fused(self, query: str, retrieval_span) -> list[Source]:
        names = [
            name
            for name in self.settings.fusion_backend_list
            if name in self.KNOWN_BACKENDS and self._configured(name) and name not in self._unavailable
        ]
        if not names:
            return []

        outcomes = await asyncio.gather(*(self._one(query, name) for name in names))
        groups = [
            (name, self._normalize(outcome, name))
            for name, outcome in zip(names, outcomes)
            if outcome
        ]
        groups = [(name, sources) for name, sources in groups if sources]
        if not groups:
            return []

        merged = merge_results(groups)
        ranked = self._rank_relevant(merged, query, agreement=agreement_bonus(merged))
        retrieval_span.set_many(
            {
                "retrieval.provider": "fusion",
                "retrieval.backends_tried": names,
                "retrieval.per_provider": {name: len(outcome) for name, outcome in zip(names, outcomes) if outcome},
                "retrieval.merged": len(merged),
                "retrieval.agreed": sum(1 for source in merged if len(source.found_by) > 1),
                "retrieval.kept": len(ranked),
            }
        )
        return ranked[: self.settings.search_result_limit]

    async def _one(self, query: str, backend: str) -> list[Source]:
        """Run one provider, recording failures without breaking the join."""
        if backend in self._unavailable:
            return []
        try:
            with span("search_backend", **{"retrieval.backend": backend}) as backend_span:
                results = await self._search_with(backend, query)
                backend_span.set("raw_results", len(results))
                return results
        except Exception as exc:
            logger.warning("搜索后端不可用 | provider=%s query=%r error=%s: %s", backend, query, type(exc).__name__, exc)
            if self._is_permanent_failure(exc):
                self._unavailable[backend] = type(exc).__name__
                logger.warning("本次研究不再尝试该后端 | provider=%s error=%s", backend, type(exc).__name__)
            return []

    async def _fallback(self, query: str, retrieval_span) -> list[Source]:
        errors: list[str] = []
        attempted: list[str] = []
        for backend in self.backends():
            attempted.append(backend)
            if backend in self._unavailable:
                errors.append(f"{backend}: 本轮已跳过（{self._unavailable[backend]}）")
                continue
            try:
                results = await self._one(query, backend)
                if not results:
                    errors.append(f"{backend}: 无结果")
                    continue
                normalized = self._normalize(results, backend)
                # Rank against the actual search query. The planner may
                # translate a Chinese topic into English before searching.
                relevant = self._rank_relevant(normalized, query)
                if relevant:
                    retrieval_span.set_many(
                        {
                            "retrieval.provider": backend,
                            "retrieval.backends_tried": attempted,
                            "retrieval.raw": len(results),
                            "retrieval.kept": len(relevant),
                        }
                    )
                    return relevant[: self.settings.search_result_limit]
                logger.info("搜索命中但全部被判为不相关 | provider=%s query=%r 原始=%d", backend, query, len(normalized))
                errors.append(f"{backend}: 命中但全部不相关")
            except Exception as exc:  # _one already swallows; kept for safety
                errors.append(f"{backend}: {exc}")
        logger.warning("所有搜索后端均未返回可用结果 | query=%r errors=%s", query, errors or "无错误但无结果")
        retrieval_span.set_many(
            {
                "retrieval.backends_tried": attempted,
                "retrieval.kept": 0,
                "retrieval.errors": errors,
            }
        )
        return [
            Source(
                title="未获得搜索结果",
                url="",
                snippet="请检查网络连接或搜索服务配置后重试。",
                provider="fallback",
            )
        ]

    @staticmethod
    def _is_permanent_failure(exc: Exception) -> bool:
        """True when retrying this backend in the same run cannot help.

        A bad key or a dead local service will fail identically for every query,
        so retrying it per task only wastes time and log lines.
        """
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None) or getattr(exc, "status_code", None)
        if status in {401, 403, 404} or (status is not None and status >= 500):
            return True
        return isinstance(exc, httpx.ConnectError)

    async def _search_with(self, backend: str, query: str) -> list[Source]:
        if backend == "bocha" and self.settings.bocha_api_key:
            return await self._bocha(query)
        if backend == "zhipu" and self.settings.zhipu_api_key:
            return await self._zhipu(query)
        if backend == "baidu" and self.settings.baidu_api_key:
            return await self._baidu(query)
        if backend == "deepseek" and self.settings.deepseek_api_key:
            return await self._deepseek(query)
        if backend == "metaso" and self.settings.metaso_api_key:
            return await self._metaso(query)
        if backend == "tavily" and self.settings.tavily_api_key:
            return await self._tavily(query)
        return []

    async def _tavily(self, query: str) -> list[Source]:
        async with self._client() as client:
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

    async def _bocha(self, query: str) -> list[Source]:
        """博查 AI 搜索：面向中文与 RAG 场景的国内搜索 API。"""
        async with self._client() as client:
            response = await client.post(
                "https://api.bochaai.com/v1/web-search",
                headers={"Authorization": f"Bearer {self.settings.bocha_api_key}"},
                json={
                    "query": query,
                    "summary": True,
                    "count": self.settings.search_result_limit,
                    "freshness": "noLimit",
                },
            )
            response.raise_for_status()
            payload = response.json()
        return [
            Source(
                title=str(item.get("name") or item.get("title") or "未命名结果"),
                url=str(item.get("url") or ""),
                # `summary` is the longer model-friendly extract; `snippet` is the
                # one-line version. Prefer the longer one when summary=true.
                snippet=str(item.get("summary") or item.get("snippet") or ""),
                provider="bocha",
            )
            for item in self._bocha_items(payload)
            if item.get("url")
        ]

    async def _zhipu(self, query: str) -> list[Source]:
        """智谱联网搜索：与 GLM 同平台的 Web Search API。"""
        async with self._client() as client:
            response = await client.post(
                "https://open.bigmodel.cn/api/paas/v4/web_search",
                headers={"Authorization": f"Bearer {self.settings.zhipu_api_key}"},
                json={
                    "search_engine": "search_std",
                    "search_query": query,
                    "count": self.settings.search_result_limit,
                },
            )
            response.raise_for_status()
            payload = response.json()
        items = payload.get("search_result") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            logger.warning(
                "智谱搜索返回结构未识别 | keys=%s",
                list(payload)[:8] if isinstance(payload, dict) else type(payload).__name__,
            )
            return []
        return [
            Source(
                title=str(item.get("title") or "未命名结果"),
                url=str(item.get("link") or ""),
                snippet=str(item.get("content") or ""),
                provider="zhipu",
            )
            for item in items
            if isinstance(item, dict) and item.get("link")
        ]

    async def _deepseek(self, query: str) -> list[Source]:
        """DeepSeek 网页搜索。

        Documented response: ``results[].{rerank_score,title,url,snippet,summary,content,...}``.
        ``summary`` is 600–800 characters and ``content`` can carry the whole page
        when ``need_content`` is true — both far richer than a snippet, which is
        why this endpoint is worth keeping in the fusion set.
        """
        async with self._client() as client:
            response = await client.post(
                self.settings.deepseek_search_endpoint,
                headers={"Authorization": f"Bearer {self.settings.deepseek_api_key}"},
                json={
                    # The API caps query length at 128 characters.
                    "query": query[:128],
                    "count": self.settings.search_result_limit,
                    "need_content": self.settings.deepseek_need_content,
                },
            )
            response.raise_for_status()
            payload = response.json()
        items = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            logger.warning("DeepSeek 搜索返回结构未识别 | keys=%s", list(payload)[:8] if isinstance(payload, dict) else type(payload).__name__)
            return []
        return [
            Source(
                title=str(item.get("title") or "未命名结果"),
                url=str(item.get("url") or ""),
                snippet=str(item.get("summary") or item.get("snippet") or ""),
                content=str(item.get("content") or ""),
                provider="deepseek",
            )
            for item in items
            if isinstance(item, dict) and item.get("url")
        ]

    async def _metaso(self, query: str) -> list[Source]:
        """秘塔搜索。

        The endpoint answers ``{"errCode": 2005, "errMsg": "API密钥无效"}`` on a
        bad key, so failures arrive with HTTP 200 — the envelope has to be
        checked explicitly or a broken key would look like "no results".
        """
        async with self._client() as client:
            response = await client.post(
                self.settings.metaso_search_endpoint,
                headers={"Authorization": f"Bearer {self.settings.metaso_api_key}"},
                json={
                    "q": query,
                    "scope": "webpage",
                    "includeSummary": True,
                    "size": self.settings.search_result_limit,
                },
            )
            response.raise_for_status()
            payload = response.json()
        if isinstance(payload, dict) and payload.get("errCode") not in (None, 0, 200):
            logger.warning("秘塔搜索返回错误 | errCode=%s errMsg=%s", payload.get("errCode"), payload.get("errMsg"))
            return []
        items = self._find_result_list(payload, ("webpages", "data", "results", "list", "documents"))
        if items is None:
            logger.warning("秘塔搜索返回结构未识别 | keys=%s", list(payload)[:8] if isinstance(payload, dict) else type(payload).__name__)
            return []
        return [
            Source(
                title=str(item.get("title") or item.get("name") or "未命名结果"),
                url=str(item.get("url") or item.get("link") or ""),
                snippet=str(item.get("summary") or item.get("snippet") or item.get("content") or ""),
                provider="metaso",
            )
            for item in items
            if (item.get("url") or item.get("link"))
        ]

    async def _baidu(self, query: str) -> list[Source]:
        """百度千帆 AI 搜索：中文网页覆盖最全的一家。"""
        async with self._client() as client:
            response = await client.post(
                self.settings.baidu_search_endpoint,
                headers={"Authorization": f"Bearer {self.settings.baidu_api_key}"},
                json={
                    "messages": [{"role": "user", "content": query}],
                    "search_source": "baidu_search_v2",
                    "resource_type_filter": [{"type": "web", "top_k": self.settings.search_result_limit}],
                },
            )
            response.raise_for_status()
            payload = response.json()
        items = self._find_result_list(payload, ("references", "search_results", "results", "documents"))
        if items is None:
            logger.warning("百度搜索返回结构未识别 | keys=%s", list(payload)[:8] if isinstance(payload, dict) else type(payload).__name__)
            return []
        return [
            Source(
                title=str(item.get("title") or item.get("name") or "未命名结果"),
                url=str(item.get("url") or item.get("link") or ""),
                snippet=str(item.get("content") or item.get("snippet") or item.get("abstract") or ""),
                provider="baidu",
            )
            for item in items
            if (item.get("url") or item.get("link"))
        ]

    @staticmethod
    def _find_result_list(payload: object, keys: tuple[str, ...]) -> list[dict] | None:
        """Locate a list of result objects under any of the known key paths.

        Provider schemas drift and differ by API version. Searching a few
        plausible shapes beats hard-coding one and silently returning nothing
        when the vendor renames a field — but "not found" must stay
        distinguishable from "found, but empty", hence ``None`` vs ``[]``.
        """
        if not isinstance(payload, dict):
            return None
        for key in keys:
            value = payload.get(key)
            if isinstance(value, list) and all(isinstance(item, dict) for item in value):
                return value
            if isinstance(value, dict):
                for nested in ("value", "items", "list", "records"):
                    inner = value.get(nested)
                    if isinstance(inner, list) and all(isinstance(item, dict) for item in inner):
                        return inner
        data = payload.get("data")
        if isinstance(data, dict) and data is not payload:
            return SearchClient._find_result_list(data, keys)
        return None

    @staticmethod
    def _bocha_items(payload: object) -> list[dict]:
        """Find the result array in a Bocha response.

        The documented shape is ``data.webPages.value``. Several spellings show
        up in the wild, and an unknown shape must be reported rather than
        silently producing zero results that look like "no matches".
        """
        data = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(data, dict):
            pages = data.get("webPages") or data.get("webpages")
            if isinstance(pages, dict):
                value = pages.get("value")
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
            if isinstance(pages, list):
                return [item for item in pages if isinstance(item, dict)]
        logger.warning(
            "博查搜索返回结构未识别 | keys=%s",
            list(payload)[:8] if isinstance(payload, dict) else type(payload).__name__,
        )
        return []

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

    # A source must cover at least this share of the query's terms to be kept.
    # Low enough to tolerate paraphrasing, high enough to drop adjacent noise.
    MIN_QUERY_COVERAGE = 0.3

    # Neutral function words only. Domain vocabulary must never live here: a
    # ranker that knows about one topic silently degrades every other topic.
    STOPWORDS = frozenset({
        "the", "and", "for", "with", "how", "does", "what", "why", "when", "which",
        "are", "is", "was", "were", "in", "of", "to", "a", "an", "on", "at", "by",
        "best", "practices", "practice", "guide", "overview", "introduction",
    })

    # At most this many results from one domain. Without a cap, a single site can
    # fill every slot and the "multiple sources" claim becomes fiction.
    MAX_PER_DOMAIN = 2

    @classmethod
    def _rank_relevant(
        cls,
        sources: list[Source],
        query: str,
        agreement: dict[str, int] | None = None,
        max_per_domain: int = MAX_PER_DOMAIN,
    ) -> list[Source]:
        """Keep and order sources by how much of the query they lexically cover.

        Deliberately generic: no framework names, no language-specific intent
        words, no per-domain rules. If the planner wants a term to be mandatory,
        it must say so explicitly instead of relying on a hidden hard-coded list.

        Terms are weighted by inverse document frequency inside the candidate
        set, so a rare discriminating word counts more than a common one. A
        per-domain cap keeps one site from occupying the whole result list.
        """
        terms = {term for term in re.findall(r"[a-z][a-z0-9+#.-]{1,}", query.lower()) if term not in cls.STOPWORDS}
        chinese = {
            phrase[index:index + 2]
            for phrase in re.findall(r"[\u4e00-\u9fff]+", query)
            if phrase not in cls.STOPWORDS
            for index in range(len(phrase) - 1)
        }
        if not terms and not chinese:
            # Unparseable query: judge nothing rather than silently drop everything.
            return list(sources)

        texts = [f"{source.title} {source.snippet} {source.url}".lower() for source in sources]
        all_terms = terms | chinese
        total_documents = max(len(texts), 1)
        weights = {
            term: math.log(1 + total_documents / max(sum(1 for text in texts if term_present(term, text)), 1)) + 1.0
            for term in all_terms
        }
        latin_total = sum(weights[term] for term in terms) or 1.0
        chinese_total = sum(weights[term] for term in chinese) or 1.0

        scored: list[tuple[float, Source]] = []
        for source, text in zip(sources, texts):
            latin_score = sum(weights[term] for term in terms if term_present(term, text))
            chinese_score = sum(weights[term] for term in chinese if term_present(term, text))
            coverage = max(
                latin_score / latin_total if terms else 0.0,
                chinese_score / chinese_total if chinese else 0.0,
            )
            if coverage < cls.MIN_QUERY_COVERAGE:
                continue
            # Source type nudges ordering (+2 official, -1 community) but never
            # decides inclusion: a community post can still be the only evidence.
            priority = 2 if source.source_type == "official" else -1 if source.source_type == "community" else 0
            # Independent indexes agreeing on a page is weak but real evidence of
            # authority. Capped so three providers cannot outrank relevance.
            votes = (agreement or {}).get(source.id, 1)
            priority += 3 * min(max(votes - 1, 0), 2)
            scored.append((round(coverage, 4) * 10 + priority, source))

        scored.sort(key=lambda item: item[0], reverse=True)
        return cls._diversify(scored, max_per_domain)

    @staticmethod
    def _diversify(scored: list[tuple[float, Source]], max_per_domain: int) -> list[Source]:
        """Drop near-duplicate titles and cap how many results one domain takes."""
        per_domain: dict[str, int] = {}
        seen_titles: set[str] = set()
        selected: list[Source] = []
        for _, source in scored:
            domain = source.domain or (urlparse(source.url).hostname or "")
            if per_domain.get(domain, 0) >= max_per_domain:
                continue
            # Syndicated copies of the same article differ only in URL.
            title_key = re.sub(r"[\s\W_]+", "", source.title.lower())[:24]
            if title_key and title_key in seen_titles:
                continue
            per_domain[domain] = per_domain.get(domain, 0) + 1
            if title_key:
                seen_titles.add(title_key)
            selected.append(source)
        return selected

