# Long Earn

自我进化的量化交易系统（v2.0.0）。基于 LangGraph 的证券交易顾问智能体，支持策略研发、算子自进化、股票分析和回测可视化。

## 系统概览

Long Earn 是一个 AI 驱动的量化交易研究平台，核心能力包括：

- **智能意图路由** — 自动识别用户查询意图，路由到策略研发或股票分析子图
- **策略研发** — 基于 Reflexion 框架的闭环策略研发：自适应检索 → 研究 → 开发 → 回测 → 反思 → 优化 → 多轮迭代
- **算子框架** — 可插拔的 5 类算子（factor / filter / rank / compose / technical），因果性硬约束（无未来函数），LLM 可直接生成新算子
- **算子自进化** — 算子研发子图异步消费 backlog，AI 实现 → 因果性证明 → 注册上线，策略引擎会自动使用新算子
- **策略优化流水线** — 基线策略 → 优化 → 回测验证 → 验收门槛，可累积多轮演进谱系
- **多视角股票分析** — 从巴菲特、查理芒格、彼得林奇、费雪四个投资大师视角并行分析股票
- **事件驱动回测引擎** — T 维迭代 × S 维向量化，包含 Broker / Portfolio / VisibilityGuard / ML Strategy，DuckDB 审计追踪
- **回测可视化仪表盘** — RESTful API + HTML 前端，权益曲线 / 交易日志 / 风险指标 / 多策略对比
- **自我进化** — 策略经验自动沉淀到 3-Tier 记忆系统，后续研发可检索历史经验作为参考

## 工作流

```
用户查询
  └─ 主图（意图识别 + 路由）
       ├─ 策略研究子图
       │    └─ 初始检索 → 自适应检索（评估-检索循环）→ 研究 → 开发(YAML DSL) → 回测
       │         └─ 回测成功 → ToT 多分支反思 → 保存经验 → 监督器 → 优化 → 开发优化版 → 回测优化版（循环）
       │         └─ 回测失败 → 代码修复 → 重新回测（最多 3 次）
       ├─ 算子研发子图（异步，与策略研发并行）
       │    └─ pick_task → 规格审查 → AI 实现 → 因果性证明（硬约束） → 契约验证 → 注册上线
       │         └─ 失败 → refine 修复（最多 3 次）→ mark_blocked
       └─ 股票分析子图
            └─ 数据获取 → 四视角并行分析 → 汇总
                 ├─ 巴菲特视角（价值投资）
                 ├─ 查理芒格视角（多学科思维）
                 ├─ 彼得林奇视角（PEG 策略）
                 └─ 费雪视角（成长股投资）
```

## 快速开始

### 前置条件

- Python 3.11
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

# 启动回测可视化仪表盘
uv run python -m long_earn.dashboard.api
```

### 常用命令

```sh
uv sync                                    # 安装依赖
uv run python -m long_earn                 # 运行项目
uv run python -m long_earn.dashboard.api   # 启动可视化服务 (默认 8090 端口)
uv run pytest tests/ -v                    # 运行全部测试
uv run pytest tests/unit/ -v               # 仅运行单元测试
uv run pytest tests/integration/ -v        # 仅运行集成测试（需 .env 配置）
uv run ruff check .                        # 代码检查
uv run ruff format .                       # 代码格式化
uv run mypy src/                           # 类型检查
```

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LLM_TYPE` | LLM 类型（ollama/dashscope/openai） | `ollama` |
| `LLM_MODEL` | 模型名称 | `deepseek-v4-flash:cloud` |
| `LLM_BASE_URL` | LLM API 地址 | `http://localhost:11434` |
| `DASHSCOPE_API_KEY` | 阿里百炼 API Key（LLM_TYPE=dashscope 时必填） | — |
| `OPENAI_API_KEY` | OpenAI API Key（LLM_TYPE=openai 时必填） | — |
| `MEMORY_PATH` | 记忆持久化路径 | `~/.long_earn/memory.npz` |
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
create_runtime_context(config)
    ↓
RuntimeContext
    ├── config               — 应用配置
    ├── logger               — 日志记录
    ├── monitoring           — 性能监控
    ├── llm_service          — LLM 调用
    ├── memory               — 3-Tier 记忆系统
    ├── stock_service        — 股票数据获取
    ├── backtest_service     — 事件驱动回测引擎（含算子目录）
    └── data_provider        — 数据源（可选）
