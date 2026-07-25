# TODO — 待办清单

> 最后更新：2026-07-26
>
> 按「重要性 + 威胁程度」统一排序，合并功能开发待办与合规审计。
> 威胁优先级：金融合规 / 数据正确性 > 功能完整性 > 工程质量。
>
> **修复进度追踪约定**：每项前的复选框 `[ ]` 未开始、`[~]` 部分完成。所有修复必须配套回归测试。
> 当前系统**不具备直接进入实盘交易的合规条件**，P0 全部闭环后需重新审计方可进入模拟盘验证。

---

## P0 — 致命威胁（回测结果虚高 / 未来函数泄漏 / 金融制度违规）

> 直接影响回测可信度与系统对外输出，必须立即修复。

### CI / 测试基线

- [~] **AUDIT-P0-13** 覆盖率门禁未生效 + 集成测试不在 CI — 部分完成（commit `a0de9ad`）
  - 已完成：(1) `pyproject.toml:97` `fail_under = 60`（原为 0，门禁已生效，当前实际覆盖率 ~65%）；(2) `ci.yml` test job 加入 `uv run pytest tests/integration/ -v -m "not requires_credentials"`。
  - **不合理 / 暂缓**：TODO 要求 `fail_under` 改为 `80`，但当前实际覆盖率仅 ~65%，强行设 80 会导致 CI 红灯。测试后续根据业务实际需求补充，覆盖率门禁随测试补齐逐步上调（60→70→80），不在本轮强行推到 80。关键路径 broker/engine/causality 单独 95 单项门禁同理暂缓。
  - 位置：`pyproject.toml:97`、`.github/workflows/ci.yml`

---

## P1 — 高优先级（A 股合规性 + 建模精度）

> 影响建模精度与合规性，P0 闭环后应立即推进。

### 数据层

- [ ] **AUDIT-P1-01** universe 缓存用当前成分股标注历史日期（幸存者偏差）
  - 位置：`akshare_provider.py:187`、`miniqmt_provider.py:1094,1107,1130`（均用请求 `date` 作快照日期，非 `date.today()`，这点已符合）
  - 未完成：全代码搜索 `universe_pit_warning` 零匹配 — 回测结果缺幸存者偏差警告标志；无 PIT 成分股历史快照（仍用当日成分股标注历史日期）。
  - 修复：`save_universe` 用请求 `date` 作为快照日期（已符合）；回测结果增加 `universe_pit_warning` 标志；数据层补 PIT 成分股历史快照。

- [~] **AUDIT-P1-02** Composite/Ciccwm provider 对所有列 ffill（含价格）— 部分完成
  - 位置：`provider.py:438`（仍 `groupby.ffill()` 全列）、`miniqmt_provider.py:1006`（同）
  - 现状：ciccwm/akshare 财务降级分支已屏蔽，实际仅行情列被 ffill，但无显式 `fin_cols` 过滤。
  - 未完成：价格列在交易日缺失时仍被前向填充，与"价格列保持 NaN"目标未对齐。
  - 修复：仅对财务列 ffill（参照 `fin_cols` 过滤），价格列保持 NaN。

### 交易执行

- [ ] **AUDIT-P1-04** 行业集中度完全未实现（纸面约束无执行）— **依赖前置条件，暂缓**
  - 位置：`dsl.py:96` RiskControlConfig 无行业字段
  - **不合理/暂缓原因**：实现行业集中度风控需要 (1) 数据层补充 `industry` 字段 — 当前回测数据层（`miniqmt_provider`/`cache`/`provider`）**完全无行业数据**，全代码仅 `stock_service.py:78` 从 stock_detail 取 industry（非回测路径）；(2) 需要引入行业分类数据源（如申万一级），这是一项独立的数据层增强任务，非简单风控补丁。在数据层提供 industry 字段之前，RiskControlConfig 的 `max_industry_pct` 只能是纸面约束无法执行。
  - 修复（待前置条件满足）：(1) 数据层补充 `industry` 字段；(2) RiskControlConfig 增加 `max_industry_pct`；(3) `Portfolio.process_signal` 生成订单前按行业聚合检查。

