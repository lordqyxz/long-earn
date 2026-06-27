# long\_earn

自我进化的量化交易系统（v1.0.1）。基于 LangGraph 的证券交易顾问智能体，支持策略研发和股票分析。

## 常用命令

```sh
uv sync                                    # 安装依赖
uv run python -m long_earn                 # 运行项目
uv run pytest tests/ -v                    # 运行全部测试（含根级测试文件）
uv run pytest tests/unit/ -v               # 仅运行单元测试
uv run pytest tests/integration/ -v        # 仅运行集成测试（需 .env 配置）
uv run ruff check .                        # 代码检查（lint + 复杂度）
uv run ruff format .                       # 代码格式化
uv run lint-imports                        # 架构依赖校验
```

类型检查用 Serena LSP，见下方「质量门槛」。

### 质量门槛（按强弱排序）

1. **Serena LSP 单文件零错**（**首要、唯一类型检查工具**）：编辑任何代码符号后，必须用 `mcp__serena__get_diagnostics_for_file` 验证目标文件 `Error` 级别诊断为空。这是最快、最聚焦的反馈回路，也是本项目**唯一**的类型检查手段。
2. **`uv run ruff check src/` 全局零错**：风格、复杂度（McCabe ≤15）、Pylint 规则。
3. **`uv run lint-imports`**：架构依赖契约（数据层不依赖上层、服务层不依赖 tools）必须保持 0 broken。
4. **`uv run pytest tests/unit/`**：单元测试全绿。

> 不使用 mypy / pyright CLI：以 Serena LSP 单文件诊断为准，避免双工具冲突与配置分裂。

### 架构与设计原则

- **整洁架构 (Clean Architecture)**：依赖方向单向收敛——`tools` → `services` → `domain`，外层可知内层，内层不知外层。
- **DDD 辅助**：`backtest/domain/` 承载领域模型（实体、值对象、领域异常）；`services/` 是应用服务（编排领域行为 + 跨上下文事务）；`backtest/engine/` 是领域服务（纯计算）；`data/` 是基础设施（数据提供者实现）。
- **依赖注入容器**：`RuntimeContext` 是 DI Container，下游组件接收**已构造完毕**的服务实例（非 `Service | None`）。允许 `Service | None` 仅限**容器初始化中间态**；业务节点接受非空依赖。

## 架构

```txt
long_earn/
├── src/long_earn/           # 主项目源码
│   ├── backtest/            # 内嵌回测引擎
│   │   ├── domain/          #   领域模型（实体、值对象、异常）
│   │   ├── engine/          #   事件驱动回测引擎 + AST 安全求值器 + 并行编排 + 参数网格 + 共享数据底座
│   │   ├── operators/       #   算子框架（factor/filter/rank/compose/technical + 因果检测）
│   │   └── data/            #   数据提供（Composite 多源降级：DuckDB → miniqmt → ciccwm → akshare）
│   ├── core/                # 核心工具（prompt_loader `${var}`、render 纯函数渲染、llm_utils）
│   ├── substance/           # 物质-运动统一架构（Substance + Motion，ADR-007，已实施）
│   │   └── indices/         #   RetrievalIndex（keyword+semantic 双通道）+ GraphIndex（邻接表）
│   ├── operator_dev/        # 算子研发子图（sandbox + backlog + spec + agents）
│   ├── strategy_optimization/ # 策略优化 pipeline（acceptance / optimizer）
│   ├── services/            # 服务接口与实现
│   ├── state.py             # 主图状态定义
│   ├── strategy_rd/         # 策略研发子图
│   │   └── agents/          # 策略研发 Agent（含同目录 .md prompt）
│   ├── stock_analysis/      # 股票分析子图
│   │   └── agents/          # 多视角分析师 Agent（含同目录 .md prompt）
│   ├── dashboard/           # 可视化仪表盘（分析器 + API + 前端）
│   │   └── templates/       #   HTML 仪表盘模板
│   ├── tools/               # 工具函数（回测、知识库、股票信息）
│   └── utils/               # 通用工具（llm_factory, logger）
├── tests/                   # 测试
│   ├── unit/                # 单元测试
│   │   ├── test_backtest/  # 回测引擎测试（含并行 / 参数网格 / 渲染器）
│   │   ├── test_substance/ # 物质-运动架构测试
│   │   ├── test_services/  # 服务层测试
│   │   ├── test_strategy_rd/ # 策略研发测试
│   │   └── test_config.py  # 配置测试
│   └── integration/         # 集成测试
├── docs/                    # 文档
│   ├── adr/                 # 架构决策记录
│   └── research/            # 调研文档
├── scripts/                 # 一次性脚本（独立回测、数学验证），不属于主包
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
    ├── memory: MemoryService (Protocol)        # 委托 SubstanceStore（ADR-007）
    ├── stock_service: StockService (Protocol)
    ├── backtest_service: BacktestService (Protocol)
    ├── logger: LoggerService
    ├── monitoring: MonitoringService
    ├── config: AppConfig
    └── data_provider: DataProvider | None       # 可选；跨子图共享数据提供者
```

