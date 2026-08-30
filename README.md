# Long Earn

自我进化的量化交易系统。基于 LangGraph 的证券交易顾问智能体，支持策略研发、股票分析和实时行情监控。

## 项目简介

Long Earn 是 AI 驱动的量化交易研究平台，核心能力：

- **分层智能体编排** — MasterAgent（ReAct）负责任务分解与跨能力调度（ADR-016）
- **ToG 策略研发飞轮** — ResearchAgent 在 Substance/Ontology 上 explore→prune，以回测与统计门为不可跳过证据，写回经验形成飞轮（ADR-018）；HTR 假设树保留为 beam 谱系/状态存储，降为脚手架
- **多视角股票分析** — 巴菲特 / 芒格 / 彼得林奇 / 费雪 / 资金流向五视角并行分析（ADR-012）
- **事件图谱基础设施** — `prepare_context` 自动激活事件上下文；缺省时由 agent 层显式触发采集推理（ADR-007 / ADR-018）
- **内嵌回测引擎** — 事件驱动引擎直接集成在主项目中，YAML DSL + 算子目录描述策略，支持进程级并行回测（ADR-005 / ADR-009）
- **实时行情监控** — 实时行情 Provider（显式主源 miniqmt，次源 ciccwm）+ 价格阈值告警（ADR-011 / ADR-018）

开发规范与铁律约束见 [AGENTS.md](AGENTS.md)。

## 快速开始

### 前置条件

- Python 3.13
- [uv](https://docs.astral.sh/uv/) 包管理器
- LLM 服务（默认 DeepSeek，也支持 Ollama / DashScope / OpenAI 兼容 API）
- PostgreSQL（回测缓存、审计、物质库统一存储，见 `PG_*` 环境变量）

### 安装

```sh
git clone https://github.com/lordqyxz/long-earn.git
cd long-earn
uv sync
cp .env.example .env   # 编辑 .env，至少配置 LLM 相关变量
```

### 运行

```sh
# 查看 CLI 子命令
uv run python -m long_earn --help

# 主智能体对话（MasterAgent ReAct）
uv run python -m long_earn agent "分析某只股票"

# 策略研发循环（ResearchAgent ToG 飞轮）
uv run python -m long_earn research "基于 ROE 的选股策略"

# 启动 FastAPI 后端（回测看板 / REST API / WebSocket，默认 8090）
uv run python -m long_earn web
```

前端开发见 [web/README.md](web/README.md)：`web/` 目录为 React + Vite SPA，开发时 `npm run dev`（5173），代理 `/api` 与 `/ws` 到后端 8090。

### 常用命令

```sh
uv sync                                              # 安装依赖
uv run python -m long_earn                           # CLI 入口（等价 long-earn）
uv run pytest tests/unit/ -v                         # 单元测试
uv run pytest tests/integration/ -v                  # 集成测试（需 .env）
uv run ruff check . && uv run ruff format .          # 代码检查与格式化
uv run pyright src/                                  # 静态类型检查（首要关口）
uv run lint-imports                                  # 架构依赖校验
uv run python scripts/check_llm_call_sites.py        # LLM 调用分层卡口（ADR-021）
uv run python scripts/download_data.py               # 全量下载行情/财务到 PostgreSQL 缓存
```

## 环境变量

完整清单与默认值以 `AppConfig` docstring 为单一真相源，见 [`src/long_earn/config.py`](src/long_earn/config.py)。

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LLM_TYPE` | LLM 类型（deepseek / ollama / dashscope / openai） | `deepseek` |
| `LLM_MODEL` | 模型名称 | `deepseek-v4-flash` |
| `LLM_BASE_URL` | LLM API 地址 | `https://api.deepseek.com/v1` |
| `LONG_EARN_DATA_DIR` | 统一数据根目录（假设树、策略产物等派生路径） | `D:/dev/long-earn-data` |
| `INIT_DIR` | 知识库初始化目录（首次启动导入 Substance） | `./init` |
| `PG_HOST` / `PG_PORT` / `PG_DB` / `PG_USER` / `PG_PASSWORD` | PostgreSQL 连接（缓存 / 审计 / 物质库） | 见 `.env.example` |

按 `LLM_TYPE` 配置对应 API Key（`DEEPSEEK_API_KEY`、`DASHSCOPE_API_KEY`、`OPENAI_API_KEY` 等），详见 `.env.example`。

## 架构

运行时总览与调用图见 [docs/architecture.md](docs/architecture.md)；架构决策索引见 [docs/adr/README.md](docs/adr/README.md)。

**依赖注入**：所有 Agent 与子图通过 `RuntimeContext` 初始化（`create_runtime_context()` / `initialize_context()`），禁止无 context 构造。服务接口定义为 `Protocol`，测试中用 Mock 替换。

**ToG 飞轮（ADR-018）**：ResearchAgent 在 Ontology/Substance 上 expand→prune→算子开发→YAML 编译→训练集回测→OOS 统计门→写回经验；回测与 `prove_causality` 因果证明为不可跳过的硬约束。

**数据层三组接口（ADR-018，无静默降级）**：

| 能力 | 接口 | 数据源 |
|------|------|--------|
| 历史面板 | `DataConnector` | PostgreSQL Cache + 显式主源 miniqmt |
| 市场情报 | `MarketIntelligenceProvider` | ciccwm 独占 |
| 实时行情 | `RealtimeDataProvider` | 主源 miniqmt；不可用时显式切换 ciccwm |

**策略 DSL（ADR-009）**：策略走算子目录（factor / filter / rank / compose + 因果检测），已退役 AST `evaluator.py` 及旧式 `factors` / `filter` / `rank` / `expression` 字段。

## 技术栈

| 类别 | 技术 |
|------|------|
| 语言 | Python 3.13 |
| 工作流框架 | LangGraph |
| LLM | DeepSeek（默认）/ Ollama / DashScope / OpenAI 兼容 API |
| 回测引擎 | 自研事件驱动引擎（Polars + NumPy） |
| 统一存储 | PostgreSQL（行情缓存 / 回测审计 / Substance 物质库，ADR-019） |
| 记忆系统 | 物质-运动统一架构（Substance + 双索引检索，ADR-007） |
| 证券数据 | miniqmt（面板主源）· ciccwm（情报）· 实时显式主/次源切换 |
| Web | FastAPI 后端 + React/Vite 前端（`web/`） |
| 日志 | loguru |
| 包管理 | uv |

## 测试

```sh
uv run pytest tests/unit/ -v          # 单元测试（无需外部 API）
uv run pytest tests/integration/ -v   # 集成测试（需 .env 配置）
```

## 知识库（`init/`）

系统初始化时，`MemoryService` 从 `INIT_DIR`（默认 `./init`）加载 `.md` / `.txt` / `.py` 文档到 **Substance** 物质库（PostgreSQL 持久化）。PG 已有数据时优先从 PG 加载；策略研发过程中通过 WorldInfo 关键词 + 语义相似度双通道检索，成功经验写回 Substance。

## 许可

MIT
