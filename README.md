# CiteTrail

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

CiteTrail 是一个可本地运行的深度研究工作台：输入一个研究主题，后端会拆分任务、检索公开来源、整理证据并生成带引用的 Markdown 报告；前端通过 SSE 实时展示研究进度。

## 功能

- Vue 3 + Vite 研究工作台
- FastAPI 流式研究接口
- 可选的 OpenAI 兼容大语言模型
- 博查、智谱、Tavily 搜索后端（国内优先）
- 抓取搜索结果正文、按查询切块，并对每条结论做引用核查
- 自动保存任务笔记、Markdown 报告和研究链路 JSON
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

通过 `SEARCH_MODE` 选择检索方式，用 `SEARCH_FUSION_BACKENDS` 指定参与融合的后端：

| 值 | 需要的配置 | 实测状态 | 说明 |
| --- | --- | --- | --- |
| `bocha` | `BOCHA_API_KEY` | ✅ 可用 | 博查 AI 搜索，响应约 0.9s |
| `zhipu` | `ZHIPU_API_KEY` | ✅ 可用 | 智谱联网搜索 |
| `baidu` | `BAIDU_API_KEY` | ✅ 可用 | 百度千帆 AI 搜索，中文网页覆盖较全 |
| `deepseek` | `DEEPSEEK_API_KEY` | ✅ 可用 | 返回 `rerank_score`、`authority` 与 600–800 字摘要 |
| `metaso` | `METASO_API_KEY` | ✅ 可用 | 秘塔搜索 |
| `tavily` | `TAVILY_API_KEY` | ✅ 可用 | 海外服务，默认作为兜底 |

```dotenv
SEARCH_MODE=fusion                          # fusion | fallback
SEARCH_FUSION_BACKENDS=bocha,zhipu,baidu,deepseek,metaso
SEARCH_API=tavily                            # 兜底后端
```

> 火山引擎与阿里百炼**没有接入**：两者的联网搜索都不是独立搜索接口——火山把搜索挂在 chat 的工具调用下（无独立端点），阿里只返回生成的答案而不暴露来源列表。对以"引用可核验"为目标的本项目来说，没有来源就等于不可用。

**`fusion`（默认）**：并行查询所有已配置的融合后端，按 URL 去重合并，再统一排序。同一页面被两个独立索引命中的，会获得排序加成——这是"多来源佐证"真正成立的地方。

**`fallback`**：按顺序尝试，直到某个后端返回结果。

两种模式都不会调用未配置密钥的后端。融合全部落空时会降级到 `SEARCH_API`，所以即使国内后端全挂，研究仍能跑完。

同一轮研究里，某个后端一旦出现鉴权失败、连接失败或 5xx，本次研究后续不再重试它。

### 正文抓取与引用核查

```dotenv
FETCH_PAGES=true        # 抓取搜索结果正文；关闭后只能使用搜索摘要
FETCH_TIMEOUT=12
FETCH_MAX_SOURCES=4     # 每个子任务最多抓几个页面
CHUNKS_PER_SOURCE=2     # 每个来源最多送入模型几个片段
VERIFY_CLAIMS=true      # 为每条结论核对引用来源是否真的支持它
VERIFY_MAX_CLAIMS=12    # 每个子任务最多核查多少条结论
```

两个开关都直接影响成本：关闭 `FETCH_PAGES` 会退回"只读摘要"的模式，证据明显变薄；关闭 `VERIFY_CLAIMS` 可省下约一半 token，但报告里不再有支撑度判断。

### 反思补检

如果某个子任务检索到的证据明显不足（没有来源、结论过少、或多数结论缺乏支撑），系统会再生成一批检索式补检一轮：

```dotenv
MAX_WEB_RESEARCH_LOOPS=1   # 额外补检轮数；0 关闭
REFLECTION_MAX_QUERIES=2   # 每轮最多生成几条新检索式
```

每多一轮意味着一次额外的检索、抓取、总结和核查，成本会明显上升。之所以默认只给一轮，是因为补检只应在证据明显不足时触发，而不是每次都想"再多查一点"。

### 并发

```dotenv
TASK_CONCURRENCY=3   # 同时推进几个子任务；1 表示串行
```

子任务大部分时间在等网络和模型响应，并发能把总耗时压到原来的三分之一左右。研究进度仍按"已完成 / 总数"推进，面板会同时标出正在进行中的几个步骤。

## API

### 健康检查

```http
GET /health
```

响应会包含当前 LLM 是否已配置、模型名称与能力表（是否支持结构化输出、是否推理模型、上下文长度等），以及每个搜索后端是否已配置。注意 `configured` 只表示"配置项存在"，不代表服务可用——密钥过期或本地服务未启动仍然会失败，失败会在研究日志里体现。

模型能力以 `LLM_*` 配置项覆盖，应用在运行时会按服务端实际响应自动降级。

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

## 历史记录

每次研究都会写入 `backend/workspace/runs.db`（SQLite），记录主题、状态、耗时产物和计数：

```http
GET /api/research?limit=20        # 历史列表，时间倒序
GET /api/research/{research_id}   # 单条详情，含报告 Markdown
```

这样关掉页面不会丢结果，也方便把历史接到别的界面上。

## MCP 服务

`backend/src/Mcp` 把检索能力包装成标准 MCP server，供 Claude Desktop、Cursor 等宿主调用：

```bash
cd backend
python -m src.Mcp        # stdio 传输，按行读写 JSON-RPC
```

暴露三个工具：

| 工具 | 作用 |
| --- | --- |
| `search_web` | 用配置好的多个搜索引擎检索，返回标题、链接、摘要和命中的引擎 |
| `list_research` | 列出本地研究记录 |
| `get_report` | 按编号取回 Markdown 报告 |

## 输出文件

每次研究会在 `backend/workspace/` 下保存任务笔记，并在 `backend/workspace/reports/` 下保存最终 Markdown 报告。`workspace/` 已加入 `.gitignore`，这些运行产物不会被提交到仓库。

## 开发与测试

后端单元测试：

```bash
cd backend
pip install -r requirements-dev.txt
ruff check .
python -m unittest discover -s tests
```

前端生产构建（含 TypeScript 类型检查）：

```bash
cd frontend
npm run build
```

仓库自带 GitHub Actions（`.github/workflows/ci.yml`）：每次推送或 PR 跑后端 lint + 单元测试、前端构建；**评测不挂在提交上**，只在手动触发时跑两条冒烟用例，因为它会消耗真实 API 额度。

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
