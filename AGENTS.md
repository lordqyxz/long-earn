# long\_earn

自我进化的量化交易系统。基于 LangGraph 的证券交易顾问智能体，支持策略研发、股票分析和实时行情监控。

> **代码是第一真相**：本文档只记录稳定的开发规范与铁律约束。具体的目录结构、文件清单、字段数量、用例计数、行号引用、已知偏离等动态内容以代码本身为准，不在本文档维护。如需了解某模块的实现细节，请直接阅读对应源码。

---

## 一、项目定位

Long Earn 是 AI 驱动的量化交易研究平台，核心能力：

- **分层智能体编排** — MasterAgent（ReAct）负责任务分解与跨能力调度（ADR-016）
- **ToG 策略研发飞轮** — ResearchAgent 在 Substance/Ontology 上 explore→prune，以回测与统计门为不可跳过证据，写回经验形成飞轮（ADR-018）；假设树与 OOS 门保留为状态/硬约束（ADR-010 / ADR-015）
- **多视角股票分析** — 巴菲特 / 芒格 / 彼得林奇 / 费雪 / 资金流向五视角并行分析（ADR-012）
- **事件图谱基础设施** — `prepare_context` 自动激活事件上下文；缺省时触发采集推理（ADR-007 / ADR-018）
- **自我进化（规划中）** — 策略经验沉淀到物质-运动统一架构记忆系统（ADR-007 / ADR-017 Deferred）
- **内嵌回测引擎** — 事件驱动引擎直接集成在主项目中，YAML DSL 描述策略，支持进程级并行回测（ADR-005 + ADR-008）
- **实时行情监控** — 实时行情 Provider（显式主源 miniqmt，次源 ciccwm）+ 价格阈值告警（ADR-011 / ADR-018）

运行时总览与分层图见 [docs/architecture.md](docs/architecture.md)。

---

## 二、架构与设计原则

### 2.1 整洁架构与领域划分

**整洁架构**：依赖方向单向收敛——`tools` → `services` → `domain`，外层可知内层，内层不知外层。

**长期可维护性优先级**：架构整洁是第一原则，优先于向后兼容、短期便利、局部实现成本和修改范围。修改时应以恢复或强化既有领域边界、依赖方向和接口职责为目标；不得为保留已不合理的调用路径、旧接口或过渡逻辑而引入反向依赖、跨上下文耦合或双轨实现。需要不兼容变更时，应完整迁移项目内调用方并删除废弃路径；只有存在明确的外部兼容承诺时，才保留有退出期限的适配层。不得将“最小修改面”作为设计或实施原则；当完整清理或重构是维持架构整洁所必需时，应按职责边界完成改造，不受改动范围约束。

**LLM 生成代码质量**：架构决策应将 LLM 能否稳定生成、理解、审查和安全修改代码作为参考项。优先选择职责单一、边界显式、接口契约清楚、依赖可追踪、命名一致且易于局部验证的设计，降低隐式约定、过深控制流、跨模块共享可变状态和非必要元编程。该参考项服务于长期可维护性，不得凌驾于架构整洁、领域边界或正确性之上。

**DDD 划界上下文，而非分层**：每个 `src/long_earn/<上下文>/` 目录是一个独立业务领域（`backtest` / `strategy_rd` / `stock_analysis` / `substance` / `event_inference` / `operator_dev` / `ontology` / `skills`），拥有自己的领域模型与通用语言，跨上下文通过服务接口通信。上下文内部可分层落地（如 `backtest/` 下 `domain` 领域模型 + `engine` 领域服务 + `data` 基础设施），分层是手段，领域边界才是目的。

**依赖注入容器**：`RuntimeContext` 是 DI Container，下游组件接收**已构造完毕**的服务实例（非 `Service | None`）。允许 `Service | None` 仅限**容器初始化中间态**；业务节点接受非空依赖。具体字段清单见 `src/long_earn/config.py` 中 `RuntimeContext` 定义。

### 2.2 依赖注入

所有 Agent 和子图必须通过 `context` 参数初始化：