主图（`agent.py`）路由到子图：

- **strategy\_rd**：策略研发（start → init\_iteration → initial\_retrieval → adaptive\_retrieval 循环 → develop → backtest → 代码修复循环（最多3次）→ reflection → save\_experience → supervisor → optimize 循环）
- **stock\_analysis**：股票分析（4 视角并行分析后汇总）

## 编码规范

- Python 3.13 严格版本（`requires-python = "==3.13.*"`）
- 所有函数和参数必须添加类型注解
- `str` 类型参数默认值 `""`
- 代码格式和检查：ruff（format + lint + McCabe 圈复杂度 ≤15 + Pylint 规则 + 未使用参数检测，88 字符行宽）
- 类型检查：Serena LSP 单文件诊断（`mcp__serena__get_diagnostics_for_file`），不使用 mypy/pyright CLI（详见上文「质量门槛」）
- 架构依赖校验：import-linter（数据层不依赖上层、服务层不依赖 tools）
- 中文注释和文档字符串
- **日志统一使用 loguru**：禁止 `import logging` / `logging.getLogger`；所有模块直接 `from loguru import logger`。日志格式由 `LoggerServiceImpl` 统一配置（带颜色、时间、模块名、函数名、行号）。脚本入口需 `logger.remove()` 后 `logger.add(sys.stderr, ...)` 配置，格式与 `LoggerServiceImpl` 一致。

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

使用 `MarkdownPromptTemplate` 加载 `.md` 文件。变量使用 `${variable}` 语法（POSIX/bash/JS 模板字面量同款，跨语言可移植）；底层由 `core/render.py` 纯函数渲染（已解耦 LangChain，代码块内大括号无需转义）。frontmatter 可选，支持 `version`/`description` 字段。

```python
from long_earn.core.prompt_loader import MarkdownPromptTemplate
prompt_template = MarkdownPromptTemplate("my_prompt.md", caller_file=__file__)
prompt = prompt_template.format(query=query)
```

**约定**：每个 Agent 的 prompt `.md` 文件与该 Agent 的 `.py` 文件放在同一目录下（例如 `strategy_research_agent.py` 与 `strategy_research_prompt.md` 同在 `agents/` 目录）。

## Gotchas

