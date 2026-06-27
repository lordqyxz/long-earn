# Long Earn

自我进化的量化交易系统（v1.1.0）。基于 LangGraph 的证券交易顾问智能体，支持策略研发和股票分析。

## 系统概览

Long Earn 是一个 AI 驱动的量化交易研究平台，核心能力包括：

- **智能意图路由** — 自动识别用户查询意图，路由到策略研发或股票分析子图
- **策略研发** — 基于 Reflexion 框架的闭环策略研发：研究 → 开发 → 回测 → 反思 → 优化，支持多轮迭代和自适应知识检索
- **多视角股票分析** — 从巴菲特、查理芒格、彼得林奇、费雪四个投资大师视角并行分析股票
- **自我进化** — 策略经验自动沉淀到物质-运动统一架构记忆系统（ADR-007），后续研发可检索历史经验作为参考
- **内嵌回测引擎** — 事件驱动回测引擎直接集成在主项目中，通过 YAML DSL 描述策略，支持进程级并行回测 + 参数网格寻优

## 工作流

```
用户查询
  └─ 主图（意图识别 + 路由）
       ├─ 策略研究子图
       │    └─ 初始检索 → 自适应检索 → 研究 → 开发 → 回测
       │         └─ 回测成功 → 反思 → 保存经验 → 监督器 → 优化（循环）
       │         └─ 回测失败 → 代码修复 → 重新回测（最多 3 次）
       └─ 股票分析子图
            └─ 数据获取 → 四视角并行分析 → 汇总
                 ├─ 巴菲特视角（价值投资）
                 ├─ 查理芒格视角（多学科思维）
                 ├─ 彼得林奇视角（PEG 策略）
                 └─ 费雪视角（成长股投资）
```

## 快速开始

### 前置条件