```

服务接口定义为 `Protocol` 类，可在测试中轻松替换为 Mock 实现。

### 策略研究子图

基于 [Reflexion](https://www.promptingguide.ai/zh/techniques/reflexion) 框架，闭环迭代优化策略：

1. **自适应检索** — 根据查询自动从记忆系统检索相关信息，评估是否需要补充检索（最多 3 轮）
2. **策略研究** — LLM 结合知识上下文生成投资策略
3. **策略开发** — 将策略转化为 YAML DSL 策略描述（引用算子目录中的算子）
4. **回测** — 通过事件驱动回测引擎执行，失败时自动修复代码（最多 3 次）
5. **反思** — 基于 Tree of Thought 多分支反思，分析回测结果并提出改进方向
6. **保存经验** — 将策略和反思沉淀到 3-Tier 记忆系统
7. **监督器** — 评估是否继续迭代
8. **优化 + 开发优化版 + 回测优化版** — 多轮演进，每轮以上一轮优化版为起点

### 算子框架（operators）

算子目录是策略引擎的核心可插拔层，5 类算子：

- **factor** — 因子计算（动量、价值、质量等）
- **filter** — 股票筛选（流动性、价格区间等）
- **rank** — 排序（横截面排序选股）
- **compose** — 组合运算（复合因子）
- **technical** — 技术指标（MA / Bollinger / RSI / MACD）

**因果性硬约束**：每个算子必须声明 `causal=True`，并通过因果性证明测试 —— 算子在任意时刻 t 的输出仅依赖 timestamp ≤ t 的数据，绝不可能窥探未来。这是量化回测金融级可信的根基。

**参数 JSON Schema**：LLM 引用算子时目录能直接吐出 JSON Schema；填错参数在解析期被拦下，根本进不到回测。

**输入输出均为 polars**：与引擎主循环一致，避免 pandas↔polars 胶水。

### 算子研发子图（operator_dev）

LangGraph 编排的算子自进化链路，与策略研发并行运行：

```
pick_task → spec_review → implement → test_validate（含因果性证明） → register
                      ↓                   ↓
                   去重已存在          refine（最多 3 次）→ mark_blocked
```

关键性质：
- **因果性证明**：算子上线前必须在确定性面板上通过因果性证明（数学证明无未来函数），是硬约束
- **Sandbox AST 审计**：AI 生成的代码先经 AST 审计 + 隔离编译收敛，再参与回测
- **Backlog 异步消费**：策略研发永不阻塞；算子研发子图异步消费 operator backlog
- **可注入实现**：支持 FakeImplementer 做确定性 e2e 测试

### 策略优化流水线（strategy_optimization）

`OptimizationPipeline` 把「交易策略优化」串成可独立调用、可注入的闭环：

```
基线策略 → 优化器产出优化策略 → 回测服务验证 → 验收门槛（AcceptanceGate） → 接受 / 拒绝 + 谱系记录
```

- 可注入 `StrategyOptimizer` 与 `BacktestService`，e2e 测试可用 Fake 优化器 + mock 回测
- 支持多轮演进谱系记录，优化策略累积 `evolution_lineage`

### 股票分析子图

从四个投资大师视角并行分析股票内在价值，通过 akshare 获取股票和财务数据，支持指数退避重试。

### 回测引擎（事件驱动）

回测引擎已演进为事件驱动架构，T 维度迭代 × S 维度向量化（Slab）：

```
src/long_earn/backtest/
├── models.py                    # BacktestResult Pydantic 模型
├── domain/
│   ├── entities.py              # 领域实体（MarketDataEvent / OrderEvent / Portfolio / PerformanceMetrics）
│   ├── exceptions.py            # 领域异常层次
│   └── interfaces.py            # 数据 / 宇宙 Provider 接口
├── engine/
│   ├── core.py                  # EventDrivenBacktestEngine（T-Loop × Slab 执行）
│   ├── broker.py                # Broker 模拟撮合 + 交易成本（佣金 / 滑点 / 冲击）
│   ├── portfolio.py             # Portfolio 持仓 / 权益 / 暴露度管理
│   ├── strategy.py              # BaseStrategy 抽象（YAML DSL 策略 / ML 策略）
│   ├── ml_strategy.py           # TimeSeriesSplit 机器学习策略支持
│   ├── dsl.py                   # YAML DSL 解析器（StrategyDSL + 字段校验）
│   ├── evaluator.py             # AST 白名单表达式求值器
│   ├── visibility.py            # VisibilityGuard（因果性守卫，禁止前视偏差）
│   ├── audit.py                 # 审计追踪（DuckDB 持久化）
│   ├── operator_executor.py     # 算子执行器（连接算子目录与引擎）
│   └── strategy_templates.py    # 内置策略模板
├── operators/                   # 算子目录（5 类可插拔算子）
│   ├── base.py                  # Operator 基类 + 契约校验
│   ├── causality.py             # 因果性证明器
│   ├── factor/                  # 因子算子
│   ├── technical/               # 技术指标算子
│   ├── filter/                  # 筛选算子
│   ├── rank/                    # 排序算子
│   ├── compose/                 # 组合算子
│   └── _loader.py               # 自动扫描 + 注册
└── data/
    ├── cache.py                 # DuckDB 本地缓存（行情 / 财务 / 成分股）
    ├── provider.py              # Akshare 数据获取
    ├── akshare_provider.py      # Akshare 具体实现
    ├── miniqmt_provider.py      # MiniQMT 数据获取
    └── universe.py              # 股票池管理（沪深 300 / 中证 500 / 板块等）
