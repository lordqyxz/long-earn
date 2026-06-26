# TODO.md — 长期演进开发计划

> 最后更新：2026-06-26
> 当前分支：v1.0.0（开发中）

---

## 架构分层原则

项目按 **可替换性** 分为三层，LLM 生成内容与不变框架严格解耦：

```
┌──────────────────────────────────────────────┐
│  Layer 3: LLM 生成层                         │
│  策略逻辑 / ML 模型 / 特征工程 / Prompt      │
│  → 依赖抽象接口，LLM 可安全生成和迭代          │
├──────────────────────────────────────────────┤
│  Layer 2: 分析 & 可视化层                     │
│  Dashboard / Analyzer / Visualization API    │
│  → Agent 和 Dashboard 共享同一套审计接口       │
├──────────────────────────────────────────────┤
│  Layer 1: 不变框架 (Stable Framework)         │
│  事件引擎 / 撮合 / 风控 / 审计 / 数据适配      │
│  → Python 原生实现，按需优化热点路径              │
└──────────────────────────────────────────────┘
```

**关键约束**：
- Layer 3 只能依赖 Layer 1 的抽象接口（Protocol / ABC），绝不直接依赖具体实现
- Layer 1 保持 Python 原生实现，热点路径可通过 NumPy 向量化优化
- Layer 2 通过 `BacktestAnalyzer` + `AuditProvider` 消费数据，与 Agent 共享同一套接口

---

## Layer 1: 不变框架 (Stable Framework)

### 1.1 领域模型 ✅ 基本完成

| 模块 | 文件 | 状态 | 说明 |
|------|------|------|------|
| 领域实体 | `backtest/domain/entities.py` | ✅ | Event 体系、Order、Position、PerformanceMetrics |
| 领域异常 | `backtest/domain/exceptions.py` | ✅ | 异常层次 |
| 抽象接口 | `backtest/domain/interfaces.py` | ✅ | AuditProvider Protocol、AuditRecord |

### 1.2 事件引擎核心 ✅ 基本完成

| 模块 | 文件 | 状态 | 说明 |
|------|------|------|------|
| 事件循环 | `backtest/engine/core.py` | ✅ | EventDrivenBacktestEngine，T 维度迭代 × S 维度向量化 |
| 引擎核心测试 | `backtest/test_engine.py` | ✅ | 10 个 unittest 用例覆盖主流程/风控/Walk-Forward |
| 可见性守护 | `backtest/engine/visibility.py` | ✅ | VisibilityGuard，杜绝未来函数 |
| 撮合经纪人 | `backtest/engine/broker.py` | ✅ | Broker + TradingCostConfig（滑点/佣金/印花税） |
| 组合管理 | `backtest/engine/portfolio.py` | ✅ | Portfolio，信号→订单→成交→持仓更新 |
| Walk-Forward | `backtest/engine/core.py` | ✅ | walk_forward_run()，时序交叉验证 |

**待开发**：
- [ ] **1.2.1 高性能数据喂入**：Polars → 考虑 Arrow IPC 流式加载，支持百万级行数据
- [x] **1.2.2 高级订单类型**：限价单 (Limit Order)、止损单 (Stop Order)、止损限价单 (Stop-Limit)、OCO 互斥订单（2026-05-20 完成，21 个测试）
- [ ] **1.2.3 多资产支持**：期货（保证金）、ETF、可转债的持仓和撮合模型

### 1.3 审计系统 ✅ 基本完成

| 模块 | 文件 | 状态 | 说明 |
|------|------|------|------|
| DuckDB 审计存储 | `backtest/engine/audit.py` | ✅ | DuckDBAuditProvider，审计日志持久化 |
| 审计记录器 | `backtest/engine/audit.py` | ✅ | AuditLogger，引擎事件→AuditRecord |
| 内存审计跟踪 | `backtest/engine/core.py` | ✅ | InMemoryAuditTrail，测试用 |

**待开发**：
- [ ] **1.3.1 审计查询性能**：大数据量下因果链查询优化（索引策略）
- [ ] **1.3.2 审计可视化**：因果链的时间线视图（Span 瀑布图）

### 1.4 可观测性 ✅ 基本完成

| 模块 | 文件 | 状态 | 说明 |
|------|------|------|------|
| Telemetry | `backtest/engine/telemetry.py` | ✅ | OtelSpanContext，span 链路追踪 |
| 引擎插桩 | `backtest/engine/telemetry.py` | ✅ | instrument_engine() + 事件钩子 |

**待开发**：
- [ ] **1.4.1 正式 OpenTelemetry 集成**：替换轻量模拟为真实 OTLP exporter
- [ ] **1.4.2 实时监控指标**：Prometheus metrics（回测吞吐量、延迟分布）