- **回测引擎内嵌**：回测引擎已整合到主项目（`src/long_earn/backtest/`），无需启动外部 HTTP 服务。策略通过 YAML DSL 描述，引擎直接调用。
- **子项目为 git submodule**：`remoteMiniQmt/` 通过 `.gitmodules` 引入，clone 后需 `git submodule update --init --recursive` 才会有内容
- **记忆系统**：基于物质-运动统一架构（ADR-007），事件/关系/知识/策略经验统一为 `Substance`，检索走 WorldInfo 关键词触发 + 语义相似度双通道。持久化至 `~/.long_earn/substances.jsonl`（JSONL，无 pickle）。旧 `memory/` 模块（ADR-004）已删除。
- **数据缓存**：回测引擎使用 DuckDB 本地缓存（`~/.long_earn/backtest_cache.duckdb`），多源降级链：DuckDB 缓存 → miniqmt (xtquant) → ciccwm (HTTP) → akshare。另有 `remoteMiniQmt/` 子项目提供远程 WebSocket 数据服务。
- **Prompt 文件路径**：`MarkdownPromptTemplate` 基于 `caller_file` 解析相对路径，移动 `.md` 文件后需同步修改对应 Agent 中的文件名
- **表达式安全**：回测引擎使用 AST 白名单求值器 (`backtest/engine/evaluator.py`)，不使用 `eval()`。详见 [ADR-003](docs/adr/003-ast-safe-evaluator.md)。算子目录路径（[ADR-009](docs/adr/009-operator-catalog-and-operator-dev-subgraph.md)）以 `prove_causality` 因果性数学证明替代表达式白名单，作为新策略的主执行路径；旧表达式路径向后兼容保留。
- **集成测试需 `.env`**：运行 `tests/integration/` 或根级集成测试文件前需配置环境变量（见下方环境变量表）

## 架构决策记录 (ADR)

- [ADR-001](docs/adr/001-yaml-dsl-strategy.md): YAML DSL 策略描述替代 Python/qlib
- [ADR-002](docs/adr/002-partial-node-injection.md): `functools.partial` 替代闭包进行节点注入
- [ADR-003](docs/adr/003-ast-safe-evaluator.md): AST 白名单表达式求值替代 `eval()`
- [ADR-004](docs/adr/004-memory-system.md): numpy/pandas 三级记忆系统替代 Qdrant 向量数据库（Superseded by ADR-007）
- [ADR-005](docs/adr/005-event-driven-backtest.md): 事件驱动回测框架替代向量化引擎。优先保证可信性（杜绝未来函数）与复杂策略表达力，速度为次要目标。
- [ADR-006](docs/adr/006-ciccwm-data-provider.md): 引入 ciccwm 财经数据 Provider（Accepted）。纯 HTTP、零本地依赖的第四数据源，补齐财务报表 / 资金流向 / 排行 / 关联板块 / 热榜资讯能力；已实现 `ciccwm_client.py` + `ciccwm_provider.py`，接入 `CompositeDataProvider` 降级链（DuckDB → miniqmt → ciccwm → akshare）。
- [ADR-007](docs/adr/007-unified-substance-architecture.md): 物质-运动统一架构（**已实施**）。`Substance`（Pydantic）统一事件/关系/知识/策略经验为"物质"，`motion` 函数为"运动"（不持久化）；双索引（RetrievalIndex keyword+semantic + GraphIndex 邻接表）；JSONL 持久化无 pickle。旧 `memory/`（ADR-004 v2.0）已删除。`MemoryService` Protocol 破坏性收窄 8 → 4 方法（删僵尸方法 `reflect`/`relate`/`remember`/`recall` + `tier` 死参；`save_experience` 收 `StrategyExperience` 值对象，`search_experience` 返回 `list[StrategyExperience]`，消灭 markdown 往返 regex 契约）；否决拆 `KnowledgeService` + `ExperienceService`（Substance 模型下无本质区别，仅 metadata 标签差异）。
- [ADR-008](docs/adr/008-parallel-backtest-and-unified-templating.md): 并行回测 + 统一模板渲染（**已实施**）。`${var}` 占位符语法（跨语言可移植）+ 纯函数渲染器解耦 LangChain + 进程级并行编排层（SharedMemory 零拷贝 + ProcessPoolExecutor）+ 参数网格（标量插值 + 对象层变换）。删除 80 行转义逻辑；32 核并行回测；`BacktestService.run_grid` / `run_walk_forward_parallel`。
- [ADR-009](docs/adr/009-operator-catalog-and-operator-dev-subgraph.md): 算子目录 + 算子研发子图（**核心链路已实施**）。类型化算子目录（`@operator` + Pydantic params + 约定目录自动扫描）替代 ADR-003 自由表达式 DSL；`prove_causality` 因果性数学证明（未来扰动不变性）作算子上线硬约束；operator_dev 异步闭环（spec→审计→因果证明→注册）+ strategy_optimization 验收（sharpe 严格提升）。**后续**：gap_detector 接入 / register 写盘 / 主图挂载 / 退役 evaluator。
- [ADR-010](docs/adr/010-hypothesis-tree-refinement.md): 假设树精炼 HTR（**Proposed**）。将 `strategy_rd` 子图从线性进化循环升级为 Arbor HTR 六步循环（observe→ideate→select→dispatch→backpropagate→decide）+ 持久化假设树 + Walk-Forward held-out 合并门。**混合持久化**：树本体独立 JSON Store，摘要回写 ADR-007 SubstanceStore 做 hot-start。5 阶段实施。