```

**执行流程**：
```
T-Loop → MarketDataEvent → Strategy.on_bar → SignalEvent → Portfolio → OrderEvent → Broker → FillEvent → Portfolio.update
```

**YAML DSL 策略**：LLM 直接生成 YAML 策略描述（引用算子目录、信号、权重、风控），引擎解析后执行事件驱动回测。

**关键特性**：
- `VisibilityGuard` — 因果性守卫，在 T 维度严格禁止前视偏差
- `TradingCostConfig` — 佣金 / 滑点 / 冲击成本配置
- `InMemoryAuditTrail` + DuckDB 审计 — 全链路可追溯，供仪表盘查询
- `TimeSeriesSplit` — 支持机器学习策略的滚动训练 / 验证

### 仪表盘（dashboard）

回测可视化 API 服务 + HTML 前端，可用端点：

```
GET  /                            — 仪表盘页面
GET  /api/health                  — 健康检查
GET  /api/runs                    — 列出所有回测运行
GET  /api/runs/{run_id}/summary   — 运行摘要
GET  /api/runs/{run_id}/equity    — 权益曲线数据
GET  /api/runs/{run_id}/trades    — 交易日志
GET  /api/runs/{run_id}/signals   — 信号历史
GET  /api/runs/{run_id}/risk      — 风险指标
GET  /api/runs/{run_id}/daily_returns — 日收益率序列
GET  /api/runs/{run_id}/dashboard — 完整仪表盘数据
POST /api/compare                 — 多策略对比
```

默认监听 `0.0.0.0:8090`。

### 记忆系统

基于 numpy/pandas 的 3-Tier 记忆系统，无需外部向量数据库：

```
src/long_earn/memory/
├── store.py                 # 3-Tier 记忆存储（Working / Core / Archival）
├── tfidf.py                 # TF-IDF 向量化器 + 余弦相似度检索
├── embedding.py             # Embedding 支持（可选）
└── graph.py                 # 关系图存储（entity-relation graph）
```

- **Working**：会话级临时上下文（当前推理窗口）
- **Core**：持久化事实、策略规则、用户偏好
- **Archival**：历史经验、过往回测结果、已过期的规则
- **持久化**：`~/.long_earn/memory.npz`
- **检索**：`recall()` 支持按层级、关键词、分类过滤；`search()` 返回格式化字符串

## 技术栈

| 类别 | 技术 |
|------|------|
| 语言 | Python 3.11 |
| 工作流框架 | LangGraph |
| LLM | Ollama（默认）/ DashScope / OpenAI 兼容 API |
| 回测引擎 | 自研事件驱动引擎（Polars Slab × Pandas 向量化） |
| 算子框架 | 5 类可插拔算子（polars DataFrame） |
| 因果性 | VisibilityGuard + 因果性证明器（数学证明无未来函数） |
| 记忆系统 | 3-Tier 记忆（TF-IDF + 余弦相似度 + 关系图） |
| 数据处理 | Polars（主）+ NumPy |
| 审计 / 数据缓存 | DuckDB |
| 证券数据 | akshare / MiniQMT |
| Web 搜索 | Kimi Web Search / Tavily |
| 可视化 | Python http.server（REST API）+ 原生 HTML/JS |
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
