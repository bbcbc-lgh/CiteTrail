from collections.abc import AsyncIterator
from uuid import uuid4

from src.Agent.PlannerAgent import PlannerAgent
from src.Agent.ReporterAgent import ReporterAgent
from src.Agent.SummarizerAgent import SummarizerAgent
from src.Infrastructure.Config import Settings
from src.Infrastructure.Tools import keywords, truncate
from src.Infrastructure.LLMClient import LLMClient
from src.Infrastructure.NoteStore import NoteStore
from src.Infrastructure.SearchClient import SearchClient
from src.Schema.Event import Event
from src.Schema.Task import Task
from src.Schema.Source import Source
from src.Schema.Summary import Summary
from src.Tool.NoteTool import NoteTool
from src.Tool.SearchTool import SearchTool


class ResearchWorkFlow:
    """Coordinates planning, searching, summarization, and report generation."""

    def __init__(self, settings: Settings):
        llm = LLMClient(settings)
        self.settings = settings
        self.planner = PlannerAgent(llm)
        self.summarizer = SummarizerAgent(llm)
        self.reporter = ReporterAgent(llm)
        self.search_tool = SearchTool(SearchClient(settings))
        self.note_tool = NoteTool(NoteStore(settings.workspace_dir / "notes"))

    async def research(self, topic: str) -> AsyncIterator[Event]:
        research_id = uuid4().hex
        yield self._event(research_id, "progress", "planning", 5, "正在分析研究主题并制定任务计划…")
        try:
            tasks = await self.planner.plan(topic, self.settings.max_tasks)
            if len(tasks) < 3:
                tasks = self._fallback_plan(topic)
            yield Event(type="plan", research_id=research_id, stage="planning", percentage=10, text="研究计划已生成", data={"tasks": [task.model_dump() for task in tasks]})
            summaries: list[Summary] = []
            total = len(tasks)
            for index, task in enumerate(tasks, start=1):
                start_percentage = 10 + int((index - 1) / total * 70)
                yield Event(type="task_started", research_id=research_id, stage="researching", percentage=start_percentage, text=f"正在研究任务 {index}/{total}：{task.title}", data={"task": task.model_dump(), "index": index, "total": total})
                sources = await self.search_tool.run(task.query)
                yield Event(type="search_results", research_id=research_id, stage="researching", percentage=start_percentage + 8, text=f"已获得 {len([item for item in sources if item.url])} 条可引用搜索结果", data={"task_id": task.id, "sources": [source.model_dump() for source in sources]})
                summary = await self.summarizer.summarize(topic, task, sources)
                summary = summary or self._fallback_summary(task, sources)
                self.note_tool.save_task_summary(research_id, summary)
                summaries.append(summary)
                yield Event(type="task_summary", research_id=research_id, stage="researching", percentage=10 + int(index / total * 70), text=f"已完成任务 {index}/{total} 的证据总结", data=summary.model_dump())
            yield self._event(research_id, "progress", "reporting", 85, "正在整合任务总结并撰写最终报告…")
            report = await self.reporter.generate(topic, summaries)
            report = report or self._fallback_report(topic, summaries)
            report_path = self.note_tool.save_report(research_id, report)
            yield Event(type="report", research_id=research_id, stage="reporting", percentage=100, text="研究报告已生成", data={"markdown": report, "report_path": str(report_path), "research_id": research_id})
            yield self._event(research_id, "done", "completed", 100, "研究完成。")
        except Exception as exc:
            yield Event(type="error", research_id=research_id, stage="failed", percentage=100, text="研究流程发生错误", data={"message": str(exc)})

    def _fallback_plan(self, topic: str) -> list[Task]:
        query_terms = " ".join(keywords(topic, 5)) or topic
        specs = [
            ("核心概念与范围", "建立研究主题的定义、边界和关键术语。", f"{query_terms} definition overview"),
            ("现状与关键证据", "收集近期事实、数据、权威观点和代表性案例。", f"{query_terms} current evidence analysis"),
            ("应用、影响与争议", "分析实际应用、潜在影响、局限性与不同观点。", f"{query_terms} applications challenges debate"),
            ("发展趋势与待验证问题", "识别未来方向以及仍需补充验证的知识空白。", f"{query_terms} future trends research gaps"),
        ]
        return [Task(title=title, intent=intent, query=query) for title, intent, query in specs[: self.settings.max_tasks]]

    @staticmethod
    def _fallback_summary(task: Task, sources: list[Source]) -> Summary:
        usable_sources = [source for source in sources if source.url]
        if not usable_sources:
            return Summary(task=task, summary="本任务没有获得可引用的搜索结果，因此不能据此形成可靠结论。", key_points=["请配置可用的搜索服务后重新执行此任务。"], sources=[])
        key_points = [truncate(source.snippet, 220) for source in usable_sources if source.snippet][:3]
        citations = "、".join(f"[{index}]" for index in range(1, len(usable_sources) + 1))
        return Summary(task=task, summary=f"围绕“{task.title}”，检索获得 {len(usable_sources)} 条可引用结果。以下要点来自搜索摘要，仍应打开原始来源核验上下文与发布时间：{citations}。", key_points=key_points or ["搜索结果缺少足够的摘要文本，建议访问来源页面进一步核验。"], sources=usable_sources)

    @staticmethod
    def _fallback_report(topic: str, summaries: list[Summary]) -> str:
        source_index: dict[str, int] = {}
        sources: list[tuple[int, str, str]] = []
        for summary in summaries:
            for source in summary.sources:
                if source.url and source.url not in source_index:
                    number = len(sources) + 1
                    source_index[source.url] = number
                    sources.append((number, source.title, source.url))
        lines = [f"# {topic}", "", "## 执行摘要", "本报告按研究计划逐项检索并汇总公开搜索结果。以下结论以来源摘要为基础；重要决策前应继续核验原文、作者和发布时间。", "", "## 研究发现"]
        for summary in summaries:
            citations = " ".join(f"[{source_index[source.url]}]" for source in summary.sources if source.url in source_index)
            lines.extend([f"### {summary.task.title}", summary.summary + (f" {citations}" if citations else "")])
            lines.extend(f"- {point}" for point in summary.key_points)
            lines.append("")
        lines.extend(["## 局限与待研究问题", "- 搜索摘要可能不完整、过期或脱离上下文；报告未自动抓取并验证全文。", "- 如果某项没有可用来源，应将其视为待补充的证据空白，而不是事实结论。", "", "## 来源"])
        lines.extend(f"{number}. [{title}]({url})" for number, title, url in sources) if sources else lines.append("本次执行未获得可引用的外部来源。")
        return "\n".join(lines)

    @staticmethod
    def _event(research_id: str, event_type: str, stage: str, percentage: int, text: str) -> Event:
        return Event(type=event_type, research_id=research_id, stage=stage, percentage=percentage, text=text)