## 调研文档

- [量化交易 + Agent 记忆系统最佳实践](docs/research/agent-memory-quant-best-practices.md): 3-Tier 记忆、领域服务分层、Agent 设计模式

## 测试说明

- **单元测试**：`tests/unit/` 下按模块组织（test\_backtest/、test\_memory/、test\_services/、test\_strategy\_rd/）
- **集成测试**：`tests/integration/` 需配置 `.env` 环境变量

### 测试编写原则

测试只写在两个地方：

1. **接口层**：验证接口实现符合契约（服务 Protocol 代理、配置注入、子图编译、Prompt 加载）
2. **系统关键环节**：引擎主流程、风控触发、Walk-Forward、安全求值器等不可出错的核心链路

其余代码（数据类、工具函数、内部辅助方法）不写测试 —— Python 已保证其正确性，测试只是重复声明。

**不写的测试**：

- 简单数据类的构造/默认值/不可变性
- 显而易见的错误路径（文件不存在抛 FileNotFoundError、空输入返回空列表）
- 重复边界用例（同一逻辑的多个细微变体）
- 实现细节（日志调用、属性赋值、`repr()` 格式）
- 需要大量 mock 链的端到端子图流程（属于集成测试范畴）

### 核心引擎测试 (tests/unit/test_backtest/test_engine.py)

引擎测试覆盖关键链路而非内部实现细节，使用 `unittest.TestCase`：

| 测试类 | 覆盖点 | 用例数 |
|--------|--------|--------|
| `TestEngineInit` | 构造函数默认值和自定义参数 | 2 |
| `TestEngineRun` | run() 主流程、空数据处理、异常捕获 | 3 |
| `TestRiskChecks` | 止损触发、最大回撤触发、风控关闭场景 | 3 |
| `TestWalkForward` | Walk-Forward 折叠结构和平均指标 | 1 |
| `TestAuditTrail` | 审计跟踪记录事件类型完整性 | 1 |

通过 `MockDataProvider` + 内联策略桩（`_SimpleStrategy` / `_EmptyStrategy` / `_RaisingStrategy`）注入测试数据，避免对外部数据源的依赖。

## 回测引擎（事件驱动）

回测引擎已从向量化架构迁移至 **事件驱动 (Event-Driven)** 架构，旨在实现与 LLM Agent 的共同进化。

**核心设计哲学：**
- **Agent 友好度 (Agent-Centric)**：接口设计优先考虑 LLM 的认知成本和生成正确率（杜绝索引幻觉），而非传统量化框架习惯。
- **金融级可信 (Financial Fidelity)**：通过严格的事件流控制时间线，在架构层面绝对杜绝“未来函数”。
- **复杂表达力 (Expressiveness)**：支持状态化策略，允许 Agent 定义复杂的执行逻辑和动态风控。

