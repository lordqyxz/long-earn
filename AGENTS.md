# long\_earn

自我进化的量化交易系统。基于 LangGraph 的证券交易顾问智能体，支持策略研发、股票分析和实时行情监控。

> **代码是第一真相**：本文档只记录稳定的开发规范与硬性约束。目录结构、字段清单、行号、已知偏离等动态内容以源码为准。  
> 实现层易错事实见 [docs/gotchas.md](docs/gotchas.md)；ADR 编写规范见 [docs/adr/AGENTS.md](docs/adr/AGENTS.md)（各篇元数据为 frontmatter）；待办见 [TODO.md](TODO.md)。

---

## 一、项目定位

Long Earn 是 AI 驱动的量化交易研究平台，核心能力：

- **分层智能体编排** — MasterAgent（ReAct）任务分解与跨能力调度（ADR-016）
- **ToG 策略研发正反馈闭环** — ResearchAgent explore→prune，以回测与统计验证门控为不可跳过证据（ADR-018 / ADR-022）；假设树状态保留；ADR-010 HTR 编排已 Deprecated
- **多视角股票分析** — 五视角并行（ADR-012）
- **事件图谱** — `prepare_context` 确定性激活；miss 时由 agent 显式触发采集（ADR-007 / ADR-021）
- **自我进化（规划中）** — Deferred；解锁节奏 ADR-022 L0–L3（规格 ADR-017）
- **内嵌回测引擎** — 事件驱动 + YAML DSL + 进程级并行（ADR-005 / ADR-008）
- **实时行情监控** — 主源 miniqmt、次源 ciccwm + 价格告警（ADR-011 / ADR-018）

运行时总览：[docs/architecture.md](docs/architecture.md)。

---

## 二、架构与设计原则

### 2.1 整洁架构与领域划分

**整洁架构**：依赖单向收敛——`tools` → `services` → `domain`。

**长期可维护性优先**：架构整洁优先于向后兼容、短期便利与「最小修改面」。不兼容变更须完整迁移调用方并删除废弃路径；仅在有明确外部兼容承诺时保留有退出期限的适配层。

**LLM 生成代码质量**：优先职责单一、边界显式、契约清楚、依赖可追踪、命名一致、易于局部验证的设计；不得凌驾于架构整洁与正确性之上。

**DDD 划界上下文**：每个 `src/long_earn/<上下文>/` 为独立业务领域（`backtest` / `strategy_rd` / `stock_analysis` / `substance` / `event_inference` / `operator_dev` / `ontology` / `skills`），跨上下文经服务接口通信。

**依赖注入**：`RuntimeContext` 为 DI 容器；业务节点接受已构造完毕的非空依赖。字段见 `src/long_earn/config.py`。

### 2.2 依赖注入

```python
context = create_runtime_context()
agent = ResearchAgent(context=context)  # 禁止无 context 构造
```

### 2.3 关键架构约束

- 服务接口：`Protocol`（`services/__init__.py`），实现在各 `*_service.py`。
- `create_runtime_context()` 不初始化记忆；`initialize_context()` 含 `memory.initialize()`。
- import-linter：`backtest.data` / `substance` / `core` 不依赖上层；`services` 不依赖 `tools`。
- 落盘路径唯一由 `core/storage.py` + `LONG_EARN_DATA_DIR` 裁决（默认 repo 同级 `long-earn-data`）。

### 2.4 核心能力基线

修改不得破坏下列基线；偏离登记 [TODO.md](TODO.md)。阈值与验证位置以测试与源码为准：

1. **策略质量**：ToG 产出可回测 YAML；合并经 held-out OOS 与 ADR-022 统计验证门控。
2. **回测可信性**：事件驱动杜绝未来函数；撮合/风控/审计可追溯（ADR-005）。
3. **数据**：PostgreSQL Cache + 显式多源；PIT 严格；失败即失败（ADR-018）。
4. **并行**：多核回测编排 + 子进程隔离下载（ADR-008）。

---

## 三、编码规范

- Python `==3.13.*`；全量类型注解；避免 `Any`（内部用 `@dataclass`，外部用 Pydantic）。
- `str` 参数默认 `""`；中文注释与文档字符串。
- ruff（format + lint + McCabe ≤15，88 列）；import-linter；`uv run pyright src/` 为零错关口。
- 日志：仅 loguru（`from loguru import logger`），由 `LoggerServiceImpl` 统一格式。
- LangGraph 节点只返回待更新的 key。
- Prompt：`MarkdownPromptTemplate` + jinja2 `{{ var }}`（ADR-011）；禁止 `${var}` / 自研 `core/render.py`。`.md` 与对应 Agent `.py` 同目录。

---

## 四、质量门槛与测试

按强弱：`pyright src/` → `ruff check src/` → `lint-imports` → `pytest tests/unit/` → `scripts/check_llm_call_sites.py`（ADR-021）。

- 单元测试：`tests/unit/`；集成测试：`tests/integration/`（需 `.env`）。
- 只测接口契约与系统关键环节；不测简单数据类、显而易见的错误路径、实现细节、大量 mock 的端到端子图。
- 评审规则清单：[docs/review-rules.md](docs/review-rules.md)（上游为本文与 ADR）。

---

## 五、量化数据分割（硬性约束）

| 区间 | 环境变量 | 默认跨度 | 用途 |
|------|----------|----------|------|
| 训练 | `TRAIN_*` | 2022-01-01 ~ 2024-12-31 | 研发与寻优，可反复使用 |
| 测试 | `TEST_*` | 2025-01-01 ~ 2026-03-24 | 仅合并门 Walk-Forward OOS |
| 验证 | `VALIDATION_*` | 2026-03-25 ~ 2026-06-25 | 最终评估触碰一次；研发期禁止 |

测试集合并经 `run_oos_gates`（ADR-022）；禁止用测试/验证集调参。字段见 `AppConfig`。

---

## 六、常用命令

```sh
uv sync
uv run python -m long_earn
uv run pytest tests/unit/ -v
uv run ruff check src/
uv run ruff format .
uv run lint-imports
uv run pyright src/
uv run python scripts/check_deprecated_syntax.py
uv run python scripts/check_llm_call_sites.py
uv run python scripts/download_data.py --max-workers 4
```

PostgreSQL `long_earn` 权威缓存不得随意 DELETE/DROP；全量刷新仅经 `download_data.py`。`LONG_EARN_DATA_DIR` 默认 `D:/dev/long-earn-data`。

---

## 七、环境变量与 LLM

业务 / 运行时 / API Key 文档自包含于 `AppConfig` docstring（`src/long_earn/config.py`）。

默认 Ollama：`ollama serve`，`http://localhost:11434`。切换 DashScope / OpenAI 时配置对应 Key，无需本机 Ollama。

---

## 八、文档索引

| 文档 | 路径 |
|------|------|
| 实现 Gotchas | [docs/gotchas.md](docs/gotchas.md) |
| ADR 编写规范 | [docs/adr/AGENTS.md](docs/adr/AGENTS.md) |
| 运行时总览 | [docs/architecture.md](docs/architecture.md) |
| 评审规则 | [docs/review-rules.md](docs/review-rules.md) |
| 研究与论文 | [docs/research/papers/README.md](docs/research/papers/README.md) |
| 待办 | [TODO.md](TODO.md) |
