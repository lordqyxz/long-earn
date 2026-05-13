# long\_earn

自我进化的量化交易系统（v0.8.0）。基于 LangGraph 的证券交易顾问智能体，支持策略研发和股票分析。

## 常用命令

```sh
uv sync                                    # 安装依赖
uv run python -m long_earn                 # 运行项目
uv run pytest tests/ -v                    # 运行全部测试（含根级测试文件）
uv run pytest tests/unit/ -v               # 仅运行单元测试
uv run pytest tests/integration/ -v        # 仅运行集成测试（需 .env 配置）
uv run ruff check .                        # 代码检查（lint + 复杂度）
uv run ruff format .                       # 代码格式化
uv run mypy src/                           # 类型检查
uv run lint-imports                        # 架构依赖校验
```

## 架构

```txt
long_earn/
├── src/long_earn/           # 主项目源码
│   ├── backtest/            # 内嵌回测引擎
│   │   ├── domain/          #   领域模型（实体、值对象、异常）
│   │   ├── engine/          #   向量化回测引擎 + AST 安全求值器
│   │   └── data/            #   数据提供（akshare + DuckDB 缓存）
│   ├── core/                # 核心工具（prompt_loader, llm_utils）
│   ├── memory/              # 记忆系统（TF-IDF + 关系图，numpy/pandas）
│   ├── services/            # 服务接口与实现
│   ├── state.py             # 主图状态定义
│   ├── strategy_rd/         # 策略研发子图
│   │   └── agents/          # 策略研发 Agent（含同目录 .md prompt）
│   ├── stock_analysis/      # 股票分析子图
│   │   └── agents/          # 多视角分析师 Agent（含同目录 .py prompt）
│   ├── tools/               # 工具函数（回测、知识库、股票信息）
│   └── utils/               # 通用工具（llm_factory, logger）
├── tests/                   # 测试
│   ├── unit/                # 单元测试
│   │   ├── test_backtest/  # 回测引擎测试
│   │   ├── test_memory/    # 记忆系统测试
│   │   ├── test_services/  # 服务层测试
│   │   ├── test_strategy_rd/ # 策略研发测试
│   │   └── test_config.py  # 配置测试
│   └── integration/         # 集成测试
├── docs/                    # 文档
│   ├── adr/                 # 架构决策记录
│   └── research/            # 调研文档
└── langgraph.json           # LangGraph 部署配置
```

依赖注入架构，所有服务通过 `RuntimeContext` 传递：

```
AppConfig.from_env()
    ↓
create_runtime_context(config) / initialize_context(config)
    ↓
RuntimeContext(dataclass)
    ├── llm_service: LLMService (Protocol)
    ├── memory: MemoryService (Protocol)        # 3-Tier 记忆系统
    ├── stock_service: StockService (Protocol)
    ├── backtest_service: BacktestService (Protocol)
    ├── logger: LoggerService
    ├── monitoring: MonitoringService
    └── config: AppConfig
```

主图（`agent.py`）路由到子图：

- **strategy\_rd**：策略研发（start → init\_iteration → initial\_retrieval → adaptive\_retrieval 循环 → develop → backtest → 代码修复循环（最多3次）→ reflection → save\_experience → supervisor → optimize 循环）
- **stock\_analysis**：股票分析（4 视角并行分析后汇总）

## 编码规范

- Python 3.11 严格版本（`requires-python = "==3.11.*"`）
- 所有函数和参数必须添加类型注解
- `str` 类型参数默认值 `""`
- 代码格式和检查：ruff（format + lint + McCabe 圈复杂度 ≤15 + Pylint 规则 + 未使用参数检测，88 字符行宽）
- 类型检查：mypy（渐进式，warn\_return\_any + check\_untyped\_defs）
- 架构依赖校验：import-linter（数据层不依赖上层、服务层不依赖 tools）
- 中文注释和文档字符串

### 依赖注入

所有 Agent 和子图必须通过 `context` 参数初始化：

```python
# 正确
context = create_runtime_context()
agent = StrategyResearchAgent(context=context)

# 错误 — 禁止无 context 创建
agent = StrategyResearchAgent()
```