### 1.5 数据适配层 ✅ 基本完成

| 模块 | 文件 | 状态 | 说明 |
|------|------|------|------|
| DuckDB 缓存 | `backtest/data/cache.py` | ✅ | 本地数据缓存 |
| Akshare 数据源 | `backtest/data/provider.py` | ✅ | A 股数据获取 |
| 股票池 | `backtest/data/universe.py` | ✅ | 股票池管理 |

**待开发**：
- [ ] **1.5.1 多数据源适配**：支持 Tushare、Wind、Bloomberg 等数据源
- [ ] **1.5.2 数据质量校验**：缺失值检测、异常价格过滤、复权校验

### 1.6 策略接口 ✅ 完成

| 模块 | 文件 | 状态 | 说明 |
|------|------|------|------|
| 策略基类 | `backtest/engine/strategy.py` | ✅ | BaseStrategy (ABC)，on_bar + init + state |
| 可见性上下文 | `backtest/engine/visibility.py` | ✅ | VisibilityContext，只读数据访问 |

> 这是 **Layer 1 → Layer 3 的关键合约**。所有 LLM 生成的策略都继承 `BaseStrategy`。

---

## Layer 2: 分析 & 可视化层 (Dashboard)

### 2.1 回测分析器 ✅ 完成

| 模块 | 文件 | 状态 | 说明 |
|------|------|------|------|
| 审计查询 | `dashboard/analyzer.py` | ✅ | BacktestAnalyzer，SQL 查询 + Polars 分析 |
| 运行摘要 | `dashboard/analyzer.py` | ✅ | get_run_summary() |
| 因果链追踪 | `dashboard/analyzer.py` | ✅ | trace_trade_lifecycle() |
| 失败分析 | `dashboard/analyzer.py` | ✅ | analyze_rejected_events() |
| 权益曲线导出 | `dashboard/analyzer.py` | ✅ | export_equity_curve() |
| 交易日志导出 | `dashboard/analyzer.py` | ✅ | export_trade_journal() |
| 信号历史导出 | `dashboard/analyzer.py` | ✅ | export_signal_history() |
| 仪表盘数据 | `dashboard/analyzer.py` | ✅ | export_dashboard_data() |
| 多策略对比 | `dashboard/analyzer.py` | ✅ | compare_runs() — 2026-05-18 新增 |
| 风险指标 | `dashboard/analyzer.py` | ✅ | get_risk_metrics() — VaR/CVaR/最大回撤天数 |
| 日收益率 | `dashboard/analyzer.py` | ✅ | get_daily_returns() — 从审计日志推导 |

**待开发**：
- [ ] **2.1.1 绩效归因分析**：Brinson 归因、因子归因、行业归因
- [ ] **2.1.2 风险分析增强**：压力测试、情景分析
- [ ] **2.1.3 策略对比增强**：绩效矩阵热力图、相关性矩阵

### 2.2 可视化模块 ✅ 已抽取为独立模块

| 模块 | 文件 | 状态 | 说明 |
|------|------|------|------|
| Dashboard 包 | `dashboard/__init__.py` | ✅ | 统一导出 BacktestAnalyzer, BacktestAPIHandler, serve_visualization |
| 分析器 | `dashboard/analyzer.py` | ✅ | 从 tools/backtest_analyzer.py 迁移 |
| REST API | `dashboard/api.py` | ✅ | 从 tools/visualization_api.py 迁移 |
| Dashboard HTML | `dashboard/templates/dashboard.html` | ✅ | 从 tools/backtest_dashboard.html 迁移 |
| 向后兼容 | `tools/__init__.py` | ✅ | re-export 保持旧路径可用 |

> Dashboard 模块与 Agent 共享同一套审计接口（`AuditProvider` Protocol + `BacktestAnalyzer`）。

**待开发**：
- [ ] **2.2.1 Dashboard HTML 交互增强**：Chart.js CDN 图表 + run 选择器下拉
- [ ] **2.2.2 WebSocket 实时推送**：回测运行中实时推送权益曲线（SSE/WebSocket）
- [ ] **2.2.3 报告导出**：PDF/HTML 回测报告自动生成

---

## Layer 3: LLM 生成层

### 3.1 ML 策略框架 ✅ 完成

