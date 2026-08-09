# TODO — 待办清单

> 最后更新：2026-08-08
>
> 按 **紧急 × 重要** 四象限组织（艾森豪威尔矩阵），合并功能开发与合规审计。
> 判定准则：
> - **重要**：扭曲回测可信度 / 阻断模拟盘准入 / 破坏 ToG 飞轮证据链
> - **紧急**：正在污染当前结果，或架构翻转后若不马上验证会把错误路径固化
>
> 威胁优先级仍适用：金融合规 / 数据正确性 > 功能完整性 > 工程质量。
>
> **修复进度追踪约定**：`[ ]` 未开始、`[~]` 部分完成、`[x]` 已完成（可归档删除）。所有修复必须配套回归测试。
> 当前系统**不具备直接进入实盘交易的合规条件**，Q2 信任项闭环后需重新审计方可进入模拟盘验证。
>
> **架构现状（ADR-018）**：策略研发控制面已翻转为 ToG `ResearchAgent`；HTR 降为脚手架；事件 `prepare_context` 与显式多源数据层已落地。总览见 [docs/architecture.md](docs/architecture.md)。

---

## 本周冲刺（2026-08-08 Q2）

> Q1 数据真相与飞轮验证已闭环，进入 Q2 模拟盘准入与信任加固。

- [x] **AUDIT-P1-13** 回测完整重放：RUN_START 完整 symbols/strategy_yaml/strategy_hash；MARKET_DATA slab 摘要
- [x] **AUDIT-P1-08** LIMIT/STOP 接入引擎主流程：SignalEvent / Strategy.submit_order 贯通
- [x] **AUDIT-P1-16** 审计流测试覆盖全部事件类型 / run_id 关联
- [x] **AUDIT-P1-17** 算子数值稳定性测试 NaN/Inf/极值/除零/超长窗口
- [x] **AUDIT-P1-18** `max_position_pct` 触发与 RISK_TRIGGER 断言
- [x] 全量 831 单测通过、ruff 零错、lint-imports 5/5 合约保持
- [x] **ResearchAgent 端到端集成测试**：DuckDB 线程安全修复、prove_causality 工具命名修复、3 快速测试 + 2 LLM 测试（Ollama 不可用时自动跳过）
- [x] **HTR 双轨收缩**：htr_subgraph / config 标记废弃只读兼容；CLI / subgraph.py 文档指向 ResearchAgent；旧代码保留不删除

---

## 已归档（Q1 冲刺，2026-08-08）

- [x] 修复 ruff 11 个错误（import 排序、未使用 import、尾换行）— 4 个算子文件
- [x] 修复 AcceptanceGate 退化策略未拒绝 bug — `is_metrics_unreliable` 缺 `degenerate` 检查
- [x] 修复 4 个新增算子因果性证明失败 — 添加内部 `sort(["symbol", "timestamp"])` 排序
- [x] 修复 `test_auto_evolution_system` 污染全局 OPERATOR_REGISTRY 导致其他测试间歇失败
- [x] 全量 807 单元测试通过、ruff 零错、lint-imports 5/5 合约保持
- [x] **P1-09 停牌显式字段**：数据层 `is_tradable`（xtquant `suspendFlag`）→ DuckDB → 引擎 pre-trade 显式拒单
- [x] **P1-01 成分股 PIT 真值**：修复 4 处虚假历史快照 bug；新增 `_collect_universe_snapshots` 定期采集
- [x] **证据门契约加固**：`run_oos_gates` 接入 DSR 多重检验校正；`_validate_success_writeback` 三道证据门
- [x] **ToG Spike**：新增 `TestEvidenceGatePipeline` 6 个全流程测试覆盖三道证据门
- [x] 全量 813 单元测试通过、ruff 零错

---

## 本轮已完成（ADR-018，2026-08）

- [x] ToG / ToG-2 论文入库 + 机制映射（`docs/research/papers/`）
- [x] ADR-018 Accepted；ADR-016 §C / ADR-010 编排地位修订
- [x] `ResearchAgent` + Master `research_strategy` 委托
- [x] `RuntimeContext.prepare_context`；默认 Collector；Connector 注入 memory
- [x] 数据层取消静默降级叙事（Cache + 显式 miniqmt）
- [x] 废弃 `strategy_rd/subgraph` 归档；`fund_flow` 入边修复
- [x] 架构总览文档 / Canvas
- [x] 集成测试入 CI；移除覆盖率百分比门禁（`fail_under` / `--cov-fail-under`）

---

## Q1 — 紧急且重要（现在就做）

> 正在污染回测，或 ADR-018 翻转后若不验证会固化错误飞轮。**本冲刺只吃这一象限。**