### 节点返回值

LangGraph 节点只需返回要更新的 key，不需要返回完整状态：

```python
def my_node(state: State, context: RuntimeContext):
    return {"result": "..."}  # 自动合并到全局状态
```

### Prompt 管理

使用 `MarkdownPromptTemplate` 加载 `.md` 文件。变量使用 `{{variable}}` 双花括号语法（自动转换为 LangChain 的 `{variable}`）；代码块内大括号自动转义。frontmatter 可选，支持 `version`/`description` 字段。

```python
from long_earn.core.prompt_loader import MarkdownPromptTemplate
prompt_template = MarkdownPromptTemplate("my_prompt.md", caller_file=__file__)
prompt = prompt_template.format(query=query)
```

**约定**：每个 Agent 的 prompt `.md` 文件与该 Agent 的 `.py` 文件放在同一目录下（例如 `strategy_research_agent.py` 与 `strategy_research_prompt.md` 同在 `agents/` 目录）。

## Gotchas

- **回测引擎内嵌**：回测引擎已整合到主项目（`src/long_earn/backtest/`），无需启动外部 HTTP 服务。策略通过 YAML DSL 描述，引擎直接调用。
- **记忆系统**：基于 numpy/pandas 的 3-Tier 记忆系统（Working/Core/Archival），替代 Qdrant 向量数据库。无需外部嵌入模型，使用内置 TF-IDF + 余弦相似度检索。持久化至 `~/.long_earn/memory.npz`。
- **数据缓存**：回测引擎使用 DuckDB 本地缓存（`~/.long_earn/backtest_cache.duckdb`），首次运行时会通过 akshare 获取数据。
- **Prompt 文件路径**：`MarkdownPromptTemplate` 基于 `caller_file` 解析相对路径，移动 `.md` 文件后需同步修改对应 Agent 中的文件名
- **表达式安全**：回测引擎使用 AST 白名单求值器 (`backtest/engine/evaluator.py`)，不使用 `eval()`。详见 [ADR-003](docs/adr/003-ast-safe-evaluator.md)
- **集成测试需 `.env`**：运行 `tests/integration/` 或根级集成测试文件前需配置环境变量（见下方环境变量表）

## 架构决策记录 (ADR)

- [ADR-001](docs/adr/001-yaml-dsl-strategy.md): YAML DSL 策略描述替代 Python/qlib
- [ADR-002](docs/adr/002-partial-node-injection.md): `functools.partial` 替代闭包进行节点注入
- [ADR-003](docs/adr/003-ast-safe-evaluator.md): AST 白名单表达式求值替代 `eval()`
- [ADR-004](docs/adr/004-memory-system.md): numpy/pandas 三级记忆系统替代 Qdrant 向量数据库

## 调研文档

- [量化交易 + Agent 记忆系统最佳实践](docs/research/agent-memory-quant-best-practices.md): 3-Tier 记忆、领域服务分层、Agent 设计模式

## 测试说明

- **单元测试**：`tests/unit/` 下按模块组织（test\_backtest/、test\_memory/、test\_services/、test\_strategy\_rd/）
- **集成测试**：`tests/integration/` 需配置 `.env` 环境变量

### 单元测试原则

单元测试只覆盖以下两类场景，不写冗余测试：

1. **接口对接正确性**：服务 Protocol 是否正确代理、子图能否编译、Prompt 模块能否加载并格式化、配置/上下文是否正确注入
2. **核心域逻辑正确性**：DSL 解析与校验、AST 安全求值器（白名单与禁止列表）、引擎执行主流程、记忆系统检索与持久化、TF-IDF 向量化

**不写的测试**：

- 简单数据类的构造/默认值/不可变性（Python dataclass 行为已由语言保证）
- 显而易见的错误路径（文件不存在抛 FileNotFoundError、空输入返回空列表）
- 重复边界用例（同一逻辑的多个细微变体，如 `min_weight` 过滤 vs 不过滤）
- 实现细节（日志是否调用、属性是否赋值、`repr()` 格式）
- 需要大量 mock 链的端到端子图流程（属于集成测试范畴）