```python
# 正确
context = create_runtime_context()
agent = ResearchAgent(context=context)  # 或 MasterAgent(context=context)

# 错误 — 禁止无 context 创建
agent = ResearchAgent()
```

### 2.3 关键架构约束

- **服务接口**：定义为 `Protocol` 类（`services/__init__.py`），具体实现在各 `*_service.py` 中。测试中用 Mock 替代真实服务，无需 API 调用。
- **上下文初始化**：`create_runtime_context()` 创建服务实例但不初始化记忆；`initialize_context()` 包含完整初始化（额外调用 `memory.initialize()` 加载记忆）。
- **import-linter 合约**：`backtest.data` 不依赖上层模块，`services` 不依赖 `tools`，`substance` 不依赖上层，`core` 不依赖上层。
- **统一存储位置**：所有生成数据（回测缓存、记忆库、假设树、策略研发产物）的落盘路径由 `core/storage.py` 统一裁决，唯一控制变量为 `LONG_EARN_DATA_DIR` 环境变量（默认 `D:/dev/long-earn-data`，repo 同级 `long-earn-data`）。各模块通过 `core/storage.py` 提供的辅助函数获取路径，不得自行 `Path.home()` 或硬编码。`AppConfig` 的存储相关字段从 `core.storage` 派生。

### 2.4 核心能力基线

> 「质量门槛」（见第四节）是代码层硬性检查；「能力基线」是系统层验证标准——任何修改不得破坏既有基线，发现偏离须在 TODO.md 登记并按威胁程度排期修复。基线的具体阈值、验证位置、已知偏离以代码为准（见 `tests/unit/` 与对应源码），本节只记录四个核心维度的目标：

1. **策略生成与持续进化**：ResearchAgent（ToG）能产出可回测的策略 YAML；合并须通过 held-out OOS 与 ADR-015 统计门，保证策略质量单调提升。
2. **回测金融级可靠性**：事件驱动引擎在架构层面绝对杜绝未来函数，撮合/风控/审计可追溯、可重放。详见 [ADR-005](docs/adr/005-event-driven-backtest.md)。
3. **数据利用充分性**：PostgreSQL Cache + 显式多源（miniqmt 面板 / ciccwm 情报 / 实时）按能力点名接入，PIT 对齐严格，缓存加速可观测。
4. **多核 CPU 利用**：并行回测编排 + 共享数据底座 + 子进程隔离下载，发挥多核优势。

---

## 三、编码规范

### 3.1 基本规则

- Python 3.13 严格版本（`requires-python = "==3.13.*"`）
- 所有函数和参数必须添加类型注解
- **尽量避免使用 `Any` 类型**：内部数据结构用 `@dataclass`（`from dataclasses import dataclass`）建模，外部/动态数据用 Pydantic 模型建模；`Any` 仅作为最后兜底（如第三方库返回值、JSON 反序列化中间态），并注释说明原因
- `str` 类型参数默认值 `""`
- 中文注释和文档字符串

### 3.2 代码风格与检查

- 代码格式和检查：ruff（format + lint + McCabe 圈复杂度 ≤15 + Pylint 规则 + 未使用参数检测，88 字符行宽）
- 架构依赖校验：import-linter（数据层不依赖上层、服务层不依赖 tools）
- 类型检查：pyright 全项目静态类型检查（`uv run pyright src/`）为最终关口；Serena LSP 单文件诊断（`mcp__serena__get_diagnostics_for_file`）作为编辑期快速反馈回路（详见第四节「质量门槛」）

### 3.3 日志

**日志统一使用 loguru**：禁止 `import logging` / `logging.getLogger`；所有模块直接 `from loguru import logger`。日志格式由 `LoggerServiceImpl` 统一配置（带颜色、时间、模块名、函数名、行号）。脚本入口需 `logger.remove()` 后 `logger.add(sys.stderr, ...)` 配置，格式与 `LoggerServiceImpl` 一致。

### 3.4 LangGraph 节点

节点只需返回要更新的 key，不需要返回完整状态：