| ID | 事项 | 为何现在做 |
|----|------|-----------|
| AUDIT-P1-02 | 价格列禁止 ffill，仅财务列前向填充 `[x]` | **直接扭曲价格路径**；每次回测都在造假 |
| AUDIT-P1-01 | 幸存者偏差：PIT 成分股快照 + `universe_pit_warning` `[x]` | 回测虚高的系统性来源；模拟盘准入硬伤 |
| AUDIT-P1-09 | 停牌显式字段（`is_tradable` / `is_suspended`）`[x]` | 隐式 `volume==0` 不可审计；撮合结果不可信 |
| ToG Spike | 真实 LLM + 回测路径对照 ResearchAgent vs 旧 HTR `[x]` | 架构已翻转，**不验证等于在未证实的控制面上继续堆功能** |
| 证据门契约 | 无 `run_backtest` / `run_oos_gates` 禁止 `record_path_outcome` success；AcceptanceGate / DSR·PBO 接入合并路径 `[x]` | 飞轮可写「假成功」；比缺功能更危险 |

### 明细

- [x] **AUDIT-P1-02** 价格列禁止 ffill，仅财务列前向填充 — 已完成
  - 位置：`connector.py` / `miniqmt_provider.py` 中 `get_merged_panel` 已限制 `fin_cols` 过滤。
  - 修复：按 `fin_cols` 过滤；价格列缺失保持 NaN。

- [x] **AUDIT-P1-01** 幸存者偏差：缺 PIT 成分股历史快照 + `universe_pit_warning` — 已完成
  - 修复：`save_universe` 用请求 `date` 作快照日期；`BacktestResult.universe_pit_warning` 字段；`backtest_service._check_universe_pit` 回测前检查；`_collect_universe_snapshots` 定期采集。

- [x] **AUDIT-P1-09** 停牌显式字段 — 已完成
  - 修复：数据层 `is_tradable`（xtquant `suspendFlag`）；DuckDB 缓存；pre-trade 显式拒单。

- [x] **ToG Spike 对照**：真实 LLM + 回测路径上对比 ResearchAgent vs 旧 HTR 入口 — 已完成
  - 新增 `TestEvidenceGatePipeline` 6 个全流程测试覆盖三道证据门。

- [x] **证据门契约加固**：无 `run_backtest` / `run_oos_gates` 结果禁止 `record_path_outcome` 标记 success — 已完成
  - 三道证据门（存在/可信/显著）；DSR 多重检验校正接入 OOS 路径。

---

## Q2 — 重要不紧急（排期做，下一冲刺主线）

> 模拟盘准入与信任加固所必需，但不在每次回测中即时造假。Q1 闭环后再推进。

### 审计与执行完整性

- [x] **AUDIT-P1-13** 回测无法完整重放 — 已完成
  - RUN_START 完整 symbols/strategy_yaml/strategy_hash；MARKET_DATA slab 摘要；SIGNAL dict JSON 列。
- [x] **AUDIT-P1-08** LIMIT/STOP 未接入引擎主流程 — 已完成
  - SignalEvent / Strategy.submit_order 贯通，Broker 高级订单路径已接入引擎主流程。

### 测试锁回归（锁住已修 / 将修行为）

- [x] **AUDIT-P1-16** 审计流测试覆盖全部事件类型 / run_id 关联 — 已完成
- [x] **AUDIT-P1-17** 算子数值稳定性 NaN/Inf/极值/除零/超长窗口 — 已完成
- [x] **AUDIT-P1-18** `max_position_pct` 触发与 RISK_TRIGGER 断言 — 已完成
- [x] **ResearchAgent 端到端集成测试**（替代旧「仅 strategy_rd 子图」表述）— 已完成

### ToG 控制面收敛

- [x] **HTR 双轨收缩**：htr_subgraph / config 标记废弃只读兼容；CLI / subgraph.py 文档指向 ResearchAgent；旧代码保留不删除 — 已完成
- [x] **参数自动寻优接入 ResearchAgent**：`run_param_search` 工具已接入 ToG 工具列表，利用 ParamGrid + ParallelRunner 基建，训练集上并行网格搜索最优参数 — 已完成
- [x] **AUDIT-P2-03** ORDER_SKIPPED 原因统一为结构化枚举 — 已完成（`OrderSkipReason(StrEnum)` 6 种原因，portfolio.py / core.py / 测试全量更新）

### 建模精度（影响可信度，但非即时污染）

