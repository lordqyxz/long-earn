# TODO — 待办清单

> 最后更新：2026-08-02
>
> 按「重要性 + 威胁程度」统一排序，合并功能开发待办与合规审计。
> 威胁优先级：金融合规 / 数据正确性 > 功能完整性 > 工程质量。
>
> **修复进度追踪约定**：`[ ]` 未开始、`[~]` 部分完成、`[x]` 已完成（可归档删除）。所有修复必须配套回归测试。
> 当前系统**不具备直接进入实盘交易的合规条件**，P0/P1 信任项闭环后需重新审计方可进入模拟盘验证。
>
> **架构现状（ADR-018）**：策略研发控制面已翻转为 ToG `ResearchAgent`；HTR 降为脚手架；事件 `prepare_context` 与显式多源数据层已落地。总览见 [docs/architecture.md](docs/architecture.md)。

---

## 本轮已完成（ADR-018，2026-08）

- [x] ToG / ToG-2 论文入库 + 机制映射（`docs/research/papers/`）
- [x] ADR-018 Accepted；ADR-016 §C / ADR-010 编排地位修订
- [x] `ResearchAgent` + Master `research_strategy` 委托
- [x] `RuntimeContext.prepare_context`；默认 Collector；Connector 注入 memory
- [x] 数据层取消静默降级叙事（Cache + 显式 miniqmt）
- [x] 废弃 `strategy_rd/subgraph` 归档；`fund_flow` 入边修复
- [x] 架构总览文档 / Canvas

---

## P0 — 致命威胁（回测虚高 / 未来函数 / 制度违规）

> 直接影响回测可信度；模拟盘准入的硬门槛。

### CI / 测试基线

- [~] **AUDIT-P0-13** 覆盖率门禁 + 集成测试入 CI — 部分完成（commit `a0de9ad`）
  - 已完成：`fail_under = 60`（实际 ~65%）；CI 含 `pytest tests/integration/ -m "not requires_credentials"`。
  - 暂缓：强行 `fail_under=80` / broker·engine·causality 单项 95% — 随测试补齐再 60→70→80。
  - 位置：`pyproject.toml`、`.github/workflows/ci.yml`

---

## P1 — 高优先级（信任加固 · 模拟盘准入）

> 建议下一冲刺主线：价格 ffill → 幸存者偏差 → 停牌字段 → 审计重放 → 测试锁回归。

### 数据层

- [ ] **AUDIT-P1-01** 幸存者偏差：缺 PIT 成分股历史快照 + `universe_pit_warning`
  - 现状：`save_universe` 用请求 `date` 作快照日期（已符合）；仍无历史成分股真值、回测结果无警告标志。
  - 修复：PIT 成分股快照；回测结果增加 `universe_pit_warning`。

- [~] **AUDIT-P1-02** 价格列禁止 ffill，仅财务列前向填充 — 部分完成
  - 位置：`provider.py` / `miniqmt_provider.py` 仍全列 `groupby.ffill()`。
  - 修复：按 `fin_cols` 过滤；价格列缺失保持 NaN。

### 交易执行

- [ ] **AUDIT-P1-04** 行业集中度风控 — **暂缓**（缺回测路径 `industry` 数据源）
  - 前置：数据层申万一级等行业字段 → 再补 `max_industry_pct` + 下单前聚合检查。

- [~] **AUDIT-P1-08** LIMIT/STOP 未接入引擎主流程 — 部分完成
  - 已完成：broker 层撮合。
  - 未完成：`SignalEvent` / `Strategy.submit_order` 未贯通主流程（仍默认 MARKET）。

- [~] **AUDIT-P1-09** 停牌仅靠 `volume==0` 隐式推断 — 部分完成
  - 修复：数据层 `is_tradable` / `is_suspended`；pre-trade 显式拒单。

### 审计日志

- [~] **AUDIT-P1-13** 回测无法完整重放 — 部分完成
  - 未完成：RUN_START 需完整 `symbols`、`strategy_yaml`/`strategy_hash`；MARKET_DATA slab 摘要；SIGNAL dict JSON 列。