- [~] **AUDIT-P1-08** 高级订单类型未接入引擎主流程 — 部分完成（commit `a0de9ad`）
  - 位置：`src/long_earn/backtest/engine/broker.py:119-140`（LIMIT/STOP/STOP_LIMIT 撮合实现完整）
  - 已完成：broker 层支持 LIMIT/STOP/STOP_LIMIT 撮合。
  - 未完成：`core.py:793-861` 撮合主流程仅用市价单路径（`OrderEvent.exec_type` 未设置则默认 MARKET），`SignalEvent` 未扩展允许携带订单类型与价格，无 `Strategy.submit_order()` 接口。
  - 修复：扩展 `SignalEvent` 允许携带订单类型与价格，或提供 `Strategy.submit_order()` 接口。

- [~] **AUDIT-P1-09** 停牌处理依赖数据缺失的隐式逻辑 — 部分完成（commit `a0de9ad`）
  - 位置：`src/long_earn/backtest/engine/core.py:147-151`（`volume == 0` 视为停牌拒单）
  - 已完成：停牌逻辑有（volume=0 拒单，记 `ORDER_SKIPPED`）。
  - 未完成：数据层无显式 `is_tradable`/`is_suspended` 布尔字段（全代码搜索零匹配），仅基于 volume=0 隐式推断。
  - 修复：数据层增加 `is_tradable`/`is_suspended` 布尔字段，pre-trade 风控拒绝停牌日订单。

### 审计日志

- [~] **AUDIT-P1-13** 无法完整重放回测 — 部分完成（commit `a0de9ad`）
  - 位置：`src/long_earn/backtest/engine/core.py:220-231`（RUN_START payload）
  - 已完成：RUN_START 含 `start_date, end_date, symbols_count, benchmark_symbol, stop_loss, max_drawdown_limit, max_position_pct, max_positions, strategy_id`。
  - 未完成：(1) `symbols_count` 而非完整 `symbols` 列表（仅数量）；(2) 无 `strategy_yaml` 或 `strategy_hash`（仅 `strategy_id` 字符串）；(3) MARKET_DATA slab 关键字段摘要、SIGNAL 保持 dict 写入 JSON 列未明确处理。
  - 修复：RUN_START 增加完整 symbols 列表、`strategy_yaml` 或 `strategy_hash`；MARKET_DATA payload 增加 slab 关键字段摘要；SIGNAL 的 signals 保持原 dict 类型写入 DuckDB JSON 列。

### 测试

- [~] **AUDIT-P1-16** 审计流测试极度单薄（仅 1 个测试函数）— 部分完成
  - 位置：`tests/unit/test_backtest/test_engine.py:421 test_audit_trail_records_events`、`436 test_audit_trail_entries_include_timestamp`、`707 test_event_order_within_bar`、`744 test_risk_trigger_replaces_signal`
  - 已完成：覆盖部分事件类型与因果链顺序、timestamp 字段、RISK_TRIGGER 替换信号。
  - 未完成：未系统性覆盖全部 12 种事件类型，run_id 关联性无显式断言。
  - 修复：覆盖全部 12 种事件类型，验证 parent_id 因果链和 run_id 关联性。

- [~] **AUDIT-P1-17** 数值稳定性测试缺失 — 部分完成
  - 位置：`tests/unit/test_backtest/test_operators/test_numerics.py`（7 个测试：shift/returns/windowed/subtraction/bool_mask/window_boundary）
  - 已完成：基础算子数值正确性测试。
  - 未完成：无 NaN/Inf/1e308 极值/除零/窗口超长场景测试。
  - 修复：`test_numerics.py` 加入 NaN/Inf/极值/除零/窗口超长场景。