**引擎结构：**
```txt
src/long_earn/backtest/
├── __init__.py              # 对外暴露 BacktestResult, EventEngine 等
├── models.py                # BacktestResult Pydantic 模型
├── domain/
│   ├── entities.py          # 领域实体（Portfolio, Event, Order）
│   └── exceptions.py        # 领域异常层次
├── engine/
│   ├── core.py              # 事件循环核心 (Event Loop)
│   ├── dsl.py               # 状态化策略 DSL 解析器
│   ├── evaluator.py         # AST 安全求值器
│   └── broker.py            # 模拟撮合与成本计算 (Slippage, Commission)
└── data/
    ├── __init__.py
    ├── cache.py             # DuckDB 本地缓存
    ├── provider.py          # 数据提供者接口（miniqmt 版）
    ├── miniqmt_provider.py  # xtquant.xtdata 数据获取封装
    └── universe.py          # 股票池管理（miniqmt 版）
```

- **状态化策略**：LLM 生成定义 `init()` 和 `on_bar()` 的状态机逻辑，引擎通过事件流驱动执行。
- **数据隔离**：策略仅能通过 `engine.current_data` 访问当前时刻数据，确保回测真实性。
- **DuckDB 缓存**：`~/.long_earn/backtest_cache.duckdb`，优化大规模数据的喂入速度。

## 记忆系统

基于物质-运动统一架构（[ADR-007](docs/adr/007-unified-substance-architecture.md)），事件/关系/知识/策略经验统一为 `Substance`：

```txt
src/long_earn/substance/
├── __init__.py              # 导出 Substance, SubstanceForm, SubstanceStore
├── model.py                 # Substance(Pydantic) + SubstanceForm + FilterLogic
├── store.py                 # SubstanceStore（统一存储 + 索引协调 + 时间过滤）
├── motion.py                # 运动层（activate/decay/conflict/compress）
├── persistence.py           # JSONL 读写（Pydantic 序列化）
└── indices/
    ├── retrieval.py         # RetrievalIndex（keyword 通道 + semantic 通道 + 融合）
    └── graph.py             # GraphIndex（dict 邻接表 + BFS 返回路径）
```

- **物质 (Substance)**：统一存在基类，`form` 区分 event/relation/knowledge/strategy/backtest。关系是一等物质（有完整 provenance）。
- **运动 (motion)**：施加在物质上的运算（activate/decay/conflict/compress），不持久化，只产出新物质。
- **双索引**：RetrievalIndex（WorldInfo 关键词触发 + TF-IDF/embedding 语义相似度双通道融合）+ GraphIndex（邻接表图遍历）。
- **持久化**：`~/.long_earn/substances.jsonl`（JSONL，无 pickle，有 schema 版本号）。
- **防未来函数**：`visible_from` 字段，回测引擎查询时仅 `visible_from ≤ current_bar_date` 的物质可见。

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| LLM_TYPE | ollama | LLM 类型（ollama/dashscope/openai）|
| LLM_MODEL | qwen3.5:cloud | 模型名称 |
| LLM_BASE_URL | http://localhost:11434 | API 基础 URL |
| DASHSCOPE_API_KEY | — | 阿里百炼 API Key（LLM_TYPE=dashscope 时必填）|
| OPENAI_API_KEY | — | OpenAI API Key（LLM_TYPE=openai 时必填）|
| MEMORY_PATH | ~/.long\_earn/substances.jsonl | 记忆持久化路径（物质-运动架构，JSONL） |
| INIT_DIR | ./init | 知识库初始化目录 |
| BACKTEST_START_DATE | 2020-01-01 | 回测默认起始日期 |
| BACKTEST_END_DATE | 2023-12-31 | 回测默认结束日期 |
| MAX_ITERATIONS | 3 | 策略研发最大迭代次数 |
| STRATEGY_KEYWORDS | 策略,思路,投资策略 | 策略研究路由关键词（逗号分隔）|
| STOCK_ANALYSIS_KEYWORDS | 股票,分析,公司 | 股票分析路由关键词（逗号分隔）|