### 测试

- [~] **AUDIT-P1-16** 审计流测试未覆盖全部事件类型 / run_id 关联 — 部分完成
- [~] **AUDIT-P1-17** 算子数值稳定性缺 NaN/Inf/极值/除零/超长窗口 — 部分完成
- [~] **AUDIT-P1-18** `max_position_pct` 触发与 RISK_TRIGGER 断言不足 — 部分完成

---

## P2 — 中优先级（ToG 飞轮硬化 + 建模精度）

### ADR-018 后续（控制面已落地，飞轮待验证）

- [ ] **ToG Spike 对照**：真实 LLM + 回测路径上对比 ResearchAgent vs 旧 HTR 入口，确认能复现「算子 + 策略」正向飞轮
- [ ] **证据门契约加固**：无 `run_backtest` / `run_oos_gates` 结果禁止 `record_path_outcome` 标记 success；AcceptanceGate / DSR·PBO 接入 ResearchAgent 合并路径
- [ ] **HTR 双轨收缩**：评估删除/只读兼容 `create_htr_subgraph` 默认路径；文档与 CLI 统一指向 ResearchAgent
- [ ] **Dashboard 事件流可视化**：`prepare_context` / Substance 事件已可激活，Dashboard 展示仍缺

### 建模精度与测试质量

- [~] **AUDIT-P2-03** ORDER_SKIPPED 原因未统一为结构化枚举 — 部分完成
- [ ] **AUDIT-P2-07** 复权一致性跨 provider 校验
- [ ] **AUDIT-P2-08** hypothesis property-based testing（算子单调性 / 滑点对称 / PIT 延迟）
- [~] **AUDIT-P2-09** data provider 契约套未参数化多源 — 部分完成（ADR-018 后「多源」= 显式点名，勿再假设降级链）
- [ ] **AUDIT-P2-10** EMA / RSI / MACD / Bollinger 公式对齐测试
- [~] **AUDIT-P2-11** Alpha / Beta / IR 与 numpy 对齐测试 — 部分完成
- [ ] **AUDIT-P2-12** 因果性扰动扩展（极值 / 负数 / 随机大数）
- [ ] **AUDIT-P2-15** 真实交易日历 XSHG 替代 `freq="B"`
- [ ] **AUDIT-P2-16** 关键事件写入单步 `latency_ms`
- [ ] **AUDIT-P2-17** MARKET_DATA 与 equity_curve 审计时点对齐（sortino 对账残差）

---

## P3 — 低优先级（能力扩展与工程化）

### 策略 / 分析增强

- [ ] **参数自动寻优接入 ResearchAgent**：基建已有（`parallel.py` + `param_grid.py`），作 ToG 工具而非 HTR 固定节点
- [ ] **多策略组合**
- [ ] **近实盘**：实时行情喂入引擎 `on_bar`
- [ ] **行业对比视角**（`stock_analysis`；依赖 P1-04 行业数据时可联动）

### 工程化与质量

- [ ] **ResearchAgent 端到端集成测试**（替代旧「仅 strategy_rd 子图」表述）
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

### 远期（门控）

- [ ] **ADR-017 自我进化** — Deferred；前置：统计门端到端验证 + 稳健策略验证集基线 + ResearchAgent 飞轮稳定

---

## 建议冲刺顺序

| 次序 | 事项 | 理由 |
|------|------|------|
| 1 | P1-02 价格禁 ffill | 直接扭曲价格路径 |
| 2 | P1-01 幸存者偏差 | 回测虚高系统性来源 |
| 3 | P1-09 停牌显式字段 | 隐式逻辑不可审计 |
| 4 | P1-13 审计可完整重放 | 合规与对账前提 |
| 5 | P1-16/17/18 测试锁回归 | 锁住已修行为 |
| 6 | P2 ToG Spike 对照 | 验证架构翻转是否兑现飞轮 |
