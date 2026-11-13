import asyncio
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from uuid import uuid4

from src.Agent.CriticAgent import CriticAgent
from src.Agent.PlannerAgent import PlannerAgent
from src.Agent.ReporterAgent import ReporterAgent
from src.Agent.SummarizerAgent import SummarizerAgent
from src.Infrastructure import Observability
from src.Infrastructure.Citations import build_citation_index
from src.Infrastructure.Config import Settings
from src.Infrastructure.Evidence import markdown_label
from src.Infrastructure.Fetcher import (
    PageFetcher,
    build_inline_documents,
    excerpts_by_source,
    group_chunks_by_source,
)
from src.Infrastructure.LLM import LLMClient
from src.Infrastructure.NoteStore import NoteStore
from src.Infrastructure.Observability import SpanRecord, span, start_trace
from src.Infrastructure.Reflection import assess
from src.Infrastructure.RunRepository import (
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_RUNNING,
    RunRecord,
    SqliteRunRepository,
)
from src.Infrastructure.SearchClient import SearchClient
from src.Infrastructure.Tools import keywords, truncate
from src.Infrastructure.Verification import ClaimVerifier
from src.Schema.Chunk import Chunk
from src.Schema.Claim import Claim
from src.Schema.Document import Document
from src.Schema.Event import Event
from src.Schema.Source import Source
from src.Schema.Summary import Summary
from src.Schema.Task import Task
from src.Tool.NoteTool import NoteTool
from src.Tool.SearchTool import SearchTool


class _ResearchCancelled(Exception):
    pass


@dataclass
class _TaskOutcome:
    """What one sub-task produced, handed back to the orchestration loop."""

    index: int
    task: Task
    summary: Summary
    degraded: bool
    documents: list[Document]
    chunks: list[Chunk]


# Cancellation is an expected outcome, not a failure: keep it out of error traces.
Observability.tracer.cancellation_types = (_ResearchCancelled,)


