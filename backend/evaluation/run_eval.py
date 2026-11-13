"""Run the CiteTrail evaluation set and produce a comparable baseline.

    cd backend
    python -m evaluation.run_eval --dry-run
    python -m evaluation.run_eval --limit 2
    python -m evaluation.run_eval --label baseline-2026-11
    python -m evaluation.run_eval --compare evaluation/baselines/baseline-2026-11.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):  # allow `python evaluation/run_eval.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evaluation.metrics import (  # noqa: E402
    DEFAULT_POINT_THRESHOLD,
    DEFAULT_SUPPORT_THRESHOLD,
    SEMANTIC_SUPPORT_THRESHOLD,
    aggregate,
    evaluate,
)
from src.Infrastructure.Config import Settings  # noqa: E402
from src.Infrastructure.LLM import LLMClient  # noqa: E402
from src.Schema.Summary import Summary  # noqa: E402
from src.WorkFlow.ResearchWorkFlow import ResearchWorkFlow  # noqa: E402

EVAL_DIR = Path(__file__).resolve().parent
REPO_ROOT = EVAL_DIR.parents[1]


def load_cases(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload["cases"]


async def run_case(case: dict, settings: Settings) -> dict:
    workflow = ResearchWorkFlow(settings)
    research_id = f"eval-{case['id']}"
    report = ""
    summaries: list[Summary] = []
    degraded = False
    error: str | None = None
    started = time.time()

    async for event in workflow.research(case["topic"], research_id=research_id):
        if event.type == "report":
            report = str(event.data.get("markdown", ""))
            degraded = bool(event.data.get("degraded"))
        elif event.type == "task_summary":
            summaries.append(Summary.model_validate(event.data))
        elif event.type == "error":
            error = str(event.data.get("message") or event.text)

    duration_s = round(time.time() - started, 1)

    tokens: dict[str, int] = {}
    trace_path = settings.workspace_dir / "traces" / f"{research_id}.json"
    if trace_path.exists():
        summary = json.loads(trace_path.read_text(encoding="utf-8")).get("summary", {})
        tokens = {
            "llm_calls": summary.get("llm_calls", 0),
            "prompt_tokens": summary.get("prompt_tokens", 0),
            "completion_tokens": summary.get("completion_tokens", 0),
        }

    metrics = evaluate(report, summaries, case["expected_points"])
    return {
        "id": case["id"],
        "topic": case["topic"],
        "language": case.get("language"),
        "domain": case.get("domain"),
        "report_chars": len(report),
        "degraded": degraded,
        "error": error,
        "duration_s": duration_s,
        "tokens": tokens,
        # Capabilities as they actually ended up after any runtime downgrade.
        "capabilities": workflow.llm.capabilities.as_dict(),
        "metrics": metrics.as_dict(),
    }


def format_rows(rows: list[dict]) -> str:
    header = f"{'case':30s} {'cov':>5s} {'recall':>6s} {'ground':>6s} {'prec':>5s} {'meas':>5s} {'claims':>6s} {'sec':>6s}"
    lines = [header, "-" * len(header)]
    for row in rows:
        metrics = row.get("metrics") or {}
        if row.get("error"):
            lines.append(f"{row['id']:30s}  ERROR: {row['error'][:60]}")
            continue
        lines.append(
            f"{row['id']:30s} "
            f"{metrics.get('coverage', 0):5.2f} "
            f"{metrics.get('citation_recall', 0):6.2f} "
            f"{metrics.get('groundedness', 0):6.2f} "
            f"{metrics.get('citation_precision', 0):5.2f} "
            f"{metrics.get('measurable_rate', 0):5.2f} "
            f"{metrics.get('claims', 0):6d} "
            f"{row.get('duration_s', 0):6.0f}"
        )
    return "\n".join(lines)


def compare_against(current: dict, baseline_path: Path) -> str:
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    old, new = baseline.get("aggregate", {}), current.get("aggregate", {})
    lines = [f"对比基线：{baseline_path.name}", f"{'metric':22s} {'baseline':>9s} {'current':>9s} {'delta':>8s}"]
    for name in ("coverage", "citation_recall", "groundedness", "citation_precision", "cited_claim_rate", "measurable_rate"):
        before = float(old.get(name, 0))
        after = float(new.get(name, 0))
        lines.append(f"{name:22s} {before:9.3f} {after:9.3f} {after - before:+8.3f}")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="运行 CiteTrail 评测集")
    parser.add_argument("--cases", type=Path, default=EVAL_DIR / "cases.json")
    parser.add_argument("--ids", default="", help="逗号分隔的用例 id")
    parser.add_argument("--limit", type=int, default=0, help="只运行前 N 条用例")
    parser.add_argument("--max-tasks", type=int, default=None, help="覆盖 MAX_TASKS（仅本次运行）")
    parser.add_argument("--search-api", default="", help="覆盖 SEARCH_API（仅本次运行）")
    parser.add_argument("--label", default="", help="同时保存为 backend/evaluation/baselines/<label>.json")
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "outputs" / "eval")
    parser.add_argument("--compare", type=Path, default=None, help="与已有基线 JSON 对比")
    parser.add_argument("--dry-run", action="store_true", help="只校验用例集，不调用模型")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    cases = load_cases(args.cases)

    if args.ids:
        wanted = {item.strip() for item in args.ids.split(",") if item.strip()}
        cases = [case for case in cases if case["id"] in wanted]
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        print("没有匹配的用例。")
        return 2

    if args.dry_run:
        print(f"用例数：{len(cases)}（来源 {args.cases}）")
        for case in cases:
            print(f"  {case['id']:30s} {case['language']:2s} {case['domain']:14s} 要点 {len(case['expected_points'])} 个")
        return 0

    settings = Settings()
    if not settings.has_llm:
        print("未配置大语言模型：评测需要模型才能产生有意义的基线。请先设置 LLM_API_KEY。")
        return 2
    if args.search_api:
        settings = settings.model_copy(update={"search_api": args.search_api})

    max_tasks = args.max_tasks or settings.max_tasks
    capabilities = LLMClient(settings).capabilities.as_dict()
    print(f"模型：{settings.llm_model_id} @ {settings.llm_base_url or '（默认端点）'}")
    print(f"搜索：{settings.search_api}    任务数：{max_tasks}    用例：{len(cases)}")
    print(
        "能力：结构化输出="
        f"{capabilities['supports_json_schema']}  推理模型={capabilities['is_reasoning']}  "
        f"temperature={capabilities['supports_temperature']}"
    )
    print()

    workspace_root = args.output_dir / "workspace"
    rows: list[dict] = []

    for index, case in enumerate(cases, start=1):
        case_settings = settings.model_copy(
            update={"workspace_dir": workspace_root / case["id"], "max_tasks": max_tasks}
        )
        print(f"[{index}/{len(cases)}] {case['id']} …", flush=True)
        try:
            row = asyncio.run(run_case(case, case_settings))
        except Exception as exc:  # a broken case must not abort the whole run
            row = {
                "id": case["id"],
                "topic": case["topic"],
                "error": f"{type(exc).__name__}: {exc}",
                "report_chars": 0,
                "degraded": False,
                "duration_s": 0,
                "tokens": {},
                "metrics": {},
            }
        rows.append(row)
        if row.get("error"):
            print(f"    失败：{row['error'][:100]}")
        else:
            metrics = row["metrics"]
            print(
                f"    coverage={metrics['coverage']:.2f} "
                f"citation_recall={metrics['citation_recall']:.2f} "
                f"groundedness={metrics['groundedness']:.2f} "
                f"({row['duration_s']:.0f}s, "
                f"{row['tokens'].get('prompt_tokens', 0)} in / {row['tokens'].get('completion_tokens', 0)} out tokens)"
            )

    capabilities = LLMClient(settings).capabilities.as_dict()
    observed = next((row["capabilities"] for row in rows if row.get("capabilities")), None)
    if observed and observed != capabilities:
        changed = [key for key in observed if observed[key] != capabilities.get(key)]
        print(f"运行中能力发生变化：{', '.join(changed)} —— 以实际观察到的为准")
        capabilities = observed

    result = {        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": {"id": settings.llm_model_id, "base_url": settings.llm_base_url, "capabilities": capabilities},
        "search_backend": settings.search_api,
        "max_tasks": max_tasks,
        "thresholds": {
            "support": DEFAULT_SUPPORT_THRESHOLD,
            "point": DEFAULT_POINT_THRESHOLD,
            "semantic": SEMANTIC_SUPPORT_THRESHOLD,
        },
        "aggregate": aggregate(rows),
        "cases": rows,
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_path = args.output_dir / f"run-{stamp}.json"
    run_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    print(format_rows(rows))
    print()
    summary = result["aggregate"]
    print(
        f"汇总（{summary['cases']} 条有效）：coverage={summary['coverage']:.3f} "
        f"citation_recall={summary['citation_recall']:.3f} "
        f"groundedness={summary['groundedness']:.3f} "
        f"citation_precision={summary['citation_precision']:.3f} "
        f"| 平均 {summary.get('mean_duration_s', 0)}s，共 "
        f"{summary.get('total_prompt_tokens', 0)} in / {summary.get('total_completion_tokens', 0)} out tokens"
    )
    if summary["degraded_excluded"] or summary["failed"]:
        print(f"已排除：降级 {summary['degraded_excluded']} 条，失败 {summary['failed']} 条（不计入均值）")
    print(f"结果已写入 {run_path}")

    if summary["cases"] == 0:
        print("没有产生任何有效的模型结果，基线未保存。请检查上面的模型/搜索配置。")
        return 1

    if args.label:
        baselines = EVAL_DIR / "baselines"
        baselines.mkdir(parents=True, exist_ok=True)
        baseline_path = baselines / f"{args.label}.json"
        baseline_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"基线已保存 {baseline_path}")

    if args.compare:
        print()
        print(compare_against(result, args.compare))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