```sh
LLM_TYPE=ollama
LLM_MODEL=qwen3.5:cloud
LLM_BASE_URL=http://localhost:11434
```

## 回测引擎（内嵌）

回测引擎已整合到主项目中 (`src/long_earn/backtest/`)，通过 YAML DSL 描述策略，无需 HTTP 远程调用。

**引擎结构：**

```txt
src/long_earn/backtest/
├── __init__.py              # 对外暴露 BacktestResult, VectorizedBacktestEngine 等
├── models.py                # BacktestResult Pydantic 模型
├── domain/
│   ├── entities.py          # 领域实体（Portfolio, DateRange）+ 值对象（PerformanceMetrics）
│   └── exceptions.py        # 领域异常层次（BacktestDomainError 子类）
├── engine/
│   ├── __init__.py
│   ├── core.py              # 向量化回测引擎（Pandas MultiIndex 矩阵运算）
│   ├── dsl.py               # YAML DSL 解析器（StrategyDSL + 字段校验）
│   └── evaluator.py         # AST 白名单表达式求值器
└── data/
    ├── __init__.py
    ├── cache.py             # DuckDB 本地缓存（行情/财务/成分股）
    ├── provider.py          # Akshare 数据获取（行情 + 财务前向填充）
    └── universe.py          # 股票池管理（沪深300/中证500/板块等）
```

- **YAML DSL 策略**：LLM 直接生成 YAML 策略描述（因子、信号、权重、风控），引擎解析后执行向量化回测
- **因子表达式**：支持 `shift(field, n)`、`rank(field)`、`np`/`pd` 函数调用
- **可用字段**：10 个行情字段（open/high/low/close/volume）和 7 个财务字段（roe/eps/net\_profit\_yoy 等）
- **股票池**：支持全 A/csi300/csi500/main\_board/gem/star\_board 及组合
- **DuckDB 缓存**：`~/.long_earn/backtest_cache.duckdb`，减少 akshare 请求
- **兼容旧接口**：`BacktestServiceImpl._convert_code_to_yaml()` 可将旧 Python 代码转为基础 YAML

## 记忆系统

基于 numpy/pandas 的 3-Tier 记忆系统，替代 Qdrant 向量数据库：

```txt
src/long_earn/memory/
├── __init__.py              # MemoryTier 枚举 + 便捷函数
├── store.py                 # 3-Tier 记忆存储（Working/Core/Archival）
├── tfidf.py                 # TF-IDF 向量化器 + 余弦相似度检索
└── graph.py                 # 关系图存储（entity-relation graph）
```

- **Working**：会话级临时上下文（当前推理窗口）
- **Core**：持久化事实、策略规则、用户偏好
- **Archival**：历史经验、过往回测结果、已过期的规则
- **持久化**：`~/.long_earn/memory.npz`
- **检索**：`recall()` 支持按层级、关键词、分类过滤；`search()` 返回格式化字符串

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| LLM_TYPE | ollama | LLM 类型（ollama/dashscope/openai）|
| LLM_MODEL | qwen3.5:cloud | 模型名称 |
| LLM_BASE_URL | http://localhost:11434 | API 基础 URL |
| MEMORY_PATH | ~/.long\_earn/memory.npz | 记忆持久化路径 |
| INIT_DIR | ./init | 知识库初始化目录 |
| BACKTEST_START_DATE | 2020-01-01 | 回测默认起始日期 |
| BACKTEST_END_DATE | 2023-12-31 | 回测默认结束日期 |
| MAX_ITERATIONS | 3 | 策略研发最大迭代次数 |
| STRATEGY_KEYWORDS | 策略,思路,投资策略 | 策略研究路由关键词（逗号分隔）|
| STOCK_ANALYSIS_KEYWORDS | 股票,分析,公司 | 股票分析路由关键词（逗号分隔）|

## 关键约束

