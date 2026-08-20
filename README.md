# CiteTrail

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

CiteTrail 是一个可本地运行的深度研究工作台：输入一个研究主题，后端会拆分任务、检索公开来源、整理证据并生成带引用的 Markdown 报告；前端通过 SSE 实时展示研究进度。

## 功能

- Vue 3 + Vite 研究工作台
- FastAPI 流式研究接口
- 可选的 OpenAI 兼容大语言模型
- DuckDuckGo、Tavily、Perplexity 和 SearXNG 搜索后端
- 自动保存任务笔记和 Markdown 报告
- 未配置 LLM 时仍可使用本地确定性回退逻辑运行完整流程

## 环境要求

- Python 3.10+
- Node.js 18+
- npm

## 快速开始

### 1. 启动后端

```bash
cd backend
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
```

macOS/Linux：

```bash
source .venv/bin/activate
```

安装依赖并创建配置文件：

```bash
pip install -r requirements.txt
cp .env.example .env
```

Windows PowerShell 请使用：

```powershell
Copy-Item .env.example .env
```

启动 API：

```bash
uvicorn src.main:app --reload --host 127.0.0.1 --port 8000
```

后端地址：<http://127.0.0.1:8000>

健康检查：<http://127.0.0.1:8000/health>

### 2. 启动前端

在另一个终端执行：

```bash
cd frontend
npm install
npm run dev
```

打开 <http://127.0.0.1:5173>，输入至少 3 个字符的研究主题即可开始。前端开发服务器会将 `/api` 和 `/health` 请求代理到本地后端。

## 配置

所有后端配置位于 `backend/.env`。可直接从 `backend/.env.example` 开始；不要提交包含密钥的 `.env` 文件。

### LLM（可选）

如果希望使用模型进行规划、任务总结和报告撰写，请填写 OpenAI 兼容服务：

```dotenv
LLM_API_KEY=your-api-key
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL_ID=gpt-4o-mini
```

`LLM_BASE_URL` 可替换为其他 OpenAI 兼容服务的地址。留空 `LLM_API_KEY` 时，应用使用本地回退逻辑。

### 搜索后端

通过 `SEARCH_API` 设置优先使用的后端：

| 值 | 需要的配置 |
| --- | --- |
| `duckduckgo` | 无需 API 密钥 |
| `tavily` | `TAVILY_API_KEY` |
| `perplexity` | `PERPLEXITY_API_KEY` |
| `searxng` | `SEARXNG_URL` |

如果首选后端不可用，应用会按 `tavily`、`perplexity`、`duckduckgo`、`searxng` 的顺序尝试其他后端。常用的流程参数还包括 `SEARCH_RESULT_LIMIT`、`SEARCH_TIMEOUT`、`MAX_TASKS` 和 `MAX_WEB_RESEARCH_LOOPS`。

## API

### 健康检查

```http
GET /health
```

响应会包含当前 LLM 是否已配置以及首选搜索后端。

### 启动研究

```http
POST /api/research/stream
Content-Type: application/json

{"topic":"研究主题"}
```

接口返回 `text/event-stream`。事件类型包括：

`progress`、`plan`、`task_started`、`search_results`、`task_summary`、`report`、`error`、`done`。

例如使用 curl 查看原始事件流：

```bash
curl -N http://127.0.0.1:8000/api/research/stream \
  -H "Content-Type: application/json" \
  -d '{"topic":"人工智能在教育中的应用"}'
```

## 输出文件

每次研究会在 `backend/workspace/` 下保存任务笔记，并在 `backend/workspace/reports/` 下保存最终 Markdown 报告。`workspace/` 已加入 `.gitignore`，这些运行产物不会被提交到仓库。

## 开发与测试

后端单元测试：

```bash
cd backend
python -m unittest discover -s tests
```

前端生产构建：

```bash
cd frontend
npm run build
```

主要目录：

```text
backend/src/Agent/           规划、总结和报告 Agent
backend/src/Tool/            搜索与笔记工具
backend/src/WorkFlow/        研究流程编排
backend/src/Infrastructure/  LLM、搜索、配置和文件存储适配器
backend/src/Schema/          API 与流程数据模型
frontend/src/                Vue 页面、组件和 SSE 客户端
```

## 参与贡献

1. Fork 本仓库并创建分支。
2. 在修改对应目录中安装依赖并运行测试或构建。
3. 提交清晰、聚焦的变更，并在 Pull Request 中说明验证方式。

欢迎提交 Issue 报告问题或提出功能建议。涉及搜索服务或模型服务时，请不要在 Issue、日志或 Pull Request 中公开 API 密钥。

## 许可证

本项目基于 [MIT License](LICENSE) 发布。