- [~] **AUDIT-P1-18** 风控规则覆盖不足 — 部分完成
  - 位置：`test_engine.py:277 test_stop_loss_trigger`、`329 test_max_drawdown_trigger`、`test_portfolio.py:241 test_max_positions_limits_new_entries`、`259 test_max_positions_zero_is_unlimited`
  - 已完成：止损/最大回撤触发测试（断言 RISK_TRIGGER 审计事件、持仓清空）；`max_positions` 触发测试。
  - 未完成：`max_position_pct` 单独触发断言缺失（仅 `test_compliance.py:204,217` 间接用 `max_position_pct=1.0`）。
  - 修复：补齐 `max_position_pct` 触发测试；风控触发后断言 RISK_TRIGGER 审计事件、持仓清空、后续 bar 被跳过。

---

## P2 — 中优先级（功能完整性 + 建模精度提升）

### 事件推理子图集成（ADR-007 后续）

Phase 2（多源采集器 + 事件推理子图 + 主图路由）与 Phase 3 数据层（财务字段全量提取 + PIT 修复）均已完成。

- [ ] **子图集成 + Dashboard**
  - stock_analysis / strategy_rd 调 `store.activate()` 注入事件上下文
  - Dashboard 事件流可视化

### 建模精度与测试质量

- [~] **AUDIT-P2-03** ORDER_SKIPPED 仅覆盖单一原因 — 部分完成
  - 位置：`src/long_earn/backtest/engine/core.py:771-785`（T+1 跳过）、`804-808`（现金不足跳过）、`826-838`（pre_trade 跳过），均记 `ORDER_SKIPPED` 并含 `reason` 字段；`portfolio.py:270-271` 返回 `skip_reason` 字符串
  - 已完成：覆盖多原因（T+1/现金不足/pre_trade/涨跌停/停牌）。
  - 未完成：`portfolio.py:324-328` skipped_reasons 仍仅传 reason 字符串，未完全统一为结构化原因枚举。
  - 修复：`Portfolio` 持有审计回调或返回跳过原因列表，由引擎统一记 `ORDER_SKIPPED`。

- [ ] **AUDIT-P2-07** 复权一致性未校验
  - 修复：provider 基类强制声明 `adjust` 策略，`get_merged_panel` 校验所有 provider 复权方式一致。

- [ ] **AUDIT-P2-08** 引入 hypothesis property-based testing
  - 修复：至少为算子单调性、broker 滑点对称性、PIT 延迟对任意报告期生效三类场景引入。

- [~] **AUDIT-P2-09** 统一 test_data_provider.py 入契约套 — 部分完成
  - 位置：`tests/unit/test_backtest/test_data_provider.py`、`test_provider_pit_contract.py`
  - 未完成：grep `TestMergedPanelFfillSorted`、`parametrize` 在该文件零匹配 — 未参数化为 3 provider 共用契约套。
  - 修复：将 `TestMergedPanelFfillSorted` 参数化为 3 provider 共用。

- [ ] **AUDIT-P2-10** 扩展算子数值正确性覆盖
  - 修复：EMA / RSI / MACD / Bollinger 在 test_numerics.py 中补齐公式对齐测试。

- [~] **AUDIT-P2-11** 基准对比指标精度测试 — 部分完成
  - 位置：`tests/unit/test_backtest/test_metrics.py:475 test_returns_match_numpy_formula`、`91 test_sharpe_matches_numpy_formula`、`124 test_total_return_matches_numpy`
  - 已完成：Sharpe/total_return 与 numpy 直接计算一致。
  - 未完成：Alpha/Beta/IR 与 numpy 直接计算一致的测试缺失。
  - 修复：Alpha/Beta/IR 与 numpy 直接计算一致（类似 `test_returns_match_numpy_formula`）。

- [ ] **AUDIT-P2-12** 因果性扰动方式单一
  - 位置：`causality.py:54-58` 仅置 NaN
  - 修复：补充极端值（1e308）、负数、随机大数扰动，检测 `fill_null(0)` 类隐藏泄漏。