- [x] **AUDIT-P2-07** 复权一致性跨 provider 校验 — 已完成（`DataCache.check_adjustment_consistency()` 逐股日收益率跳跃检测，默认阈值 50%）
- [x] **AUDIT-P2-15** 真实交易日历 XSHG 替代 `freq="B"` — 已完成（`DataCache.get_trading_dates()` 从 price_daily 查询真实交易日，`build_daily_financial_panel` 优先使用，回退到 freq="B"）
- [x] **AUDIT-P2-17** MARKET_DATA 与 equity_curve 审计时点对齐（sortino 对账残差）— 已完成（`_finalize_mark_to_market` 不再覆写 equity_curve[-1]，新增对齐测试）
- [x] **AUDIT-P2-12** 因果性扰动扩展（极值 / 负数 / 随机大数）— 已完成（PerturbStrategy 四策略 + 18 算子×4 策略因果性全覆盖 + 12 算子数值稳定性测试）

### 观测

- [x] **Dashboard 事件流可视化**：FastAPI + WebSocket 实时推送，事件推理管线可视化页面，CLI 默认启用 `--fastapi`

---

## Q3 — 紧急不重要（可延后 / 顺手做，不占主线）

> 有「该做」压力（CI 门禁、局部缺口），但不决定回测真伪或飞轮对错。**不要为此打断 Q1。**

- [x] **AUDIT-P2-03** ORDER_SKIPPED 原因统一为结构化枚举 — 已完成
- [x] **AUDIT-P2-09** data provider 契约套未参数化多源 — 已完成（RealtimeDataProvider 面向接口参数化，4 契约测试 × 2 实现）
- [x] **AUDIT-P2-11** Alpha / Beta / IR 与 numpy 对齐测试 — 已完成（C2 主对齐 + C2b/C2c/C2d 边界条件）
- [x] **AUDIT-P2-10** EMA / RSI / MACD / Bollinger 公式对齐测试 — 已完成
- [x] **AUDIT-P2-08** hypothesis property-based testing（算子单调性 / 滑点对称 / PIT 延迟）— 已完成
- [x] **AUDIT-P2-16** 关键事件写入单步 `latency_ms` — 已完成（MARKET_DATA / SIGNAL / SIGNAL_EXECUTE_T1 均记录 perf_counter 耗时）

---

## Q4 — 不紧急不重要（明确搁置）

> 能力扩展或工程化愿望；前置未满足前不动。

### 能力扩展（门控）

- [ ] **AUDIT-P1-04** 行业集中度风控 — **暂缓**（缺回测路径 `industry` 数据源）
- [ ] **行业对比视角**（`stock_analysis`；依赖行业数据时可联动）
- [ ] **多策略组合**
- [ ] **近实盘**：实时行情喂入引擎 `on_bar`
- [ ] **ADR-017 自我进化** — Deferred；前置：统计门端到端验证 + 稳健策略验证集基线 + ResearchAgent 飞轮稳定

### 工程化与纵深防御

- [ ] **性能监控**：LLM Token + 回测耗时（`MonitoringService`）
- [ ] **配置中心化**：多环境 `config.yaml`
- [ ] **AUDIT-P3-01** `@pytest.mark.regression` 集中回归套件
- [ ] **AUDIT-P3-02** broker 异常输入（NaN/Inf/负数/0）
- [ ] **AUDIT-P3-03** 部分成交（大单分批）
- [ ] **AUDIT-P3-04** 性能/压力（全 A、长周期、并发）
- [ ] **AUDIT-P3-05** 敏感信息脱敏（`password=` / `token=` / `api_key=`）
- [ ] **AUDIT-P3-06** telemetry 与审计集成
- [ ] **AUDIT-P3-07** miniqmt 内联常量清理
- [ ] **AUDIT-P3-08** `get_financials` 日期范围过滤（纵深防御）
- [ ] **AUDIT-P3-09** 因果性切点扩展
- [ ] **AUDIT-P3-10** 算子注册强制附带 `prove_causality` 报告

---

## 判定摘要：哪些值得现在做

| 现在做？ | 事项 | 一句话 |
|---------|------|--------|
| **是** | P1-02 价格禁 ffill | 每次回测都在造假 |
| **是** | P1-01 幸存者偏差 | 系统性虚高 |
| **是** | P1-09 停牌显式字段 | 撮合不可审计 |
| **是** | 证据门契约 | 防飞轮假成功 |
| **是** | ToG Spike | 验证 ADR-018 是否兑现 |
| 下一冲刺 | P1-13 审计重放、P1-16/17/18、HTR 收缩 | 准入需要，但不即时污染 |
| 不要现在 | 公式对齐、多策略、近实盘、ADR-017 | 分散注意力或前置未满 |

**不做的理由（防范围蔓延）**：LIMIT/STOP、行业风控、Dashboard、参数寻优都依赖「数据真相 + 飞轮已验证」这两块基石。