```python
def my_node(state: State, context: RuntimeContext):
    return {"result": "..."}  # 自动合并到全局状态
```

### 3.5 Prompt 管理

使用 `MarkdownPromptTemplate` 加载 `.md` 文件。变量使用 jinja2 `{{ variable }}` 语法（ADR-011）；底层由 langchain `PromptTemplate(template_format='jinja2')` 渲染，默认不 HTML 转义（消费者是 LLM 不是浏览器），与 JSON `{}` 不冲突。frontmatter 可选，支持 `version`/`description` 字段；多消息结构用 frontmatter `messages` 字段 + `MarkdownChatPromptTemplate`（ADR-011 阶段 4）。

```python
from long_earn.core.prompt_loader import MarkdownPromptTemplate
prompt_template = MarkdownPromptTemplate("my_prompt.md", caller_file=__file__)
prompt = prompt_template.format(query=query)
```

**禁止**：不再使用 `${var}` 占位符或 `core/render.py` 自定义渲染器（ADR-008 A 部分已被 ADR-011 废弃）。CI grep 卡口防止回退。

**约定**：每个 Agent 的 prompt `.md` 文件与该 Agent 的 `.py` 文件放在同一目录下（例如 `strategy_research_agent.py` 与 `strategy_research_prompt.md` 同在 `agents/` 目录）。`MarkdownPromptTemplate` 基于 `caller_file` 解析相对路径，移动 `.md` 文件后需同步修改对应 Agent 中的文件名。

---

## 四、质量门槛与测试

### 4.1 质量门槛（按强弱排序）

1. **`uv run pyright src/` 全局零错**（**首要类型检查工具**）：静态类型检查最终关口，`Error` 级别必须为零。编辑代码符号后先用 Serena LSP 单文件诊断（`mcp__serena__get_diagnostics_for_file`）快速反馈，收尾以 pyright 全项目扫描验证。
2. **`uv run ruff check src/` 全局零错**：风格、复杂度（McCabe ≤15）、Pylint 规则。
3. **`uv run lint-imports`**：架构依赖契约（数据层不依赖上层、服务层不依赖 tools）必须保持 0 broken。
4. **`uv run pytest tests/unit/`**：单元测试全绿。
5. **`uv run python scripts/check_llm_call_sites.py`**：LLM 调用点分层卡口（ADR-021）——LLM 推理只允许出现在 agent 节点层，白名单外出现即失败。

> pyright 为全项目权威类型检查工具，不使用 mypy CLI。第三方库（pandas / OpenAI SDK 等）类型推断噪音以 `cast()` 或显式注解收窄，禁止用 `# type: ignore` 掩盖。

### 4.2 测试组织

- **单元测试**：`tests/unit/` 下按模块组织
- **集成测试**：`tests/integration/` 需配置 `.env` 环境变量

### 4.3 测试编写原则

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

### 4.4 代码评审规则归结

按路径组织的评审规则清单维护在 [docs/review-rules.md](docs/review-rules.md)（评审 subagent 依据 AGENTS.md + ADR 动态归结、稳定后写回的产物）；本文件与 ADR 为其上游真相源，冲突时以上游为准。

---

## 五、量化数据分割规范（铁律）

> **此规范是量化分析的铁律，防止过拟合和前视偏差。所有策略研发、回测、优化必须严格遵守。**

三段式数据分割，每段有且仅有指定用途，**不得交叉使用**：

| 区间 | 环境变量 | 默认值 | 用途 | 约束 |
|------|----------|--------|------|------|
| **训练集 (In-Sample)** | `TRAIN_START` / `TRAIN_END` | 2022-01-01 ~ 2024-12-31 | 策略研发、因子选择、参数寻优 | 自由使用，可反复回测 |
| **测试集 (Out-of-Sample)** | `TEST_START` / `TEST_END` | 2025-01-01 ~ 2026-03-24 | Walk-Forward OOS held-out 验证 | **仅用于合并决策**，不得用于参数调优 |
| **验证集 (Forward)** | `VALIDATION_START` / `VALIDATION_END` | 2026-03-25 ~ 2026-06-25 | 前瞻验证，模拟实盘 | **开发阶段绝对禁止使用**，仅在最终评估时触碰一次 |