- 服务接口定义为 `Protocol` 类（`services/__init__.py`），具体实现在各 `*_service.py` 中
- `context_init.py` 中 `initialize_context()` 会额外调用 `memory.initialize()` 加载记忆
- `create_runtime_context()` 创建服务实例但不初始化记忆；`initialize_context()` 包含完整初始化
- 测试中使用 Mock 替代真实服务，无需 API 调用
- import-linter 合约：`backtest.data` 不依赖上层模块，`services` 不依赖 `tools`

## 关键文件

| 用途 | 路径 |
|------|------|
| 入口 | `src/long_earn/__main__.py` |
| 主图 | `src/long_earn/agent.py` |
| 主图状态 | `src/long_earn/state.py` |
| 配置 & RuntimeContext | `src/long_earn/config.py` |
| 上下文初始化 | `src/long_earn/context_init.py` |
| Prompt 加载器 | `src/long_earn/core/prompt_loader.py` |
| LLM 工具 | `src/long_earn/core/llm_utils.py` |
| 服务接口 | `src/long_earn/services/__init__.py` |
| 回测服务实现 | `src/long_earn/services/backtest_service.py` |
| 记忆服务实现 | `src/long_earn/services/memory_service.py` |
| LLM 服务实现 | `src/long_earn/services/llm_service.py` |
| 股票信息服务 | `src/long_earn/services/stock_service.py` |
| 记忆存储引擎 | `src/long_earn/memory/store.py` |
| TF-IDF 向量化器 | `src/long_earn/memory/tfidf.py` |
| 关系图存储 | `src/long_earn/memory/graph.py` |
| 领域实体 & 值对象 | `src/long_earn/backtest/domain/entities.py` |
| 领域异常 | `src/long_earn/backtest/domain/exceptions.py` |
| 回测引擎核心 | `src/long_earn/backtest/engine/core.py` |
| YAML DSL 解析器 | `src/long_earn/backtest/engine/dsl.py` |
| 安全表达式求值器 | `src/long_earn/backtest/engine/evaluator.py` |
| 数据模型 | `src/long_earn/backtest/models.py` |
| 数据提供者 | `src/long_earn/backtest/data/provider.py` |
| DuckDB 缓存 | `src/long_earn/backtest/data/cache.py` |
| 股票池管理 | `src/long_earn/backtest/data/universe.py` |
| 策略研发子图 | `src/long_earn/strategy_rd/subgraph.py` |
| 策略研发状态 | `src/long_earn/strategy_rd/state.py` |
| 知识检索 Mixin | `src/long_earn/strategy_rd/agents/mixins.py` |
| 策略开发 Agent | `src/long_earn/strategy_rd/agents/strategy_develop_agent.py` |
| 策略研发 Prompt | `src/long_earn/strategy_rd/agents/strategy_develop_prompt.md` |
| 代码修复 Prompt | `src/long_earn/strategy_rd/agents/strategy_develop_refine_prompt.md` |
| 策略研究 Prompt 模块 | `src/long_earn/strategy_rd/agents/strategy_research_prompt.py` |
| 策略研究 Prompt | `src/long_earn/strategy_rd/agents/strategy_research_prompt.md` |
| 策略研究 Agent | `src/long_earn/strategy_rd/agents/strategy_research_agent.py` |
| 监督器 Prompt 模块 | `src/long_earn/strategy_rd/agents/strategy_rd_supervisor_prompt.py` |
| 监督器 Prompt | `src/long_earn/strategy_rd/agents/strategy_rd_supervisor_prompt.md` |
| 继续迭代 Prompt | `src/long_earn/strategy_rd/agents/strategy_rd_supervisor_continue_prompt.md` |
| 策略监督器 | `src/long_earn/strategy_rd/agents/strategy_rd_supervisor.py` |
| 股票分析子图 | `src/long_earn/stock_analysis/subgraph.py` |
| 股票分析状态 | `src/long_earn/stock_analysis/state.py` |
| 知识库工具 | `src/long_earn/tools/store.py` |
| 回测工具 | `src/long_earn/tools/backtest.py` |
| 股票信息工具 | `src/long_earn/tools/get_stock_info.py` |
| 文本分割工具 | `src/long_earn/tools/md_splitter.py` |
| LangGraph 部署配置 | `langgraph.json` |
| 环境变量模板 | `.env.example` |