| 模块 | 文件 | 状态 | 说明 |
|------|------|------|------|
| 技术指标 | `backtest/engine/ml_strategy.py` | ✅ | compute_returns/rsi/macd/bollinger/atr |
| 特征工程 | `backtest/engine/ml_strategy.py` | ✅ | FeatureEngine.compute_features() |
| ML 策略基类 | `backtest/engine/ml_strategy.py` | ✅ | MLSignalStrategy(BaseStrategy) |
| 时序分割器 | `backtest/engine/ml_strategy.py` | ✅ | TimeSeriesSplit（OOS 验证） |
| 策略模板库 | `backtest/engine/strategy_templates.py` | ✅ | DoubleMA / RSIMeanReversion / MACDHistogram — 2026-05-18 新增 |

> 这些是 **LLM 生成策略的基础设施**。LLM 通过继承 `MLSignalStrategy` 并实现 `predict_weights()` 来创建策略。

**待开发**：
- [ ] **3.1.1 更多技术指标**：KDJ、OBV、CCI、威廉指标、筹码分布
- [ ] **3.1.2 因子库**：基本面因子（PE/PB/ROE）、动量因子、波动率因子、质量因子
- [ ] **3.1.3 特征存储**：Feast/离线特征存储，避免每个 bar 重复计算

### 3.2 LLM 策略生成 🔄 开发中

| 功能 | 状态 | 说明 |
|------|------|------|
| DSL 策略解析 | ✅ | YAML DSL → 策略描述 |
| AST 安全求值 | ✅ | 表达式白名单求值器 |
| 策略研发子图 | ✅ | `strategy_rd/subgraph.py`，完整研发流程 |
| 策略代码生成 | ✅ | `strategy_develop_agent.py`，LLM 生成策略代码 |
| 策略模板库 | ✅ | 3 个经典模板（双均线/RSI均值回归/MACD柱），LLM 可基于模板微调 |

**待开发**：
- [ ] **3.2.1 策略代码自修复增强**：增强错误诊断粒度（未来函数检测、除零、nan 传播）
- [ ] **3.2.2 策略进化**：基于反思（reflection）节点的策略自动优化建议
- [ ] **3.2.3 策略组合生成**：LLM 生成多策略组合 + 权重分配方案

### 3.3 Prompt 系统 ✅ 基本完成

| 模块 | 状态 | 说明 |
|------|------|------|
| MarkdownPromptTemplate | ✅ | 支持 frontmatter + `{{variable}}` |
| Agent Prompt 文件 | ✅ | 各 Agent 同目录 .md prompt |
| Prompt 版本管理 | ✅ | frontmatter version 字段 |

**待开发**：
- [ ] **3.3.1 Prompt 优化闭环**：回测结果反馈 → 自动优化 Prompt 指令
- [ ] **3.3.2 Few-shot 示例管理**：按场景动态选择示例注入 Prompt

---

## 跨层任务

### 测试体系

| 层级 | 已有测试 | 待补充 |
|------|----------|--------|
| Layer 1 | `test_engine.py`(10), `test_broker.py`(18), `test_portfolio.py`(23), `test_visibility.py`(12), `test_audit_flow.py`, `test_backtest_ml_telemetry.py`, `test_dsl.py`, `test_evaluator.py` | — |
| Layer 2 | `test_visualization_api.py` | API 端点集成测试 |
| Layer 3 | `test_strategy_templates.py`(19), `test_strategy_rd_fixes.py` | ML 策略 OOS 验证测试 |

### 工程化

- [x] **CI/CD**：GitHub Actions 自动化测试 + lint + 类型检查（`.github/workflows/ci.yml` + `benchmark.yml`）
- [ ] **性能基准**：回测引擎吞吐量 benchmark（bars/sec），对比 Python vs Rust 实现
- [ ] **配置中心化**：`.env` → `config.yaml` 多环境配置
- [ ] **依赖管理**：Layer 1 最小依赖（polars + numpy + duckdb），Layer 3 可额外引入 sklearn/lightgbm 等

### 记忆系统增强 ✅ v2.0 完成 (2026-05-19) — 已被 v3.0 取代

> v2.0 实现已随 ADR-007 物质-运动统一架构重构移除（2026-06）。以下保留为历史记录。

| 功能 | 状态 | 说明 |
|------|------|------|
| 记忆衰减 | ✅ v2.0 → v3.0 重构 | → `motion.decay()`（按 form 配不同半衰期） |
| 冲突检测 | ✅ v2.0 → v3.0 重构 | → `motion.detect_conflicts()`（可配置词库） |
| 记忆压缩 | ✅ v2.0 → v3.0 重构 | → `motion.compress()`（修复聚类算法） |
| 语义增强检索 | ✅ v2.0 → v3.0 重构 | → RetrievalIndex 双通道（keyword + semantic 融合） |
| 主题总结 | ✅ v2.0 → v3.0 重构 | → SubstanceStore 统一检索 |

