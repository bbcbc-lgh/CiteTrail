# 自动化深度研究智能体

一个独立的全栈深度研究应用。它将研究主题拆分为多个任务，搜索公开来源，提炼证据，并生成带引用的 Markdown 报告。研究过程通过 SSE 实时呈现在 Vue 前端。

## 研究流程

```text
研究主题
  → 研究规划
  → 搜索
  → 任务总结
  → 保存研究笔记
  → 生成 Markdown 报告
  → SSE 事件流 → 研究工作台
```

研究流程位于 `backend/src/WorkFlow/ResearchWorkFlow.py`，负责规划、搜索、总结、报告和研究产物保存的编排。

## 后端结构

```text
src/
├─ Agent/           Agent 基类与规划、总结、报告 Agent
├─ Tool/            搜索和笔记工具
├─ WorkFlow/        研究流程编排
├─ Infrastructure/  模型、搜索、存储和配置适配器
├─ Schema/          Pydantic 数据契约
└─ main.py          FastAPI 入口
```

`WorkFlow` 负责流程，`Agent` 负责推理，`Tool` 负责能力入口，`Infrastructure` 负责外部系统，`Schema` 只负责数据结构。

## 启动后端

在 `backend` 目录中使用 Python 3.10 或更高版本执行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn src.main:app --reload --host 127.0.0.1 --port 8000
```

未配置大语言模型时，系统会使用本地的确定性规划、总结和报告回退逻辑。

## 搜索配置

`SEARCH_API` 用于选择优先搜索后端：

- `duckduckgo`：无需密钥
- `tavily`：需要 `TAVILY_API_KEY`
- `perplexity`：需要 `PERPLEXITY_API_KEY`
- `searxng`：需要 `SEARXNG_URL`

服务会先尝试 `SEARCH_API` 指定项；失败后按 `tavily`、`perplexity`、`duckduckgo`、`searxng` 的固定顺序尝试其余后端。若 `SEARCH_API` 为空或未识别，则直接按该固定顺序搜索。

## 启动前端

在 `frontend` 目录中执行：

```powershell
npm install
npm run dev
```

浏览器访问 `http://127.0.0.1:5173`。开发服务器会将 `/api` 请求代理到 `http://127.0.0.1:8000`。

## SSE 接口

`POST /api/research/stream`：

```json
{"topic": "研究主题"}
```

接口返回 `text/event-stream`，事件类型包括 `progress`、`plan`、`task_started`、`search_results`、`task_summary`、`report`、`error` 和 `done`。最终报告与任务笔记保存至 `backend/workspace/`。