### LLM 服务启动

默认使用 Ollama 作为 LLM 后端。启动方式：

```sh
ollama serve                    # 启动 Ollama 服务（默认端口 11434）
ollama pull <model>             # 拉取模型，如 deepseek-v4-flash:cloud
```

未启动时项目会因连接 `http://localhost:11434` 失败而报错。验证服务是否就绪：

```sh
curl http://localhost:11434/api/tags    # 返回已安装模型列表
```

切换到 DashScope / OpenAI 时无需运行 Ollama，仅需在 `.env` 配置对应 API Key。

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
| 记忆存储（ADR-007 物质-运动架构） | `src/long_earn/substance/store.py` |
| 物质模型 | `src/long_earn/substance/model.py` |
| 运动层（激活/衰减/冲突/压缩） | `src/long_earn/substance/motion.py` |
| 检索索引（keyword+semantic） | `src/long_earn/substance/indices/retrieval.py` |
| 图索引（邻接表） | `src/long_earn/substance/indices/graph.py` |
| 持久化（JSONL） | `src/long_earn/substance/persistence.py` |
| 领域实体 & 值对象 | `src/long_earn/backtest/domain/entities.py` |
| 领域异常 | `src/long_earn/backtest/domain/exceptions.py` |
| 抽象接口 (AuditProvider) | `src/long_earn/backtest/domain/interfaces.py` |
| 回测引擎核心 | `src/long_earn/backtest/engine/core.py` |
| 策略基类 (BaseStrategy) | `src/long_earn/backtest/engine/strategy.py` |
| 可见性守护 (防未来函数) | `src/long_earn/backtest/engine/visibility.py` |
| 撮合经纪人 (Broker) | `src/long_earn/backtest/engine/broker.py` |
| 投资组合管理 (Portfolio) | `src/long_earn/backtest/engine/portfolio.py` |
| 审计日志 (Audit) | `src/long_earn/backtest/engine/audit.py` |
| 可观测性 (Telemetry) | `src/long_earn/backtest/engine/telemetry.py` |
| ML 策略 & 特征工程 | `src/long_earn/backtest/engine/ml_strategy.py` |
| YAML DSL 解析器 | `src/long_earn/backtest/engine/dsl.py` |
| 安全表达式求值器 | `src/long_earn/backtest/engine/evaluator.py` |
| 审计提供者 | `src/long_earn/backtest/engine/audit.py` |
| 并行编排层 | `src/long_earn/backtest/engine/parallel.py` |
| 参数网格 | `src/long_earn/backtest/engine/param_grid.py` |
| 共享数据底座 (SharedMemory) | `src/long_earn/backtest/engine/shared_data.py` |
| 算子框架基类 | `src/long_earn/backtest/operators/base.py` |
| 因果检测 | `src/long_earn/backtest/operators/causality.py` |
| 算子目录策略执行器 | `src/long_earn/backtest/engine/operator_executor.py` |
| 算子研发子图 | `src/long_earn/operator_dev/subgraph.py` |
| 策略优化 pipeline | `src/long_earn/strategy_optimization/pipeline.py` |
| 数据模型 | `src/long_earn/backtest/models.py` |
| 数据提供者 | `src/long_earn/backtest/data/provider.py` |
| ciccwm HTTP 客户端 | `src/long_earn/backtest/data/ciccwm_client.py` |
| ciccwm 数据提供者 | `src/long_earn/backtest/data/ciccwm_provider.py` |
| miniqmt 数据封装 | `src/long_earn/backtest/data/miniqmt_provider.py` |
| DuckDB 缓存 | `src/long_earn/backtest/data/cache.py` |
| 股票池管理 | `src/long_earn/backtest/data/universe.py` |
| Dashboard 分析器 | `src/long_earn/dashboard/analyzer.py` |
| Dashboard API 服务 | `src/long_earn/dashboard/api.py` |
| Dashboard HTML 模板 | `src/long_earn/dashboard/templates/dashboard.html` |
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
| 回测分析器 | `src/long_earn/tools/backtest_analyzer.py` |
| 可视化 API | `src/long_earn/tools/visualization_api.py` |
| Kimi 网页搜索 | `src/long_earn/tools/kimi_web_search.py` |
| 文本分割工具 | `src/long_earn/tools/md_splitter.py` |
| LangGraph 部署配置 | `langgraph.json` |
| 环境变量模板 | `.env.example` |