class ResearchWorkFlow:
    """Coordinates planning, searching, summarization, and report generation."""

    def __init__(self, settings: Settings):
        llm = LLMClient(settings)
        self.settings = settings
        self.llm = llm
        self.planner = PlannerAgent(llm)
        self.summarizer = SummarizerAgent(llm)
        self.reporter = ReporterAgent(llm)
        self.critic = CriticAgent(llm)
        self.verifier = ClaimVerifier(llm, max_claims=settings.verify_max_claims)
        self.fetcher = PageFetcher(settings)
        self.runs = SqliteRunRepository(settings.workspace_dir / "runs.db")
        self.search_tool = SearchTool(SearchClient(settings))
        self.note_tool = NoteTool(NoteStore(settings.workspace_dir / "notes"))

    async def research(self, topic: str, cancel_event: asyncio.Event | None = None, research_id: str | None = None) -> AsyncIterator[Event]:
        research_id = research_id or uuid4().hex
        with start_trace(
            research_id,
            "research",
            export_dir=self.settings.workspace_dir / "traces",
            **{"research.topic": topic, "research.max_tasks": self.settings.max_tasks},
        ) as run_span:
            try:
                async for event in self._execute(topic, cancel_event, research_id, run_span):
                    yield event
            except GeneratorExit:
                run_span.mark_cancelled()
                raise

    async def _execute(self, topic: str, cancel_event: asyncio.Event | None, research_id: str, run_span: SpanRecord) -> AsyncIterator[Event]:
        degraded_reasons: list[str] = []
        current_percentage = 5
        # Recorded immediately so a run stays visible in history even if the
        # process dies before it finishes.
        self.runs.save(RunRecord(research_id=research_id, topic=topic, status=STATUS_RUNNING))
        if not self.llm.is_configured:
            degraded_reasons.append("未配置大语言模型")
        yield self._event(
            research_id,
            "progress",
            "planning",
            5,
            "正在分析研究主题并制定任务计划…",
            {"degraded": bool(degraded_reasons), "reason": "；".join(degraded_reasons)},
        )
        try:
            with span("plan", **{"plan.max_tasks": self.settings.max_tasks}) as plan_span:
                tasks = await self._await_or_cancel(self.planner.plan(topic, self.settings.max_tasks), cancel_event)
                plan_fallback = False
                if self._cancelled(cancel_event):
                    run_span.mark_cancelled()
                    yield self._cancelled_event(research_id, 5)
                    return
                if len(tasks) < 3:
                    if self.llm.last_error:
                        degraded_reasons.append(self.llm.last_error)
                    elif not degraded_reasons:
                        degraded_reasons.append("模型规划结果不可用")
                    tasks = self._fallback_plan(topic)
                    plan_fallback = True
                    yield self._event(research_id, "progress", "planning", 10, "模型规划不可用，已使用本地回退计划", {"degraded": True, "reason": "；".join(dict.fromkeys(degraded_reasons))})
                plan_span.set_many({"plan.tasks": len(tasks), "plan.fallback": plan_fallback})
            current_percentage = 10
            yield Event(type="plan", research_id=research_id, stage="planning", percentage=10, text="研究计划已生成", data={"tasks": [task.model_dump() for task in tasks]})
            summaries: list[Summary] = []
            all_documents: list[Document] = []
            all_chunks: list[Chunk] = []
            total = len(tasks)
            # 任务并发执行，事件经一个队列回流，保证 SSE 仍是"发生即推送"而不是按序号编造。
            queue: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
            semaphore = asyncio.Semaphore(max(1, min(self.settings.task_concurrency, total)))

            async def emit(event: Event) -> None:
                await queue.put(("event", event))

            async def worker(task: Task, index: int) -> None:
                async with semaphore:
                    try:
                        outcome = await self._run_task(research_id, topic, task, index, total, emit, cancel_event, run_span, degraded_reasons)
                        await queue.put(("done", outcome))
                    except _ResearchCancelled:
                        await queue.put(("cancelled", index))
                    except Exception as exc:  # noqa: BLE001
                        await queue.put(("error", exc))

            workers = [asyncio.create_task(worker(task, index)) for index, task in enumerate(tasks, start=1)]
            completed = 0
            failure: Exception | None = None
            was_cancelled = False
            try:
                while completed < total:
                    kind, payload = await queue.get()
                    if kind == "event":
                        yield payload
                        continue
                    if kind == "cancelled":
                        was_cancelled = True
                        break
                    if kind == "error":
                        failure = payload
                        break
                    completed += 1
                    outcome = payload
                    all_documents.extend(outcome.documents)
                    all_chunks.extend(outcome.chunks)
                    self.note_tool.save_task_summary(research_id, outcome.summary)
                    summaries.append(outcome.summary)
                    current_percentage = 10 + int(completed / total * 70)
                    yield Event(type="task_summary", research_id=research_id, stage="researching", percentage=current_percentage, text=f"已完成 {completed}/{total} 个研究步骤", data=outcome.summary.model_dump())
            finally:
                for handle in workers:
                    if not handle.done():
                        handle.cancel()
                await asyncio.gather(*workers, return_exceptions=True)

            if failure is not None:
                raise failure
            if was_cancelled:
                run_span.mark_cancelled()
                yield self._cancelled_event(research_id, current_percentage)
                return
            current_percentage = 85
            yield self._event(research_id, "progress", "reporting", 85, "正在整合任务总结并撰写最终报告…")
            with span("report") as report_span:
                report = await self._await_or_cancel(self.reporter.generate(topic, summaries), cancel_event)
                if self._cancelled(cancel_event):
                    run_span.mark_cancelled()
                    yield self._cancelled_event(research_id, 85)
                    return
                report_degraded = not bool(report)
                if report_degraded:
                    if self.llm.last_error:
                        degraded_reasons.append(self.llm.last_error)
                    elif not degraded_reasons:
                        degraded_reasons.append("未生成模型报告")
                    yield self._event(research_id, "progress", "reporting", 90, "模型报告不可用，已使用本地回退报告", {"degraded": True, "reason": "；".join(dict.fromkeys(degraded_reasons))})
                report = report or self._fallback_report(topic, summaries)
                report_span.set_many({"report.degraded": report_degraded, "report.chars": len(report)})
            citations = build_citation_index(summaries)
            self.note_tool.store.save_evidence(
                research_id,
                topic,
                summaries,
                documents=[document.model_dump() for document in all_documents],
                chunks=[chunk.model_dump() for chunk in all_chunks],
            )
            report_path = self.note_tool.save_report(research_id, report)
            run_span.set_many(
                {
                    "research.tasks": len(summaries),
                    "research.claims": sum(len(summary.claims) for summary in summaries),
                    "research.sources": len(citations),
                    "research.documents": len(all_documents),
                    "research.chunks": len(all_chunks),
                    "research.degraded": report_degraded,
                    "research.report_chars": len(report),
                }
            )
            self.runs.save(
                RunRecord(
                    research_id=research_id,
                    topic=topic,
                    status=STATUS_COMPLETED,
                    degraded=report_degraded,
                    task_count=len(summaries),
                    source_count=len(citations),
                    claim_count=sum(len(summary.claims) for summary in summaries),
                    report_path=str(report_path),
                    reason="；".join(dict.fromkeys(degraded_reasons)) if report_degraded else "",
                )
            )
            yield Event(type="report", research_id=research_id, stage="reporting", percentage=100, text="证据摘要已生成" if report_degraded else "综合报告已生成", data={"markdown": report, "report_path": str(report_path), "research_id": research_id, "degraded": report_degraded, "reason": "；".join(dict.fromkeys(degraded_reasons)) if report_degraded else "", "citations": [citation.model_dump() for citation in citations]})
            yield self._event(research_id, "done", "completed", 100, "研究完成。")
        except _ResearchCancelled:
            run_span.mark_cancelled()
            self.runs.save(RunRecord(research_id=research_id, topic=topic, status=STATUS_CANCELLED))
            yield self._cancelled_event(research_id, current_percentage)
        except Exception as exc:
            self.runs.save(RunRecord(research_id=research_id, topic=topic, status=STATUS_FAILED, reason=str(exc)[:200]))
            yield Event(type="error", research_id=research_id, stage="failed", percentage=100, text="研究流程发生错误", data={"message": str(exc)})

    async def _run_task(
        self,
        research_id: str,
        topic: str,
        task: Task,
        index: int,
        total: int,
        emit: Callable[[Event], Awaitable[None]],
        cancel_event: asyncio.Event | None,
        run_span: SpanRecord,
        degraded_reasons: list[str],
    ) -> "_TaskOutcome":
        """One sub-task: retrieve, summarise, verify, optionally reflect."""
        with span("task", **{"task.id": task.id, "task.title": task.title, "task.index": index, "task.total": total}) as task_span:
            start_percentage = 10 + int((index - 1) / total * 70)
            current_percentage = start_percentage
            await emit(Event(type="task_started", research_id=research_id, stage="researching", percentage=start_percentage, text=f"正在研究任务 {index}/{total}：{task.title}", data={"task": task.model_dump(), "index": index, "total": total}))

            pending_queries = [task.query]
            used_queries: list[str] = []
            task_sources: list[Source] = []
            task_documents: list[Document] = []
            task_chunks: list[Chunk] = []
            summary: Summary | None = None
            task_degraded = False
            # Reflection only pays off when a model can act on the gap.
            rounds_available = 1 + (self.settings.max_web_research_loops if self.llm.is_configured else 0)
            rounds_used = 0

            for round_index in range(rounds_available):
                rounds_used = round_index + 1
                used_queries.extend(pending_queries)
                sources_before = len(task_sources)
                with span("evidence_round", **{"round": rounds_used, "round.queries": pending_queries}) as round_span:
                    task_sources, task_documents, task_chunks = await self._await_or_cancel(
                        self._gather_round(topic, task, pending_queries, task_sources, task_documents, task_chunks),
                        cancel_event,
                    )
                    if self._cancelled(cancel_event):
                        raise _ResearchCancelled
                    round_span.set_many(
                        {
                            "round.sources": len([source for source in task_sources if source.url]),
                            "round.new_sources": len(task_sources) - sources_before,
                            "round.documents": len(task_documents),
                            "round.chunks": len(task_chunks),
                        }
                    )

                # A follow-up that surfaced nothing new cannot improve the
                # summary; re-running the model on identical evidence just
                # burns tokens and perturbs the result.
                if round_index > 0 and len(task_sources) == sources_before:
                    task_span.set("task.reflection_empty", True)
                    break

                current_percentage = start_percentage + 8
                await emit(Event(type="search_results", research_id=research_id, stage="researching", percentage=start_percentage + 8, text=f"已获得 {len([item for item in task_sources if item.url])} 条可引用搜索结果", data={"task_id": task.id, "sources": [source.model_dump() for source in task_sources], "documents": len(task_documents), "chunks": len(task_chunks), "round": rounds_used}))

                chunks_by_source = group_chunks_by_source(task_documents, task_chunks)
                summary = await self._await_or_cancel(self.summarizer.summarize(topic, task, task_sources, chunks_by_source), cancel_event)
                if self._cancelled(cancel_event):
                    raise _ResearchCancelled

                task_degraded = summary is None
                if task_degraded:
                    if self.llm.last_error:
                        degraded_reasons.append(self.llm.last_error)
                    elif not degraded_reasons:
                        degraded_reasons.append("未生成模型总结")
                    await emit(self._event(research_id, "progress", "researching", start_percentage + 8, "模型总结不可用，本步骤使用搜索摘要回退", {"degraded": True, "reason": "；".join(dict.fromkeys(degraded_reasons))}))
                    summary = self._fallback_summary(task, task_sources)
                    break

                if self.settings.verify_claims and self.verifier.enabled:
                    # Only model-written claims are worth checking: the fallback
                    # builds claims directly from the snippet it cites, so a
                    # check would score itself. The verifier reads the same
                    # excerpts the claims were written from.
                    summary = await self._await_or_cancel(
                        self.verifier.verify_summary(topic, summary, excerpts_by_source(chunks_by_source)),
                        cancel_event,
                    )
                    if self._cancelled(cancel_event):
                        raise _ResearchCancelled

                decision = assess(summary, task_sources)
                if not decision.needed or rounds_used >= rounds_available:
                    break

                proposals = await self._await_or_cancel(
                    self.critic.propose_queries(topic, task, summary, used_queries, self.settings.reflection_max_queries),
                    cancel_event,
                )
                if self._cancelled(cancel_event):
                    raise _ResearchCancelled
                if not proposals:
                    task_span.set("task.reflection_stopped", decision.reason)
                    break

                task_span.set_many({"task.reflection_round": rounds_used, "task.reflection_reason": decision.reason})
                await emit(
                    self._event(
                        research_id,
                        "progress",
                        "researching",
                        current_percentage,
                        f"证据不足（{decision.reason}），正在补充检索…",
                        {"reflection": True, "reason": decision.reason, "queries": proposals},
                    )
                )
                pending_queries = proposals

            task_span.set_many(
                {
                    "task.sources": len([item for item in task_sources if item.url]),
                    "task.documents": len(task_documents),
                    "task.chunks": len(task_chunks),
                    "task.evidence_chars": sum(len(chunk.text) for chunk in task_chunks),
                    "task.rounds": rounds_used,
                    "task.degraded": task_degraded,
                    "task.claims": len(summary.claims),
                    "task.cited_claims": sum(1 for claim in summary.claims if claim.cited_source_ids),
                    "task.verified_claims": sum(1 for claim in summary.claims if claim.support_score is not None),
                }
            )
        return _TaskOutcome(
            index=index,
            task=task,
            summary=summary,
            degraded=task_degraded,
            documents=task_documents,
            chunks=task_chunks,
        )

    async def _gather_round(
        self,
        topic: str,
        task: Task,
        queries: list[str],
        sources: list[Source],
        documents: list[Document],
        chunks: list[Chunk],
    ) -> tuple[list[Source], list[Document], list[Chunk]]:
        """Run one retrieval round, adding only evidence not already collected.

        Sources are deduplicated by stable id, so a follow-up query that
        re-surfaces the same page does not re-fetch it.
        """
        known = {source.id for source in sources}
        for query in queries:
            for source in await self.search_tool.run(query, topic):
                if source.url and source.id not in known:
                    known.add(source.id)
                    sources.append(source)

        if self.llm.is_configured:
            # Bodies that arrived with the search response cost nothing extra.
            inline_documents, inline_chunks = build_inline_documents(
                task.query, sources, self.settings.chunks_per_source
            )
            if inline_documents:
                documents.extend(inline_documents)
                chunks.extend(inline_chunks)

        if not (self.fetcher.enabled and self.llm.is_configured):
            return sources, documents, chunks

        fetched = {document.source_id for document in documents}
        pending = [
            source for source in sources if source.url and source.id not in fetched
        ][: self.settings.fetch_max_sources]
        if pending:
            new_documents, new_chunks = await self.fetcher.enrich(task.query, pending)
            documents.extend(new_documents)
            chunks.extend(new_chunks)
        return sources, documents, chunks

    def _fallback_plan(self, topic: str) -> list[Task]:
        query_terms = " ".join(keywords(topic, 5)) or topic
        if re.search(r"[\u4e00-\u9fff]", topic):
            suffixes = ("核心概念 定义", "现状 数据 案例", "应用 影响 局限", "发展趋势 争议")
        else:
            suffixes = ("definition overview", "current evidence examples", "applications impact limitations", "future trends debate")
        specs = [
            ("核心概念与范围", "建立研究主题的定义、边界和关键术语。", f"{query_terms} {suffixes[0]}"),
            ("现状与关键证据", "收集近期事实、数据、权威观点和代表性案例。", f"{query_terms} {suffixes[1]}"),
            ("应用、影响与争议", "分析实际应用、潜在影响、局限性与不同观点。", f"{query_terms} {suffixes[2]}"),
            ("发展趋势与待验证问题", "识别未来方向以及仍需补充验证的知识空白。", f"{query_terms} {suffixes[3]}"),
        ]
        return [Task(title=title, intent=intent, query=query) for title, intent, query in specs[: self.settings.max_tasks]]

    @staticmethod
    def _fallback_summary(task: Task, sources: list[Source]) -> Summary:
        usable_sources = [source for source in sources if source.url]
        if not usable_sources:
            return Summary(
                task=task,
                summary="本任务没有获得可引用的搜索结果，因此不能据此形成可靠结论。",
                claims=[Claim(text="请配置可用的搜索服务后重新执行此任务。")],
                sources=[],
            )
        claims = [
            Claim(text=truncate(source.snippet, 220), cited_source_ids=[source.id])
            for source in usable_sources
            if source.snippet
        ][:3]
        if not claims:
            claims = [Claim(text="搜索结果缺少足够的摘要文本，建议访问来源页面进一步核验。")]
        return Summary(
            task=task,
            summary=f"围绕“{task.title}”，检索获得 {len(usable_sources)} 条可引用结果。以下内容来自搜索摘要，仍应打开原始来源核验上下文与发布时间。",
            claims=claims,
            sources=usable_sources,
        )

    @staticmethod
    def _fallback_report(topic: str, summaries: list[Summary]) -> str:
        citations = build_citation_index(summaries)
        number_of = {citation.source_id: citation.index for citation in citations}
        by_id: dict[str, Source] = {}
        for summary in summaries:
            for source in summary.sources:
                if source.url and source.id not in by_id:
                    by_id[source.id] = source
        lines = [f"# {markdown_label(topic)}", "", "## 结论状态", "当前使用本地回退逻辑。本文件是证据摘要，不是经过模型综合的最终结论。摘要未经全文核验；来源不足时保留证据缺口。", "", "## 按主题整理的证据"]
        seen: set[str] = set()
        gaps: list[str] = []
        labels = {"official": "文档来源", "article": "文章", "community": "社区观点，未经独立验证"}
        for summary in summaries:
            lines.append(f"### {markdown_label(summary.task.title)}")
            added = False
            shared: list[str] = []
            for source in summary.sources:
                if not source.url:
                    continue
                number = number_of[source.id]
                if source.snippet == "搜索结果未提供可直接引用的正文摘要。" or not source.snippet:
                    shared.append(f"[{number}] 只有来源线索，缺少可读摘要，需打开原文核验。")
                    continue
                sentences = re.split(r"(?<=[。！？!?])\s*|(?<=\.)\s+|\n+", source.snippet)
                fresh = []
                for sentence in sentences:
                    key = re.sub(r"\s+", "", sentence).lower()
                    if key and key not in seen:
                        seen.add(key)
                        fresh.append(sentence)
                if not fresh:
                    shared.append(f"[{number}]")
                    continue
                lines.append(f"- [{number}]（{labels[source.source_type]}）{markdown_label(' '.join(fresh))}")
                added = True
                if source.code:
                    lines.extend(["", f"### 代码摘录 [{number}]（未执行验证）", source.code])
            if shared:
                lines.append("- 相关线索或前文已展示的证据：" + "；".join(shared))
            if not added:
                gaps.append(f"{summary.task.title}：{summary.task.intent}")
                lines.append("- 本步骤没有新增的可读证据，尚不能回答该方向的问题。")
            lines.append("")
        lines.extend(["## 尚未回答的问题", f"- 原始问题“{markdown_label(topic)}”的综合答案仍需在模型服务恢复后生成。"])
        lines.extend(f"- {markdown_label(gap)}" for gap in gaps)
        lines.extend(["", "## 局限与待研究问题", "- 搜索摘要可能不完整、过期或脱离上下文；报告未自动抓取并验证全文。", "- 来源类型根据网站地址初步识别，不代表内容已经验证。社区讨论仅作为补充。", "", "## 来源"])
        lines.extend(
            f"{citation.index}. [{markdown_label(by_id[citation.source_id].title)}]({citation.url})（{labels[by_id[citation.source_id].source_type]}）"
            for citation in citations
        ) if citations else lines.append("本次执行未获得可引用的外部来源。")
        return "\n".join(lines)

    async def regenerate(self, research_id: str) -> AsyncIterator[Event]:
        with start_trace(
            f"{research_id}-regen",
            "regenerate",
            export_dir=self.settings.workspace_dir / "traces",
            **{"research.id": research_id},
        ):
            try:
                topic, summaries = self.note_tool.store.load_evidence(research_id)
            except FileNotFoundError:
                yield self._event(research_id, "error", "failed", 100, "找不到已保存证据", {"message": "本轮尚无可复用证据，请先完成一次研究"})
                return
            if not self.llm.is_configured:
                yield self._event(research_id, "error", "failed", 100, "模型未配置，无法重新生成综合报告", {"message": "请配置模型服务后重试"})
                return
            yield self._event(research_id, "progress", "reporting", 85, "正在基于已保存证据重新生成报告…")
            report = await self.reporter.generate(topic, summaries)
            if not report:
                yield self._event(research_id, "error", "failed", 100, "模型报告生成失败", {"message": self.llm.last_error or "模型响应为空"})
                return
            report_path = self.note_tool.save_report(research_id, report)
            yield Event(type="report", research_id=research_id, stage="reporting", percentage=100, text="综合报告已重新生成", data={"markdown": report, "report_path": str(report_path), "research_id": research_id, "degraded": False, "reason": ""})
            yield self._event(research_id, "done", "completed", 100, "综合报告已生成。")

    @staticmethod
    def _cancelled(cancel_event: asyncio.Event | None) -> bool:
        return cancel_event is not None and cancel_event.is_set()

    @staticmethod
    async def _await_or_cancel(awaitable, cancel_event: asyncio.Event | None):
        if cancel_event is None:
            return await awaitable
        operation = asyncio.create_task(awaitable)
        cancellation = asyncio.create_task(cancel_event.wait())
        try:
            done, _ = await asyncio.wait((operation, cancellation), return_when=asyncio.FIRST_COMPLETED)
            if cancellation in done:
                operation.cancel()
                await asyncio.gather(operation, return_exceptions=True)
                raise _ResearchCancelled
            return operation.result()
        finally:
            if not operation.done():
                operation.cancel()
            cancellation.cancel()
            await asyncio.gather(operation, cancellation, return_exceptions=True)

    @staticmethod
    def _cancelled_event(research_id: str, percentage: int) -> Event:
        return Event(type="cancelled", research_id=research_id, stage="cancelled", percentage=percentage, text="研究已停止，已完成内容已保留。")

    @staticmethod
    def _event(research_id: str, event_type: str, stage: str, percentage: int, text: str, data: dict[str, object] | None = None) -> Event:
        return Event(type=event_type, research_id=research_id, stage=stage, percentage=percentage, text=text, data=data or {})
