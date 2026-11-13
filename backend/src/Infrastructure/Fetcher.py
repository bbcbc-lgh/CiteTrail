"""Fetch the readable body of a search result.

Search snippets are too short to answer most questions, but fetching every page
is slow, brittle and SSRF-prone. This module fetches a capped number of pages in
parallel, extracts the main text with trafilatura (falling back to a crude
paragraph scrape if it is unavailable), and hands back a document plus the few
chunks that actually match the query.

Every failure is local to one source: a page that 403s or times out simply keeps
its snippet further down the pipeline.
"""

from __future__ import annotations

import asyncio
import html as html_module
import ipaddress
import logging
import re
import socket
from dataclasses import dataclass
from urllib.parse import urlparse
from uuid import uuid4

import httpx

from src.Infrastructure.Chunking import build_chunks, select_chunks, split_text
from src.Infrastructure.Config import Settings
from src.Infrastructure.Observability import span
from src.Schema.Chunk import Chunk
from src.Schema.Document import Document
from src.Schema.Source import Source

logger = logging.getLogger(__name__)

_ALLOWED_SCHEMES = {"http", "https"}
_ALLOWED_CONTENT_PREFIXES = ("text/html", "application/xhtml", "text/plain")
_COMMENT_OR_SCRIPT = re.compile(r"(?is)<(script|style|noscript|svg|nav|footer|header)[^>]*>.*?</\1>")
_PARAGRAPH = re.compile(r"(?is)<p[^>]*>(.*?)</p>")
_ANY_TAG = re.compile(r"(?s)<[^>]+>")


@dataclass
class FetchResult:
    document: Document
    chunks: list[Chunk]


