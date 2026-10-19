import asyncio
import re
from collections.abc import AsyncIterator
from uuid import uuid4

from src.Agent.PlannerAgent import PlannerAgent
from src.Agent.ReporterAgent import ReporterAgent
from src.Agent.SummarizerAgent import SummarizerAgent
from src.Infrastructure.Config import Settings
from src.Infrastructure.Tools import keywords, truncate
from src.Infrastructure.LLMClient import LLMClient
from src.Infrastructure.Evidence import markdown_label
from src.Infrastructure.NoteStore import NoteStore
from src.Infrastructure.SearchClient import SearchClient
from src.Schema.Event import Event
from src.Schema.Task import Task
from src.Schema.Source import Source
from src.Schema.Summary import Summary
from src.Tool.NoteTool import NoteTool
from src.Tool.SearchTool import SearchTool


class _ResearchCancelled(Exception):
    pass


class ResearchWorkFlow:
    """Coordinates planning, searching, summarization, and report generation."""

    def __init__(self, settings: Settings):
        llm = LLMClient(settings)
        self.settings = settings
        self.llm = llm
        self.planner = PlannerAgent(llm)
        self.summarizer = SummarizerAgent(llm)
        self.reporter = ReporterAgent(llm)
        self.search_tool = SearchTool(SearchClient(settings))
        self.note_tool = NoteTool(NoteStore(settings.workspace_dir / "notes"))

    async def research(self, topic: str, cancel_event: asyncio.Event | None = None, research_id: str | None = None) -> AsyncIterator[Event]:
        research_id = research_id or uuid4().hex
        degraded_reasons: list[str] = []
        current_percentage = 5
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
            tasks = await self._await_or_cancel(self.planner.plan(topic, self.settings.max_tasks), cancel_event)
            if self._cancelled(cancel_event):
                yield self._cancelled_event(research_id, 5)
                return
            if len(tasks) < 3:
                if self.llm.last_error:
                    degraded_reasons.append(self.llm.last_error)
                elif not degraded_reasons:
                    degraded_reasons.append("模型规划结果不可用")
                tasks = self._fallback_plan(topic)
                yield self._event(research_id, "progress", "planning", 10, "模型规划不可用，已使用本地回退计划", {"degraded": True, "reason": "；".join(dict.fromkeys(degraded_reasons))})
            current_percentage = 10
            yield Event(type="plan", research_id=research_id, stage="planning", percentage=10, text="研究计划已生成", data={"tasks": [task.model_dump() for task in tasks]})
            summaries: list[Summary] = []
            total = len(tasks)
            for index, task in enumerate(tasks, start=1):
                start_percentage = 10 + int((index - 1) / total * 70)
                current_percentage = start_percentage
                yield Event(type="task_started", research_id=research_id, stage="researching", percentage=start_percentage, text=f"正在研究任务 {index}/{total}：{task.title}", data={"task": task.model_dump(), "index": index, "total": total})
                sources = await self._await_or_cancel(self.search_tool.run(task.query, topic), cancel_event)
                if self._cancelled(cancel_event):
                    yield self._cancelled_event(research_id, start_percentage)
                    return
                current_percentage = start_percentage + 8
                yield Event(type="search_results", research_id=research_id, stage="researching", percentage=start_percentage + 8, text=f"已获得 {len([item for item in sources if item.url])} 条可引用搜索结果", data={"task_id": task.id, "sources": [source.model_dump() for source in sources]})
                summary = await self._await_or_cancel(self.summarizer.summarize(topic, task, sources), cancel_event)
                if self._cancelled(cancel_event):
                    yield self._cancelled_event(research_id, start_percentage + 8)
                    return
                if summary is None:
                    if self.llm.last_error:
                        degraded_reasons.append(self.llm.last_error)
                    elif not degraded_reasons:
                        degraded_reasons.append("未生成模型总结")
                    yield self._event(research_id, "progress", "researching", start_percentage + 8, "模型总结不可用，本步骤使用搜索摘要回退", {"degraded": True, "reason": "；".join(dict.fromkeys(degraded_reasons))})
                summary = summary or self._fallback_summary(task, sources)
                self.note_tool.save_task_summary(research_id, summary)
                summaries.append(summary)
                current_percentage = 10 + int(index / total * 70)
                yield Event(type="task_summary", research_id=research_id, stage="researching", percentage=10 + int(index / total * 70), text=f"已完成任务 {index}/{total} 的证据总结", data=summary.model_dump())
            current_percentage = 85
            yield self._event(research_id, "progress", "reporting", 85, "正在整合任务总结并撰写最终报告…")
            report = await self._await_or_cancel(self.reporter.generate(topic, summaries), cancel_event)
            if self._cancelled(cancel_event):
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
            self.note_tool.store.save_evidence(research_id, topic, summaries)
            report_path = self.note_tool.save_report(research_id, report)
            yield Event(type="report", research_id=research_id, stage="reporting", percentage=100, text="证据摘要已生成" if report_degraded else "综合报告已生成", data={"markdown": report, "report_path": str(report_path), "research_id": research_id, "degraded": report_degraded, "reason": "；".join(dict.fromkeys(degraded_reasons)) if report_degraded else ""})
            yield self._event(research_id, "done", "completed", 100, "研究完成。")
        except _ResearchCancelled:
            yield self._cancelled_event(research_id, current_percentage)
        except Exception as exc:
            yield Event(type="error", research_id=research_id, stage="failed", percentage=100, text="研究流程发生错误", data={"message": str(exc)})

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
            return Summary(task=task, summary="本任务没有获得可引用的搜索结果，因此不能据此形成可靠结论。", key_points=["请配置可用的搜索服务后重新执行此任务。"], sources=[])
        key_points = [truncate(source.snippet, 220) for source in usable_sources if source.snippet][:3]
        return Summary(task=task, summary=f"围绕“{task.title}”，检索获得 {len(usable_sources)} 条可引用结果。以下内容来自搜索摘要，仍应打开原始来源核验上下文与发布时间。", key_points=key_points or ["搜索结果缺少足够的摘要文本，建议访问来源页面进一步核验。"], sources=usable_sources)

    @staticmethod
    def _fallback_report(topic: str, summaries: list[Summary]) -> str:
        source_index: dict[str, int] = {}
        sources: list[Source] = []
        for summary in summaries:
            for source in summary.sources:
                if source.url and source.url not in source_index:
                    number = len(sources) + 1
                    source_index[source.url] = number
                    sources.append(source)
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
                number = source_index[source.url]
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
        lines.extend(f"{number}. [{markdown_label(source.title)}]({source.url})（{labels[source.source_type]}）" for number, source in enumerate(sources, 1)) if sources else lines.append("本次执行未获得可引用的外部来源。")
        return "\n".join(lines)

    async def regenerate(self, research_id: str) -> AsyncIterator[Event]:
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