### 规则

1. **训练集可反复使用**：策略研发、因子筛选、参数网格寻优、ToG 探索等全部在训练集上进行。
2. **测试集仅在合并门触碰**：候选策略合并前须跑 Walk-Forward OOS / 统计门（ResearchAgent `run_oos_gates` 或 HTR `_decide`），`oos_score > current_best + threshold` 才合并。测试集**不得**用于参数调优或日常回测。
3. **验证集最后触碰一次**：最终评估使用验证集，**整个研发过程中仅此一次**。验证集业绩是系统对外报告的唯一指标。
4. **比例参考**：训练 ~55%、测试 ~30%、验证 ~15%（按时间跨度）。可根据数据量调整，但三段必须分离。
5. **禁止前视偏差**：训练集策略不得隐式或显式地"偷看"测试集/验证集的结果来调整参数。held-out 门就是此规则的系统级保证。

`AppConfig` 提供 `train_start_date` / `train_end_date` / `test_start_date` / `test_end_date` / `validation_start_date` / `validation_end_date` 字段，从环境变量读取。具体字段定义与默认值见 `src/long_earn/config.py`。

---

## 六、关键 Gotchas

> 这些是容易踩坑、不符合直觉、或必须牢记的实现事实。

### 6.1 回测引擎

- **回测引擎内嵌**：回测引擎已整合到主项目（`src/long_earn/backtest/`），无需启动外部 HTTP 服务。策略通过 YAML DSL 描述，引擎直接调用。
- **仅支持多头、不支持做空**：`Portfolio` 仅维护 `cash` + 多头 `positions`，无 `short_positions`；DSL `weights` 仅支持 `equal`。弱市下唯一可用风控是"空仓 + 止损 + 最大回撤清仓"，无法对冲/做空/动态降仓。
- **表达式安全（已退役）**：ADR-003 的 AST 白名单求值器已于 2026-07 收尾时删除（Superseded by ADR-009）。所有策略走算子目录路径（[ADR-009](docs/adr/009-operator-catalog-and-operator-dev-subgraph.md)），以 `prove_causality` 因果性数学证明 + 算子目录白名单共同保证无未来函数。DSL 解析期强制拒绝旧式 `factors` 字段与 `filter`/`rank`/`expression` 信号类型。
- **算子更名须连带迁移 YAML（铁律）**：算子注册名（`Operator.name`）是策略 YAML `op` 字段的契约 ID。发现名实不符（如实现是价格因子却冒用基本面名）时，**必须改名并同步全部策略 YAML / 模板 / 测试引用**，不得「只改正文保留误导 ID」。旧名登记于 `OPERATOR_RENAMES`，`get_operator(旧名)` 抛明确迁移错误（不静默别名）。例：`roe_quality` → `return_quality`，`gross_margin_stability` → `price_stability`（2026-08-30）。
- **回测记录标签机制（铁律）**：回测审计记录（`backtest_audit.logs`）支持 run 级 `tags`（存于 RUN_START payload.tags，常量 `RUN_TAG_TEST = "test"`）。**测试/冒烟回测写共享 PG 时必须携带专用标签 `test`**：经引擎跑测试传 `tags=["test"]`（`EventDrivenBacktestEngine.run(tags=...)`），经 `AuditLogger` 直写用 `log_run_start({"tags": ["test"]})`。审计库清理接口（`DELETE /api/runs/clean`，口径见 `src/long_earn/app/analyzer.py::get_empty_or_error_runs`）以「带 test 标签」识别测试污染（替代旧 run_id 前缀启发式 run-/t-/conc-/rw-），并保留结构性无效口径（无 FILL 空跑 / RUN_ERROR / 无 RUN_END 孤儿 / 成交笔数 < 5）。生产回测不传 tags，不会被 test 标签清理误伤。

### 6.2 数据层