class PageFetcher:
    """Downloads and extracts readable text for a bounded set of sources."""

    USER_AGENT = "CiteTrail/0.2 (local research tool)"
    ACCEPT = "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"

    def __init__(self, settings: Settings, client_factory=None):
        self.settings = settings
        # Injected in tests so nothing touches the network.
        self._client_factory = client_factory or self._default_client

    @property
    def enabled(self) -> bool:
        return bool(self.settings.fetch_pages)

    def _default_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.settings.fetch_timeout,
            follow_redirects=True,
            max_redirects=5,
            headers={"User-Agent": self.USER_AGENT, "Accept": self.ACCEPT},
        )

    async def enrich(self, query: str, sources: list[Source]) -> tuple[list[Document], list[Chunk]]:
        """Fetch up to ``fetch_max_sources`` pages and keep their relevant chunks."""
        if not self.enabled:
            return [], []
        targets = [source for source in sources if source.url][: self.settings.fetch_max_sources]
        if not targets:
            return [], []

        async with self._client_factory() as client:
            results = await asyncio.gather(
                *(self._fetch_one(client, source, query) for source in targets),
                return_exceptions=True,
            )

        documents: list[Document] = []
        chunks: list[Chunk] = []
        failures = 0
        for source, result in zip(targets, results):
            if isinstance(result, BaseException) or result is None:
                failures += 1
                continue
            documents.append(result.document)
            chunks.extend(result.chunks)
        logger.info(
            "正文抓取完成 | 目标=%d 成功=%d 失败=%d chunks=%d",
            len(targets), len(documents), failures, len(chunks),
        )
        return documents, chunks

    async def _fetch_one(self, client: httpx.AsyncClient, source: Source, query: str) -> FetchResult | None:
        with span("page_fetch", **{"fetch.url": source.url}) as fetch_span:
            try:
                payload = await self._download(client, source.url)
            except Exception as exc:
                fetch_span.set_many({"fetch.ok": False, "fetch.error": f"{type(exc).__name__}: {exc}"[:200]})
                return None

            body = extract_readable(payload, source.url)
            if not body:
                fetch_span.set_many({"fetch.ok": False, "fetch.error": "没有抽取到正文"})
                return None

            texts = split_text(body)
            selected = select_chunks(query, texts, self.settings.chunks_per_source)
            document = Document(
                id=uuid4().hex[:12],
                source_id=source.id,
                text=body,
                word_count=len(body),
                meta={"url": source.url},
            )
            chunks = build_chunks(document.id, texts, selected)
            fetch_span.set_many(
                {
                    "fetch.ok": True,
                    "fetch.chars": len(body),
                    "fetch.chunks": len(texts),
                    "fetch.selected": len(chunks),
                }
            )
            return FetchResult(document=document, chunks=chunks)

    async def _download(self, client: httpx.AsyncClient, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in _ALLOWED_SCHEMES:
            raise ValueError(f"不支持的协议：{parsed.scheme}")
        if not await self._is_public_host(parsed.hostname or ""):
            raise ValueError("拒绝访问内网地址")

        async with client.stream("GET", url) as response:
            response.raise_for_status()
            content_type = (response.headers.get("content-type") or "").lower()
            if content_type and not content_type.startswith(_ALLOWED_CONTENT_PREFIXES):
                raise ValueError(f"不支持的内容类型：{content_type}")

            buffer = bytearray()
            async for chunk in response.aiter_bytes():
                buffer.extend(chunk)
                if len(buffer) >= self.settings.fetch_max_bytes:
                    break
        return bytes(buffer).decode(response.charset_encoding or "utf-8", errors="replace")

    @staticmethod
    async def _is_public_host(host: str) -> bool:
        """Reject loopback, private and link-local targets.

        URLs come from search results, so this is the only thing standing
        between a poisoned result and the local network.
        """
        if not host:
            return False
        try:
            infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
        except OSError:
            return False
        for info in infos:
            try:
                address = ipaddress.ip_address(info[4][0])
            except ValueError:
                return False
            if (
                address.is_private
                or address.is_loopback
                or address.is_link_local
                or address.is_reserved
                or address.is_multicast
                or address.is_unspecified
            ):
                return False
        return True


# Below this length an inline body is treated as a stub rather than the article.
INLINE_MIN_CHARS = 400


def build_inline_documents(
    query: str,
    sources: list[Source],
    chunks_per_source: int,
) -> tuple[list[Document], list[Chunk]]:
    """Turn page bodies that arrived with the search results into evidence.

    Some search APIs return the article text directly. Using it costs nothing
    extra, skips an HTTP request, and succeeds on JS-rendered pages where a
    plain GET returns a shell.
    """
    documents: list[Document] = []
    chunks: list[Chunk] = []
    for source in sources:
        text = (source.content or "").strip()
        if len(text) < INLINE_MIN_CHARS:
            continue
        document = Document(
            id=uuid4().hex[:12],
            source_id=source.id,
            text=text,
            word_count=len(text),
            meta={"url": source.url, "source": "search_api"},
        )
        texts = split_text(text)
        documents.append(document)
        chunks.extend(build_chunks(document.id, texts, select_chunks(query, texts, chunks_per_source)))
    return documents, chunks


def group_chunks_by_source(documents: list[Document], chunks: list[Chunk]) -> dict[str, list[Chunk]]:
    """Map selected chunks back to the source they came from."""
    source_of = {document.id: document.source_id for document in documents}
    grouped: dict[str, list[Chunk]] = {}
    for chunk in chunks:
        source_id = source_of.get(chunk.document_id)
        if source_id:
            grouped.setdefault(source_id, []).append(chunk)
    return grouped


def excerpts_by_source(grouped: dict[str, list[Chunk]]) -> dict[str, str]:
    """Flatten grouped chunks into one text block per source.

    The summariser and the verifier must look at the same text; deriving both
    from this single map is what keeps them in sync.
    """
    return {source_id: "\n\n".join(chunk.text for chunk in items) for source_id, items in grouped.items()}


def extract_readable(html_text: str, url: str = "") -> str:
    """Extract the main body text, preferring trafilatura when installed."""
    try:
        import trafilatura  # imported lazily: it is an optional heavy dependency

        extracted = trafilatura.extract(
            html_text,
            url=url or None,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
        )
        if extracted:
            return extracted.strip()
    except Exception as exc:  # missing package, malformed input, unexpected parser error
        logger.debug("trafilatura 抽取失败，改用回退方案 | %s", exc)
    return _fallback_extract(html_text)


def _fallback_extract(html_text: str) -> str:
    """Crude paragraph scrape, used only when trafilatura cannot help."""
    stripped = _COMMENT_OR_SCRIPT.sub(" ", html_text or "")
    paragraphs = _PARAGRAPH.findall(stripped)
    source = " ".join(paragraphs) if paragraphs else stripped
    text = html_module.unescape(_ANY_TAG.sub(" ", source))
    return re.sub(r"\s+", " ", text).strip()