**v2.0 源文件**（已删除）：`src/long_earn/memory/embedding.py`、`tests/unit/test_memory/test_memory_enhanced.py`

### 物质-运动统一架构重构 🔨 v3.0 进行中 (2026-06，见 ADR-007)

替换旧 `memory/` 模块为 `substance/` 模块。详见 [ADR-007](docs/adr/007-unified-substance-architecture.md)。

**Phase 1：SubstanceStore 核心 + 旧系统移除**

| Step | 内容 | 状态 |
|------|------|------|
| 1 | `model.py`（Substance Pydantic）+ `store.py`（SubstanceStore）+ `indices/retrieval.py`（双通道）+ `indices/graph.py`（邻接表）+ `persistence.py`（JSONL） | [ ] |
| 2 | `motion.py`（activate WorldInfo 引擎 + decay + detect_conflicts + compress） | [ ] |
| 3 | 重写 `MemoryServiceImpl` 委托 SubstanceStore + 更新 `tools/store.py`（消费方零改动） | [ ] |
| 4 | 备份旧数据到 temp + 删除 `memory/` + 重写测试 + 更新 config/import-linter/CLAUDE.md | [ ] |

**Phase 2：采集器 + 事件推理子图**

| Step | 内容 | 状态 |
|------|------|------|
| 1 | Collector registry + Kimi/Tencent/ciccwm 采集器 | [ ] |
| 2 | 事件推理子图（collect→extract→propagate→conflict→save）+ 主图路由 | [ ] |

**Phase 3：子图集成 + Dashboard**

| Step | 内容 | 状态 |
|------|------|------|
| 1 | stock_analysis/strategy_rd 调 `store.activate()` 注入 + Dashboard 事件流 | [ ] |

---

## 当前迭代任务分配

### 第 1 轮 (2026-05-18) — 已完成

| 子 Agent | 负责模块 | 任务 | 结果 |
|----------|----------|------|------|
| Agent-1 | Layer 1 核心 | 补全 Broker/Portfolio/Visibility 单元测试 | ✅ 53 tests |
| Agent-2 | Layer 2 Dashboard | 抽取 Dashboard 独立模块 + 增强 BacktestAnalyzer | ✅ 模块迁移 + 3 个新方法 |
| Agent-3 | Layer 3 策略 | 构建策略模板库 | ✅ 3 个模板 |

### 第 2 轮 (2026-05-19) — 已完成

| 子 Agent | 负责模块 | 任务 | 结果 |
|----------|----------|------|------|
| Agent-1 | Layer 1 + 3 | Walk-Forward 测试 + 策略模板单元测试 | ✅ 19 模板测试 + 6 Walk-Forward 测试 |
| Agent-2 | Layer 2 | Dashboard HTML Chart.js 增强 + API 端点扩展 | ✅ 交互式图表 + risk/compare/daily_returns API |

### 第 3 轮 (2026-05-19) — 已完成

| 子 Agent | 负责模块 | 任务 | 结果 |
|----------|----------|------|------|
| Agent-1 | Layer 1 + 3 | 止损/回撤风控测试 + 技术指标扩充 | ✅ 5 风控测试 + KDJ/CCI/威廉/SMA/EMA 完成 |
| Agent-2 | 跨层 | CI/CD + 性能 Benchmark | ✅ ci.yml + benchmark.yml |

### 记忆系统增强轮 (2026-05-19) — 已完成

| 功能 | 模块 | 结果 |
|------|------|------|
| 记忆衰减 | `memory/store.py` | ✅ 指数衰减 + search() 自动过滤 + 手动 decay() |
| 冲突检测 | `memory/store.py` | ✅ 相似度检测 + 矛盾关键词 + 冲突组 resolve |
| 记忆压缩 | `memory/store.py` | ✅ 贪心聚类合并 + 主题总结 |
| 语义检索 | `memory/embedding.py` | ✅ 嵌入混合检索 + TF-IDF 回退 |
| 测试覆盖 | `tests/unit/test_memory/test_memory_enhanced.py` | ✅ 19 个新测试（全部通过，32 tests total） |

### 第 4 轮 (2026-05-20 计划)

| 子 Agent | 负责模块 | 任务 | 结果 |
|----------|----------|------|------|
| Agent-1 | Layer 1 | **高级订单类型**：限价单、止损单、止损限价单、OCO | ✅ 21 测试，Broker 状态化改造 + 引擎 pending order 检查 |
| Agent-2 | Layer 2 + 3 | Dashboard HTML 中 compare API 修复；特征工程单元测试 | |
| Agent-3 | 跨层 | config.yaml 迁移；记忆系统回测结果自动归档 | |