- **数据库引擎层（2026-08-30 起）**：PG 连接与事务统一走 `core/db.py`（SQLAlchemy 2.0 Core，`postgresql+psycopg` 方言，进程级单例 Engine + 池化；连接参数仍由 `core/pg.py` 单一裁决）。读路径 `read_connection()`（归还自动 rollback，消除 aborted-transaction 中毒）、写路径 `write_transaction()`（engine.begin 语义）；psycopg COPY 批量装载经 `raw_psycopg_connection()` 逃生舱下沉原生协议。**DataCache 已迁移**；审计/记忆库/分析器仍走 psycopg 直连（第二阶段迁移）。SQL 双轨制：批量分析型负载（COPY/UNION 聚合/窗口函数/PIT as-of）保持驱动级原生 SQL（`_exec` 适配 psycopg `%s` 习惯），**不用 ORM**（会话模型与批量负载冲突）；4 张简单表（`universe_constituents` / `instrument_details` / `financial_sync_watermark` / `panel_dirty`）用 Core `Table` 元数据——DDL 经 `create_all` 单一真相源生成、upsert/update 走 `on_conflict_do_update` 等 Core 表达式（列名拼写进 pyright 检查范围）。DDL 保持「构造即建表」幂等模式，不引入 alembic。
- **数据缓存**：回测引擎使用 PostgreSQL 本地缓存（`long_earn` 库，Docker 容器 `pg`，连接参数由 `core/pg.py` 统一裁决），全量数据（沪深A股 + 沪深ETF 行情/财务、基准指数行情）通过 `scripts/download_data.py` 脚本从 miniqmt (xtquant) 下载。regime 门控 benchmark 四指数（000300/000905/000001/399006）已纳入正式管线增量维护（`DataIngestionService.INDEX_QUOTES` 显式点名），不依赖临时脚本。**ADR-018**：面板路径为 PostgreSQL Cache + 显式主源 miniqmt；失败即失败并打日志，不做静默跨源降级。ciccwm 通过 `MarketIntelligenceProvider` 提供情报独占能力；akshare 仅在调用方显式点名时使用。正常回测时数据提供者会按需增量补充缓存，但不得手动 DELETE/DROP 缓存内容。
- **数据层三组接口**：`DataConnector`（历史面板）、`MarketIntelligenceProvider`（市场情报，ciccwm 独占）、`RealtimeDataProvider`（实时行情）面向业务分离，不混用。具体能力清单与方法签名以 `services/__init__.py` 与 `backtest/data/connector.py` 代码为准。
- **并发下载工程实践**：`DataIngestionService` 支持 `--max-workers`（默认 4，范围 1-8），采用 `subprocess.run` 子进程隔离每批下载任务（防 xtquant C++ SIGABRT 崩溃影响主进程）+ `ThreadPoolExecutor` 并发子进程生成临时文件，主进程串行写入 PostgreSQL 避免锁冲突。子进程内绕过 `MiniQmtDataProvider` 初始化（避免 PG 连接冲突），通过 stdout 解析结果。
- **增量同步判定铁律（防死循环）**：增量判定必须区分三态并按事件密度选型——**数据状态**（`max(date)` 等最后数据点）只适用于日更/高频域（行情，上游推进即自愈）；**检查水位**（`checked_until`「上次查过的时间」）是稀疏事件域（财务公告等）的必需品；**存在性**（有无即判）适用于一次性内容（标的详情）。**禁止只用 `today - max(announce_date) > 阈值` 判 stale**：无新公告的沉默股票会形成「判 stale → 下载 0 行 → 状态不推进 → 再判 stale」死循环（2026-08-30 实证：4620 只 × 每次同步重复下载约 20 分钟）。配套规则：判定为「需检查」的 symbol 在批次下载成功后**必须推进检查水位**（空结果也推进），失败批次保留水位待重试。同款判定逻辑在多处出现时必须共享同一水位（如财务启动同步与回测读路径 `ensure_financial_cache`）。

### 6.3 记忆系统