## 子项目：Remote MiniQMT

`remoteMiniQmt/` 是基于 WebSocket 的 miniQMT (xtquant) 远程 SDK，用于将本地 xtquant 行情与交易能力暴露为远程 JSON-RPC 2.0 服务。

```
remoteMiniQmt/
├── src/rmt/
│   ├── protocol.py          # JSON-RPC 协议定义
│   ├── transport.py         # WebSocket 传输层
│   ├── types.py             # xtquant 数据类型镜像
│   ├── constants.py         # xtquant 交易常量镜像
│   ├── client/
│   │   ├── data.py          # 远程行情客户端 (RemoteXtData)
│   │   └── trader.py        # 远程交易客户端 (RemoteXtTrader)
│   └── server/
│       ├── engine.py        # xtquant 引擎封装
│       ├── app.py           # WebSocket JSON-RPC 服务端
│       └── __main__.py      # 服务启动入口
└── skills/
    └── remote-miniqmt-data/  # TRAE Skill 封装
```

- **服务端**：运行在有 miniQMT 客户端的机器上，`python -m rmt.server` 启动（默认行情 ws://0.0.0.0:8001，交易 ws://0.0.0.0:8002）
- **客户端**：远程机器通过 `RemoteXtData("ws://server-ip:8001")` 异步连接获取数据
- **主项目数据层**：`miniqmt_provider.py` 直接调用本地 `xtquant.xtdata`，无需启动 remote 服务；remote 服务用于跨机部署场景

## 开发待办 (TODO)

### 1. 回测引擎 (Backtest Engine) — 已交付

事件驱动核心链路、Agent 友好 API、金融级可信验证（无未来函数）、Walk-Forward OOS、状态化风控（动态止损 / 追踪止盈 / 最大回撤）、扩展技术指标（MACD/RSI 等）、Dashboard 模块独立、akshare → miniqmt (xtquant) 数据源迁移均已完成（详见 ADR-005 与 git log）。

后续优化项暂无规划，按需添加。

### 0. ciccwm 财经数据 Provider — 已交付

详见 [ADR-006](docs/adr/006-ciccwm-data-provider.md)。已实现 `ciccwm_client.py`（HTTP 客户端 + 鉴权 + 解析，合并三个 skill 公共逻辑）+ `ciccwm_provider.py`（`DataProvider` Protocol + ciccwm 独占扩展方法），接入 `CompositeDataProvider` 降级链（DuckDB → miniqmt → ciccwm → akshare）。资金流向 / 涨跌幅排行 / 关联板块 / 热榜资讯为 ciccwm 独占能力，以 Protocol 外扩展方法暴露，失败不静默降级。凭证复用 `~/.config/ciccwm/config.json`。

### 0. 新闻事件推理引擎 — Phase 2 待启动（ADR-007 Phase 1 已完成）

基于物质-运动统一架构（[ADR-007](docs/adr/007-unified-substance-architecture.md)），为系统增加新闻事件推理能力。详见 ADR-007 实施计划。

