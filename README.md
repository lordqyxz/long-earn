# Long Earn

自我进化的量化交易系统（v0.8.0）。基于 LangGraph 的证券交易顾问智能体，支持策略研发和股票分析。

## 系统概览

Long Earn 是一个 AI 驱动的量化交易研究平台，核心能力包括：

- **智能意图路由** — 自动识别用户查询意图，路由到策略研发或股票分析子图
- **策略研发** — 基于 Reflexion 框架的闭环策略研发：研究 → 开发 → 回测 → 反思 → 优化，支持多轮迭代和自适应知识检索
- **多视角股票分析** — 从巴菲特、查理芒格、彼得林奇、费雪四个投资大师视角并行分析股票
- **自我进化** — 策略经验自动沉淀到 3-Tier 记忆系统，后续研发可检索历史经验作为参考
- **内嵌回测引擎** — 向量化回测引擎直接集成在主项目中，通过 YAML DSL 描述策略，无需外部服务

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
```

### 常用命令

```sh
uv sync                                    # 安装依赖
uv run python -m long_earn                 # 运行项目
uv run pytest tests/ -v                    # 运行全部测试
uv run pytest tests/unit/ -v               # 仅运行单元测试
uv run pytest tests/integration/ -v        # 仅运行集成测试（需 .env 配置）
uv run ruff check .                        # 代码检查
uv run ruff format .                       # 代码格式化
uv run mypy src/                           # 类型检查
uv run lint-imports                        # 架构依赖校验
```

### 环境变量

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `LLM_TYPE` | LLM 类型（ollama/dashscope/openai） | `ollama` |
| `LLM_MODEL` | 模型名称 | `qwen3.5:cloud` |
| `LLM_BASE_URL` | LLM API 地址 | `http://localhost:11434` |
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
4. **回测** — 通过内嵌向量化回测引擎执行回测，失败时自动修复代码（最多 3 次）
5. **反思** — 基于 Tree of Thought 多分支反思，分析回测结果并提出改进方向
6. **保存经验** — 将策略和反思沉淀到 3-Tier 记忆系统
7. **监督器** — 评估是否继续迭代（终止条件：年化收益率 > 10% 且夏普比率 > 0.5）

### 股票分析子图

从四个投资大师视角并行分析股票内在价值，通过 akshare 获取股票和财务数据，支持指数退避重试。

### 回测引擎（内嵌）

回测引擎已整合到主项目中（`src/long_earn/backtest/`），通过 YAML DSL 描述策略，无需 HTTP 远程调用。

**引擎结构：**

```txt
src/long_earn/backtest/
├── models.py                # BacktestResult Pydantic 模型
├── domain/
│   ├── entities.py          # 领域实体（Portfolio、DateRange）+ 值对象（PerformanceMetrics）
│   └── exceptions.py        # 领域异常层次
├── engine/
│   ├── core.py              # 向量化回测引擎（Pandas MultiIndex 矩阵运算）
│   ├── dsl.py               # YAML DSL 解析器（StrategyDSL + 字段校验）
│   └── evaluator.py         # AST 白名单表达式求值器
└── data/
    ├── cache.py             # DuckDB 本地缓存（行情 / 财务 / 成分股）
    ├── provider.py          # Akshare 数据获取（行情 + 财务前向填充）
    └── universe.py          # 股票池管理（沪深 300 / 中证 500 / 板块等）
```

- **YAML DSL 策略**：LLM 直接生成 YAML 策略描述（因子、信号、权重、风控），引擎解析后执行向量化回测
- **因子表达式**：支持 `shift(field, n)`、`rank(field)`、`np`/`pd` 函数调用
- **可用字段**：10 个行情字段（open/high/low/close/volume）和 7 个财务字段（roe/eps/net_profit_yoy 等）
- **股票池**：支持全 A / csi300 / csi500 / main_board / gem / star_board 及组合
- **DuckDB 缓存**：`~/.long_earn/backtest_cache.duckdb`，减少 akshare 请求

### 记忆系统

基于 numpy/pandas 的 3-Tier 记忆系统，无需外部向量数据库：

```txt
src/long_earn/memory/
├── store.py                 # 3-Tier 记忆存储（Working / Core / Archival）
├── tfidf.py                 # TF-IDF 向量化器 + 余弦相似度检索
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
| 回测引擎 | 自研向量化回测引擎（Pandas + NumPy） |
| 记忆系统 | 3-Tier 记忆（TF-IDF + 余弦相似度 + 关系图） |
| 数据缓存 | DuckDB |
| 证券数据 | akshare |
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