- Python 3.13
- [uv](https://docs.astral.sh/uv/) 包管理器
- LLM 服务（默认 Ollama，也支持 DashScope / OpenAI 兼容 API）

### 安装

```sh
# 克隆仓库
git clone https://github.com/lordqyxz/long-earn.git
cd long-earn

# 安装依赖
uv sync

# 配置环境变量
cp .env.example .env
# 编辑 .env，至少配置 LLM 相关变量
```

### 运行

```sh
# 启动主项目
uv run python -m long_earn
```

### 常用命令

```sh
uv sync                                    # 安装依赖
uv run python -m long_earn                 # 运行项目
uv run pytest tests/ -v                    # 运行全部测试
uv run pytest tests/unit/ -v               # 仅运行单元测试
uv run pytest tests/integration/ -v        # 仅运行集成测试（需 .env 配置）
uv run ruff check .                        # 代码检查（lint + 复杂度）
uv run ruff format .                       # 代码格式化
uv run lint-imports                        # 架构依赖校验
```

> 类型检查用 Serena LSP（`mcp__serena__get_diagnostics_for_file`），不使用 mypy / pyright CLI。

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LLM_TYPE` | LLM 类型（ollama/dashscope/openai） | `ollama` |
| `LLM_MODEL` | 模型名称 | `deepseek-v4-flash:cloud` |
| `LLM_BASE_URL` | LLM API 地址 | `http://localhost:11434` |
| `DASHSCOPE_API_KEY` | 阿里百炼 API Key（LLM_TYPE=dashscope 时必填） | — |
| `OPENAI_API_KEY` | OpenAI API Key（LLM_TYPE=openai 时必填） | — |
| `MEMORY_PATH` | 记忆持久化路径（物质-运动架构，JSONL） | `~/.long_earn/substances.jsonl` |
| `INIT_DIR` | 知识库初始化目录 | `./init` |
| `BACKTEST_START_DATE` | 回测默认起始日期 | `2020-01-01` |
| `BACKTEST_END_DATE` | 回测默认结束日期 | `2023-12-31` |
| `MAX_ITERATIONS` | 策略研发最大迭代次数 | `3` |
| `STRATEGY_KEYWORDS` | 策略研究路由关键词（逗号分隔） | `策略,思路,投资策略` |
| `STOCK_ANALYSIS_KEYWORDS` | 股票分析路由关键词（逗号分隔） | `股票,分析,公司` |

## 架构

系统采用依赖注入架构，所有服务通过 `RuntimeContext` 统一传递：

```
AppConfig.from_env()
    ↓
create_runtime_context(config) / initialize_context(config)
    ↓
RuntimeContext
    ├── llm_service        — LLM 调用
    ├── memory_service     — 3-Tier 记忆系统（检索与存储）
    ├── stock_service      — 股票数据获取
    ├── backtest_service   — 内嵌回测引擎
    ├── logger             — 日志记录
    ├── monitoring         — 性能监控
    └── config             — 应用配置
```

服务接口定义为 `Protocol` 类，可在测试中轻松替换为 Mock 实现。

### 策略研究子图

基于 [Reflexion](https://www.promptingguide.ai/zh/techniques/reflexion) 框架，闭环迭代优化策略：

1. **自适应检索** — 根据查询自动从记忆系统检索相关信息，评估是否需要补充检索
2. **策略研究** — LLM 结合知识上下文生成投资策略
3. **策略开发** — 将策略转化为 YAML DSL 策略描述
4. **回测** — 通过内嵌事件驱动回测引擎执行回测，失败时自动修复代码（最多 3 次）
5. **反思** — 基于 Tree of Thought 多分支反思，分析回测结果并提出改进方向
6. **保存经验** — 将策略和反思沉淀到 3-Tier 记忆系统
7. **监督器** — 评估是否继续迭代（终止条件：夏普比率 ≥ 1.5）

### 股票分析子图

从四个投资大师视角并行分析股票内在价值，通过 miniqmt (xtquant) 获取股票和财务数据。

### 回测引擎（事件驱动，内嵌）

回测引擎已整合到主项目中（`src/long_earn/backtest/`），通过 YAML DSL 描述策略，无需 HTTP 远程调用。采用事件驱动架构（ADR-005），杜绝未来函数，支持状态化策略与动态风控。

**引擎结构：**

```txt
src/long_earn/backtest/
├── models.py                # BacktestResult Pydantic 模型
├── domain/
│   ├── entities.py          # 领域实体（Portfolio、Event、Order）
│   └── exceptions.py        # 领域异常层次
├── engine/
│   ├── core.py              # 事件驱动回测引擎（T 维度迭代 × S 维度向量化）+ Walk-Forward
│   ├── dsl.py               # YAML DSL 解析器（StrategyDSL + 字段校验）
│   ├── evaluator.py         # AST 白名单表达式求值器（不使用 eval）
│   ├── broker.py            # 模拟撮合（滑点 / 佣金 / 印花税）+ 高级订单类型
│   ├── portfolio.py         # 投资组合管理（信号→订单→成交→持仓）
│   ├── visibility.py        # 可见性守护（杜绝未来函数）
│   ├── audit.py             # DuckDB 审计存储 + 因果链追踪
│   ├── telemetry.py         # 可观测性（span 链路追踪）
│   ├── ml_strategy.py       # ML 策略基类 + 特征工程 + 技术指标
│   ├── strategy_templates.py # 策略模板库（双均线 / RSI 均值回归 / MACD 柱）
│   ├── parallel.py          # 进程级并行编排（SharedMemory 零拷贝分发）
│   ├── param_grid.py        # 参数网格（笛卡尔积 / 显式组合 + 标量插值）
│   └── shared_data.py       # 共享数据底座（Arrow IPC + SharedMemory）
├── operators/              # 算子框架（factor/filter/rank/compose + 因果检测）
└── data/
    ├── cache.py             # DuckDB 本地缓存（行情 / 财务 / 成分股）
    ├── provider.py          # DataProvider Protocol + CompositeDataProvider 多源降级
    ├── miniqmt_provider.py  # miniqmt (xtquant) 数据获取
    ├── akshare_provider.py  # akshare fallback 数据获取
    └── universe.py          # 股票池管理（全 A / csi300 / csi500 / 板块等）
```

- **YAML DSL 策略**：LLM 生成 YAML 策略描述（因子、信号、权重、风控），引擎解析后执行
- **状态化策略**：LLM 可生成定义 `init()` 和 `on_bar()` 的状态机逻辑，引擎通过事件流驱动执行
- **数据隔离**：策略仅能通过 `engine.current_data` 访问当前时刻数据，确保回测真实性
- **并行回测**：进程级并行编排（`parallel.py`），SharedMemory 零拷贝分发数据，参数网格自动寻优
- **Walk-Forward OOS**：时序交叉验证，防止过拟合
- **股票池**：支持全 A / csi300 / csi500 / main_board / gem / star_board 及组合
- **DuckDB 缓存**：`~/.long_earn/backtest_cache.duckdb`，多源降级：DuckDB → miniqmt → akshare

### 交易日志存储、导出与可视化

回测引擎执行时会将完整交易日志（时间、标的、方向、价格、数量、金额、持仓市值）持久化到 DuckDB，并提供数据导出和 Web 可视化功能。

**数据存储**

回测执行时，引擎通过 `DuckDBAuditProvider` 将以下事件写入 DuckDB 的 `backtest_audit.logs` 表：

| 事件类型 | 记录内容 |
|---------|---------|
| `FILL` | 成交记录（标的、方向、价格、数量、金额、持仓市值） |
| `ORDER` | 订单请求（标的、方向、数量） |
| `SIGNAL` | 策略信号（目标权重） |
| `MARKET_DATA` | 每个 bar 的组合市值快照（用于权益曲线） |

数据库位置：`.cache/backtest_cache.duckdb`（与行情缓存同库，独立 schema）。

**Web 可视化仪表盘**

启动可视化服务：

```sh
uv run python -m long_earn.dashboard.api
```

浏览器访问 `http://localhost:8090` 即可查看仪表盘，包含：

- 权益曲线、日收益率分布、事件分布
- 交易明细表（时间 / 标的 / 方向 / 价格 / 数量 / 持仓市值）
- **交易标的图表**：为每只交易过的标的绘制价格走势图，并在图上标注买入（绿色 ▲）和卖出（红色 ▼）时间点，鼠标悬停显示价格、数量、金额
- 风险指标（年化收益、夏普比率、最大回撤、VaR/CVaR）
- 多策略对比

**REST API 导出**

| 端点 | 说明 |
|------|------|
| `GET /api/runs` | 列出所有回测运行 |
| `GET /api/runs/{run_id}/symbols` | 该次回测交易过的标的列表 |
| `GET /api/runs/{run_id}/trades` | 交易日志（JSON） |
| `GET /api/runs/{run_id}/export?format=csv` | 导出交易日志为 CSV 文件下载 |
| `GET /api/runs/{run_id}/export?format=json` | 导出交易日志为 JSON 文件下载 |
| `GET /api/runs/{run_id}/symbol/{symbol}/chart` | 单只标的的价格走势 + 买卖点标注数据 |
| `GET /api/runs/{run_id}/symbol_charts` | 全部交易标的的图表数据 |

**代码调用导出**

```python
from long_earn.dashboard.analyzer import BacktestAnalyzer

analyzer = BacktestAnalyzer()

# 导出交易日志到文件
analyzer.export_trade_traces_to_file("run_id", "trades.csv", fmt="csv")
analyzer.export_trade_traces_to_file("run_id", "trades.json", fmt="json")

# 获取单只标的的价格走势 + 买卖点（用于自定义可视化）
chart_data = analyzer.export_symbol_chart_data("run_id", "600000.SH")
# chart_data = {"symbol": "600000.SH", "price_history": [...], "trade_points": [...]}

# 获取所有交易标的的图表数据
all_charts = analyzer.export_all_symbol_charts("run_id")
```

### 记忆系统

基于物质-运动统一架构（ADR-007），事件/关系/知识/策略经验统一为 `Substance`（Pydantic）：

```txt
src/long_earn/substance/
├── store.py                 # SubstanceStore（统一存储 + 双索引协调）
├── model.py                 # Substance(Pydantic) + SubstanceForm + FilterLogic
├── motion.py                # 运动层（activate/decay/conflict/compress）
├── persistence.py           # JSONL 读写（无 pickle，有 schema 版本号）
└── indices/
    ├── retrieval.py         # RetrievalIndex（keyword 通道 + semantic 通道 + 融合）
    └── graph.py             # GraphIndex（dict 邻接表 + BFS 返回路径）
```

- **物质 (Substance)**：统一存在基类，`form` 区分 event/relation/knowledge/strategy/backtest
- **运动 (motion)**：施加在物质上的运算（activate/decay/conflict/compress），不持久化，只产出新物质
- **双索引**：RetrievalIndex（WorldInfo 关键词触发 + TF-IDF/embedding 语义相似度双通道融合）+ GraphIndex（邻接表图遍历）
- **持久化**：`~/.long_earn/substances.jsonl`（JSONL，无 pickle，有 schema 版本号）
- **防未来函数**：`visible_from` 字段，回测引擎查询时仅 `visible_from ≤ current_bar_date` 的物质可见

> 旧 `memory/` 模块（ADR-004 v2.0）已删除，详见 [ADR-007](docs/adr/007-unified-substance-architecture.md)。

## 技术栈

| 类别 | 技术 |
|------|------|
| 语言 | Python 3.13 |
| 工作流框架 | LangGraph |
| LLM | Ollama（默认）/ DashScope / OpenAI 兼容 API |
| 回测引擎 | 自研事件驱动回测引擎（Polars + NumPy + DuckDB） |
| 记忆系统 | 物质-运动统一架构（Substance + 双索引 + JSONL） |
| 数据缓存 | DuckDB |
| 证券数据 | miniqmt (xtquant) → akshare（Composite 多源降级） |
| Web 搜索 | Kimi Web Search / Tavily |
| 日志 | loguru |
| 包管理 | uv |

## 测试

```sh
# 单元测试（无需 API 调用）
uv run pytest tests/unit/ -v

# 集成测试（需要 .env 配置）
uv run pytest tests/integration/ -v

# 代码格式化和检查
uv run ruff check . && uv run ruff format .
```

## 知识库

系统启动时自动加载 `init/` 目录下的文档到 3-Tier 记忆系统，支持 .md、.txt、.py 格式。策略研发过程中自动检索相关知识，成功的策略经验也会沉淀回记忆系统，实现系统的自我进化。

## 许可

MIT