- 基于物质-运动统一架构（ADR-007），事件/关系/知识/策略经验统一为 `Substance`，检索走 WorldInfo 关键词触发 + 语义相似度双通道。持久化至 PostgreSQL 的 `substances` 表（PG 事务式存储，`save_many` 幂等 UPSERT，原子追加）。旧 `memory/` 模块（ADR-004）已删除。
- **ADR-018**：研究/分析入口统一走 `RuntimeContext.prepare_context(query)`（激活事件）。**ADR-021**：该入口是纯确定性激活（返回结构化 `ContextActivation`，不内嵌推理）；miss 时的采集推理由调用方 agent 显式构造事件推理子图触发（ResearchAgent `_prepare_event_context` / app 事件管线）。`Connector` 注入 `memory_provider`。

### 6.4 集成测试

- 运行 `tests/integration/` 或根级集成测试文件前需配置环境变量（见第八节）。

### 6.5 Agent 分层（ADR-021）

- **LLM 推理只存在于 agent 节点层**（LangGraph 图节点 / ReAct 工具闭包 / persona 节点）；`services` / `tools` / 数据基础设施等脚手架层不得内嵌 LLM 调用，只产确定性、类型化的结构化中间态（dataclass）。
- **确定性先行、LLM 兜底**：路由、分类、解析、文件/路径/标的选择等规则可判定的，必须代码先行（如 stock_analysis `resolve_stock_ref`：正则 → 字典 → LLM）；兜底点必须在 agent 层。
- 执法卡口 `scripts/check_llm_call_sites.py`（已入 CI）：白名单外出现 LLM 调用标记即失败；白名单扩容须在脚本中登记架构理由。

---

## 七、常用命令

```sh
uv sync                                    # 安装依赖
uv run python -m long_earn                 # 运行项目
uv run pytest tests/ -v                    # 运行全部测试（含根级测试文件）
uv run pytest tests/unit/ -v               # 仅运行单元测试
uv run pytest tests/integration/ -v        # 仅运行集成测试（需 .env 配置）
uv run ruff check .                        # 代码检查（lint + 复杂度）
uv run ruff format .                       # 代码格式化
uv run lint-imports                        # 架构依赖校验
uv run python scripts/check_deprecated_syntax.py  # 退役语法 grep 卡口（检测 ${var} / 路径 2 回退）
uv run python scripts/check_llm_call_sites.py    # LLM 调用点分层卡口（ADR-021，agent 层白名单外即失败）
uv run python scripts/download_data.py     # 全量下载沪深A股+ETF行情、财务数据及基准指数行情到 PostgreSQL 缓存（需 miniQMT 连接）
uv run python scripts/download_data.py --max-workers 4  # 并发下载（subprocess 隔离防 xtquant SIGABRT 崩溃，1-8，默认 4）
```

> **缓存保护约定**：PostgreSQL `long_earn` 库的 `price_daily` / 财务表是全量下载的权威数据源，**不得主动修改**（如手动 DELETE/DROP 或在回测中随意覆盖），除非有明确必要理由（如数据损坏、需要增量更新）。全量刷新通过上述 `download_data.py` 脚本显式执行。旧 DuckDB 源库已完成迁移并删除（PostgreSQL 为唯一权威存储，历史迁移见 ADR-019）。数据目录由 `LONG_EARN_DATA_DIR` 环境变量控制（默认 `D:/dev/long-earn-data`）。

---

## 八、环境变量与外部服务

### 8.1 环境变量

环境变量文档**自包含在** `src/long_earn/config.py` 的 `AppConfig` 类 docstring 中（业务配置 / 运行时控制 / 第三方 API Key 三类），不在本文件重复维护。新增环境变量时同步更新该 docstring。

### 8.2 LLM 服务启动

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

---

## 九、架构决策记录 (ADR)

运行时总览图：[docs/architecture.md](docs/architecture.md)。

ADR 索引与详细说明见 [docs/adr/README.md](docs/adr/README.md)。

---

## 十、待办清单

详见 [TODO.md](TODO.md) — 按重要性 + 威胁程度统一排序，合并功能开发待办与合规审计。