- [ ] **AUDIT-P2-15** 使用真实交易日历替代 freq="B"
  - 修复：使用 `exchange_calendars` 的 XSHG 日历。

- [ ] **AUDIT-P2-16** latency_ms 仅 RUN_END/RUN_ERROR 有值
  - 修复：为 MARKET_DATA、SIGNAL、ORDER、FILL、RISK_TRIGGER 等关键事件计算并写入单步延迟。

- [ ] **AUDIT-P2-17** 审计 MARKET_DATA 采样时点 ≠ equity_curve sync 时点
  - 证据文档：[docs/research/2026-07-13-momentum-backtest-proof.md](docs/research/2026-07-13-momentum-backtest-proof.md) §6.3
  - 位置：`src/long_earn/backtest/engine/core.py:476`（MARKET_DATA 事件记录 portfolio_value，在 `update_market_values` 之后、策略信号生成之前）vs `core.py:524`（`_sync_equity_curve`，在信号生成与撮合之后）
  - 问题：MARKET_DATA 事件记录的 `portfolio_value` 是交易前市值，`equity_curve` 追加的是交易后市值。两者时点不同，导致从审计日志重建的 equity_curve 与引擎 equity_curve 存在系统性微小差异（约 0.3%），传导到 sortino 放大到 0.8%（超出 0.5% 对账容差）。
  - 影响：total_return / max_drawdown 等核心指标对账不受影响（段 A ✅ + 段 B ✅，绝对差 1e-6 级），仅 sortino 等对 equity 末端敏感的指标有残差。不影响回测可信度结论。
  - 修复：在 `core.py:524` `_sync_equity_curve` 之后追加一次 MARKET_DATA 事件记录（或新增 `EQUITY_SYNC` 事件类型），payload 含 `portfolio_value`，使审计日志能精确重建 equity_curve。

---

## P3 — 低优先级（工程化与持续改进）

### 策略研发与分析增强

- [ ] **自动化参数寻优接入**：在 `strategy_rd` 子图中增加参数自动调优节点。基础设施已交付（`engine/parallel.py` + `param_grid.py`），subgraph 接入待后续轮。
- [ ] **多策略集成**：支持将多个研发成功的子策略组合成一个组合策略。
- [ ] **近实盘策略接入**：将实时行情喂入引擎 `on_bar`（实时数据对接已完成，此为后续）。
- [ ] **行业对比视角**：`stock_analysis` 增加行业对比视角（资金流向视角已完成，此为后续）。

### 工程化与质量

- [ ] **集成测试增强**：针对 `strategy_rd` 的全链路流程编写更多端到端集成测试。
- [ ] **性能监控**：在 `MonitoringService` 中增加对 LLM Token 消耗和回测耗时的统计。
- [ ] **配置中心化**：将 `.env` 变量扩展为支持多环境配置的 `config.yaml`。
- [ ] **AUDIT-P3-01** 集中回归测试套件，用 `@pytest.mark.regression` 标记。
- [ ] **AUDIT-P3-02** 补齐 broker 异常输入测试（NaN/Inf/负数/0）。
- [ ] **AUDIT-P3-03** 补齐部分成交测试（大单分批成交）。
- [ ] **AUDIT-P3-04** 性能/压力测试（全 A 股股票池、长周期回测、并发回测）。
- [ ] **AUDIT-P3-05** 敏感信息脱敏（error_message 正则匹配 `password=|token=|api_key=`）。
- [ ] **AUDIT-P3-06** telemetry 与审计系统集成。
- [ ] **AUDIT-P3-07** miniqmt provider 使用模块常量替代内联 60。
- [ ] **AUDIT-P3-08** get_financials 增加日期范围过滤（纵深防御）。
- [ ] **AUDIT-P3-09** 因果性切点扩展（遍历所有 timestamp 或边界点）。
- [ ] **AUDIT-P3-10** 算子注册时强制要求附带 `prove_causality` 通过报告。
