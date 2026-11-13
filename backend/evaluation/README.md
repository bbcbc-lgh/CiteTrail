# 评测集

这是 CiteTrail 唯一的"质量尺子"。没有它，prompt 调整、模型更换、检索策略变更都无法判断是变好还是变坏。

## 用法

```bash
cd backend

# 校验用例集，不调用模型
python -m evaluation.run_eval --dry-run

# 跑完整集合（24 条，约 1 小时，会产生真实 API 费用）
python -m evaluation.run_eval

# 先跑两条看看
python -m evaluation.run_eval --limit 2

# 指定用例 / 临时覆盖检索后端
python -m evaluation.run_eval --ids zh-tech-sse,zh-tech-mcp --search-api tavily

# 任何 Settings 字段都可以用环境变量临时覆盖，例如对比"关闭正文抓取"
$env:FETCH_PAGES="false"; python -m evaluation.run_eval --limit 2

# 保存为基线，并与已有基线对比
python -m evaluation.run_eval --label baseline-v2
python -m evaluation.run_eval --compare evaluation/baselines/baseline-v1-2cases.json
```

产物：

| 路径 | 说明 |
|---|---|
| `evaluation/cases.json` | **用例集本身，需要提交** |
| `evaluation/baselines/<label>.json` | 人工确认过的基线，需要提交 |
| `outputs/eval/run-<时间戳>.json` | 单次运行明细（含逐条 claim），本地留存，不进版本库 |
| `outputs/eval/workspace/<case>/` | 各用例的研究笔记、报告与链路 JSON |

## 指标

| 指标 | 定义 |
|---|---|
| `coverage` | 报告中出现的"应覆盖要点" / 全部要点 |
| `citation_recall` | 被**某条有支撑的结论**覆盖的要点 / 全部要点 |
| `groundedness` | 有支撑的结论 / **可测**结论 |
| `citation_precision` | 有支撑的结论 / 可测且有引用的结论 |
| `cited_claim_rate` | 有引用的结论 / 全部结论 |
| `measurable_rate` | 可测结论 / 全部结论 |

## 三个必须知道的局限

**1. 支撑度优先来自语义核查，词法是回退。**
每条结论的 `support_score` 由 `Infrastructure/Verification.py` 的核查智能体给出（0–1 分），评测直接使用这个分数。只有当结论没有被核查过（例如关闭了 `VERIFY_CLAIMS`）时，才退回词法判断。核查智能体只看被引用的来源摘录，并且被明确要求不得使用自身知识。

**2. 跨语言结论按"不可测"处理，不计入分母。**
中文技术主题大量依赖英文来源。词法回退时翻译后的结论只有专有名词能逐字对上，所以少于 2 个可比较词的结论记为不可测——否则每个高质量的跨语言回答都会被算成 0 分。接上语义核查后这类结论会变成可测，`measurable_rate` 应随之接近 1.0。

看分数时请同时看 `measurable_rate`：它低时 `groundedness` 的参考价值有限。

**3. 降级运行不计入均值。**
没有模型时走确定性回退，回退报告直接由 snippet 生成结论并引用该 snippet，因此 `groundedness` 必然是满分——那是假象。`aggregate` 会排除 `degraded=True` 与失败的用例，并在汇总里单独报告排除数量。

## 阈值

| 常量 | 默认 | 位置 |
|---|---|---|
| `DEFAULT_SUPPORT_THRESHOLD` | 0.35 | 词法回退时，结论与其引用来源的词覆盖比 |
| `SEMANTIC_SUPPORT_THRESHOLD` | 0.5 | 语义核查分数达到此值才算"有支撑" |
| `DEFAULT_POINT_THRESHOLD` | 0.6 | 要点在文本中的词覆盖比 |
| `MIN_MEASURABLE_TERMS` | 2 | 低于此值记为不可测 |

阈值刻意写在模块常量里而不是散落在代码中，调参时只需要改一处，并且会被写进每次运行的 `thresholds` 字段。

## 用例集的结构

```json
{
  "id": "zh-tech-sse",
  "language": "zh",
  "domain": "software",
  "topic": "SSE 长连接断线后应如何设计与实现重连机制？",
  "expected_points": ["心跳", "Last-Event-ID", "指数退避", "资源释放"]
}
```

`expected_points` 要写成**短名词短语**而不是完整句子：指标按词覆盖计算，长句会引入大量无关词，让 0.6 的阈值失去意义。

当前 24 条，全部为**中文主题**：12 条技术类 + 12 条一般议题，覆盖 12 个领域。断言数量少是刻意的——一条用例一次全流程，规模越大越没有人愿意跑。

### 为什么不测英文主题

CiteTrail 的定位是中文用户、本地运行的个人工具，不追求英文提问返回英文报告。评测集因此只覆盖中文主题，避免测一个不打算做的能力。

但**英文来源仍在测试范围内**：`zh-tech-*` 这些技术主题大量检索到英文文档，中文结论引用英文证据是常态。这正是引用核查智能体存在的原因——词法匹配在这种情况下几乎必然失效，只有语义判断才能回答"这条中文结论是否被那段英文来源支持"。

## 什么时候必须重跑

- 改 prompt
- 换模型或改能力配置
- 改检索 / 排序 / 清洗逻辑
- 改报告结构或引用编号

步骤 6（检索通用化）、步骤 7（全文抓取）、步骤 8（引用校验）都应以"评测分数不低于基线"作为验收条件。