- **Phase 1（已完成）**：SubstanceStore 核心 + 旧 `memory/` 移除。Pydantic Substance 模型 + 双索引（RetrievalIndex keyword/semantic + GraphIndex 邻接表）+ WorldInfo 激活引擎 + JSONL 持久化。`MemoryServiceImpl` 委托 SubstanceStore，Protocol 不变 → 消费方零改动。
- **Phase 2（待启动）**：多源采集器（Kimi 联网搜索 / 腾讯新闻 / ciccwm 热榜）+ 事件推理子图（collect→extract→propagate→conflict→save）+ 主图路由。L2 影响传播推理（LLM 辅助建立事件→影响标的因果链）。
- **Phase 3**：子图集成（stock_analysis / strategy_rd 调 `store.activate()` 注入事件上下文）+ Dashboard 事件流可视化。

### 2. 记忆系统 — v3.0 物质-运动架构重构（已完成，见 ADR-007）

ADR-007 Phase 1 已落地，下列 4 项已由物质-运动架构的原生能力替代：

- [x] **语义增强检索** → RetrievalIndex 双通道（keyword + semantic TF-IDF/embedding 融合）
- [x] **记忆压缩与总结** → `motion.compress()`（修复聚类算法）
- [x] **记忆衰减机制** → `motion.decay()`（按 form 配不同半衰期）
- [x] **冲突检测** → `motion.detect_conflicts()`（可配置词库，不再硬编码）

### 3. 策略研发与分析 (Strategy RD & Analysis)
- [ ] **HTR 假设树精炼**：将 `strategy_rd` 子图从线性进化循环升级为 Arbor HTR 六步循环（observe→ideate→select→dispatch→backpropagate→decide）+ 持久化假设树 + Walk-Forward held-out 合并门。详见 [ADR-010](docs/adr/010-hypothesis-tree-refinement.md)（5 阶段，依赖 ADR-007 落地）。
- [ ] **自动化参数寻优**：在 `strategy_rd` 子图中增加参数自动调优节点。基础设施已交付（`engine/parallel.py` + `param_grid.py`，见 [ADR-008](docs/adr/008-parallel-backtest-and-unified-templating.md)），subgraph 接入待后续轮。
- [ ] **多策略集成**：支持将多个研发成功的子策略组合成一个组合策略。
- [ ] **实时数据对接**：将 miniqmt 静态回测扩展到支持近实时的行情监控与预警。
- [ ] **增强分析视角**：在 `stock_analysis` 中增加行业对比视角和资金流向分析。

### 3.5 算子目录与算子研发 (Operator Catalog & Dev, 见 ADR-009)
- [ ] **gap_detector 接入**：strategy_rd `reflection` 后新增 `gap_detector` 节点，扫描 `improvement_suggestions` 与算子目录差异，产出 `OperatorSpec` 写 backlog，串联 operator_dev 异步闭环。
- [ ] **operator_dev register 写盘**：register 节点写 `.py` 到 `operators/<category>/`，产物持久化到代码库走 CI/审查（当前仅内存热注册）。
- [ ] **主图挂载**：`agent.py` 注册 operator_dev / strategy_optimization 子图入口，支持 CLI / 路由触发。
- [ ] **清理双套体系**：评估 `ml_strategy.py` / `strategy_templates.py` 是否可由算子目录 + 新 DSL 完全替代。
- [ ] **退役 evaluator**：策略全部迁移到算子路径后删除 `SafeExpressionEvaluator` + `_extract_field_names`（ADR-003 标记 Superseded by ADR-009）。

### 4. 工程化与质量 (Engineering & Quality)
- [ ] **集成测试增强**：针对 `strategy_rd` 的全链路流程编写更多端到端集成测试。
- [ ] **性能监控**：在 `MonitoringService` 中增加对 LLM Token 消耗和回测耗时的统计。
- [ ] **配置中心化**：将 `.env` 变量扩展为支持多环境配置的 `config.yaml`。
