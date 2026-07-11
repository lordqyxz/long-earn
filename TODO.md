# TODO — 待办清单

> 最后更新：2026-07-12
>
> 按「重要性 + 威胁程度」统一排序，合并功能开发待办与合规审计（2026-07-08）。
> 威胁优先级：金融合规 / 数据正确性 > 功能完整性 > 工程质量。
>
> **修复进度追踪约定**：每项前的复选框 `[ ]` 完成后改为 `[x]` 并附 commit hash。所有修复必须配套回归测试。
> 当前系统**不具备直接进入实盘交易的合规条件**，P0 全部闭环后需重新审计方可进入模拟盘验证。

---

## P0 — 致命威胁（回测结果虚高 / 未来函数泄漏 / 金融制度违规）

> 直接影响回测可信度与系统对外输出，必须立即修复。

### 数据正确性

- [x] **AUDIT-P0-02** CompositeDataProvider ffill-before-sort（未来函数潜在泄漏）— 已修复（commit `a0de9ad`）
  - 位置：`src/long_earn/backtest/data/provider.py:437-438`（已 `sort_index()` 在 `ffill()` 之前）；`miniqmt_provider.py:1002-1006` 同模式
  - 问题：`pd.merge(how="outer")` 不保证排序，`groupby.ffill()` 按行序填充，可能将未来值填到过去。
  - 修复：将 `sort_index()` 移到 `ffill()` 之前，与 miniqmt 对齐。注：ciccwm/akshare 财务降级分支已屏蔽，无 ffill 路径，故无未对齐问题。

### 回测引擎可信度

- [x] **AUDIT-P0-03** metrics_unreliable 标志端到端冒泡断裂 — 已修复（commit `a0de9ad`）
  - 位置：`src/long_earn/backtest/models.py:47`（字段）、`engine/core.py:275-290`（_build_result 设置）、`engine/parallel.py:71,169,183`（BacktestOutcome + GridResult.best 过滤）
  - 问题：`BacktestResult` 模型无 `metrics_unreliable` 字段；引擎 `_build_result` 从不设置；并行回测 `BacktestOutcome` 也无该字段，退化策略混入结果集。
  - 修复：(1) `BacktestResult` 增加字段 `metrics_unreliable: bool = Field(default=False)`；(2) `core.py:_build_result` 检测 skip_ratio/partial_fills 超过 50% 设置；(3) `parallel.py:BacktestOutcome` 增加该字段，`GridResult.best` 过滤掉 `metrics_unreliable=True`。测试：`tests/unit/test_backtest/test_metrics.py:144,167`。

- [x] **AUDIT-P0-04** 撮合无成交量限制，永远全额成交 — 已修复（commit `a0de9ad`）
  - 位置：`src/long_earn/backtest/engine/broker.py:38-39`（`max_volume_participation=0.1`、`impact_cost_k=0.01`）、`46-61`（`compute_impact_bps` 平方根冲击模型）、`228-232`（`fill_quantity = min(order.quantity, volume*participation)`）、`260,264`（`partial_fill` 标记）
  - 问题：`fill_quantity = order.quantity`，策略可在一个 bar 内买入当日成交量 1000% 的份额，仅扣 2bps 滑点。无冲击成本模型。
  - 修复：(1) `TradingCostConfig` 增加 `max_volume_participation: float = 0.1`；(2) Broker 读取当日 volume 限制；(3) 平方根冲击模型 `impact = k * sqrt(participation)`；(4) `FillEvent` 增加 `partial_fill` 标记。测试：`test_compliance.py:585`。

### A 股交易制度（违反交易规则，回测系统性失真）

- [x] **AUDIT-P0-05** 前视偏差：T日信号 T日 close 成交 — 已修复（commit `a0de9ad`）
  - 位置：`src/long_earn/backtest/engine/core.py:93-95`（`_pending_signals` 队列）、`489-491`（信号入队不立即执行）、`393-401`（T+1 日以当日 open 撮合 pending 信号）
  - 问题：策略基于当日 close 决策并以当日 close 成交。
  - 修复：信号 T 日生成后进入 pending 队列，T+1 日 `_process_timestamp` 用当日 `open` 撮合。测试：`test_engine.py:572,625`。

- [x] **AUDIT-P0-06** T+1 制度完全未实现 — 已修复（commit `a0de9ad`）
  - 位置：`src/long_earn/backtest/domain/entities.py:180`（`available_date` 字段）、`portfolio.py:227-271`（卖出前校验）、`portfolio.py:391-395`（成交后 `available_date = fill.timestamp + 1d`）、`core.py:545-546,602-603,717-718`（引擎级 T+1 锁定）
  - 问题：当日买入当日可卖出，违反 A 股 T+1 规则，高估日内反转策略。
  - 修复：`Position` 增加 `available_date` 字段（`fill_date + 1`），卖出前校验，否则记 `ORDER_SKIPPED`。测试：`test_compliance.py:158,225`。

- [x] **AUDIT-P0-07** 涨跌停板完全未处理 — 已修复（commit `a0de9ad`）
  - 位置：`src/long_earn/backtest/engine/core.py:107-119`（`_compute_price_limits` 用 `prev_close*1.1/0.9`）、`381-391`（每 bar 更新）、`155-170`（`_check_limit_up_down` 涨停拒买/跌停拒卖）
  - 问题：涨停可买入、跌停可卖出，回测业绩系统性虚高。
  - 修复：基于 `prev_close * 1.1/0.9` 计算涨跌停价，pre-trade 风控拒绝涨停买入/跌停卖出，记 `ORDER_SKIPPED`。测试：`test_compliance.py:363,383,399,409`。

- [x] **AUDIT-P0-08** Pre-trade 单笔风控缺失 — 已修复（commit `a0de9ad`）
  - 位置：`src/long_earn/backtest/engine/core.py:121-153`（`_pre_trade_check`）、`822-826`（撮合前调用并记 `ORDER_SKIPPED`）
  - 问题：风控检查在策略信号生成之前，订单生成后直接撮合，中间无单笔订单合规检查。
  - 修复：在撮合前插入 `_pre_trade_check`，覆盖涨跌停、价格有效性、停牌（volume=0）。T+1 约束在 Portfolio 完成，成交量限制在 Broker 完成（分工明确）。

### 审计可追溯性

- [x] **AUDIT-P0-10** 并行回测无 DuckDB 持久化 — 已修复（commit `a0de9ad`）
  - 位置：`src/long_earn/backtest/engine/parallel.py:90-98`（worker 注入 `DuckDBAuditProvider(db_path=Path(task.audit_db_path))`）、`75-76`（`_derive_worker_db_path` 派生 worker 专属路径）
  - 问题：并行 worker `audit_logger=InMemoryAuditTrail()` 未注入 `audit_provider`，进程结束后审计数据全部丢失。
  - 修复：并行 worker 注入 `DuckDBAuditProvider`，每个 worker 独立 db 文件。

- [x] **AUDIT-P0-11** InMemoryAuditTrail 缺少 timestamp 字段 — 已修复（commit `45ab884`）
  - 位置：`src/long_earn/backtest/engine/core.py:890-893`（`ts = timestamp or datetime.now()`，entry 含 `"timestamp": ts`）、`915`（传给 `db_audit.log_transition(..., timestamp=ts)`）
  - 问题：docstring 声称"保证内存审计与 DuckDB 审计字段一致"，但 `timestamp` 字段在内存审计中完全缺失。
  - 修复：在 `_log_audit` 的 entry 字典加入 `"timestamp": timestamp or datetime.now()`，并传给 `db_audit.log_transition`。测试：`test_engine.py:436`。

### CI / 测试基线

- [x] **AUDIT-P0-12** CI Python 版本错配 — 已修复（commit `a0de9ad`）
  - 位置：`.github/workflows/ci.yml` 两处 `python-version: "3.13"`，与 `pyproject.toml:8 requires-python = "==3.13.*"` 一致
  - 修复：CI 改为 `python-version: "3.13"`。

- [~] **AUDIT-P0-13** 覆盖率门禁未生效 + 集成测试不在 CI — 部分完成（commit `a0de9ad`）
  - 已完成：(1) `pyproject.toml:97` `fail_under = 60`（原为 0，门禁已生效）；(2) `ci.yml` test job 加入 `uv run pytest tests/integration/ -v -m "not requires_credentials"`。
  - 未完成：`fail_under` 目标 80（当前 60）；关键路径（broker/engine/causality）单独 95 单项门禁未实现。
  - 位置：`pyproject.toml:97`、`.github/workflows/ci.yml`

- [x] **AUDIT-P0-14** 489% 虚高回归测试不在 pytest 套件 — 已修复（commit `a0de9ad`）
  - 位置：`tests/unit/test_backtest/test_pit_regression.py`（5 个 PIT 测试，`@pytest.mark.regression` 标记，由 CI `pytest tests/unit/` 自动收集）
  - 修复：转为 pytest 回归测试（用 mock 数据源），加入 `tests/unit/test_backtest/test_pit_regression.py`，标记 `@pytest.mark.regression`。

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

- [x] **AUDIT-P1-03** 过户费缺失（沪市双向万分之 0.1）— 已修复（commit `45ab884`）
  - 位置：`src/long_earn/backtest/engine/broker.py:40`（`transfer_fee_rate=0.00001`）、`247-250`（沪市 `.SH` 双向征收）、`265,329`（`FillEvent` 含 `transfer_fee`）、`312-315`（pending fill 路径）
  - 修复：`TradingCostConfig` 增加 `transfer_fee_rate`，根据 symbol 后缀（.SH vs .SZ）判断征收。测试：`test_compliance.py:528,562`。

- [ ] **AUDIT-P1-04** 行业集中度完全未实现（纸面约束无执行）
  - 位置：`dsl.py:87-101` RiskControlConfig 无行业字段
  - 修复：(1) RiskControlConfig 增加 `max_industry_pct`；(2) 数据层补充 `industry` 字段；(3) `Portfolio.process_signal` 生成订单前按行业聚合检查。

- [x] **AUDIT-P1-05** 止盈缺失（仅有止损）— 已修复（commit `45ab884`）
  - 位置：`src/long_earn/backtest/engine/core.py:72,84`（`take_profit` 参数）、`528-529`（`_run_risk_checks` 含 `_check_take_profit` 分支）、`534-572`（`_check_take_profit` 盈利超阈值强制卖出）
  - 注：`take_profit` 在引擎构造参数而非 `dsl.py:96 RiskControlConfig`（轻微偏差，RiskControlConfig 仅有 `stop_loss`）。
  - 修复：`_run_risk_checks` 增加 `_check_take_profit` 分支。测试：`test_compliance.py:635`。

- [x] **AUDIT-P1-06** max_turnover 是"死配置" — 已修复（commit `68e3936`）
  - 位置：`src/long_earn/backtest/engine/core.py:88`（`self._max_turnover`）、`766`（`max_turnover=getattr(self, "_max_turnover", None)`）、`portfolio.py:134,178-185`（换手率检查：`turnover_rate > max_turnover` 时 `scale = max_turnover / turnover_rate` 缩放订单）
  - 修复：在 `Portfolio.process_signal` 实现换手率检查（`sum(|new_weight - old_weight|) <= max_turnover`）。

- [x] **AUDIT-P1-07** 滑点固定 bps 无动态模型 — 已修复（commit `a0de9ad`）
  - 位置：`src/long_earn/backtest/engine/broker.py:46-61`（`compute_impact_bps` 平方根冲击模型 `k * (participation ** 0.5) * 10000`）、`236-237`（`total_slip_bps = slippage_bps + impact_bps`）
  - 修复：改为 `base_bps + impact_bps * sqrt(participation)` 动态滑点模型。

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

- [x] **AUDIT-P1-10** DuckDB 使用非单调墙钟 — 已修复
  - 位置：`src/long_earn/backtest/engine/audit.py`（`_seq` 自增序列号 + `seq` 列 + 主键改为 `(run_id, trace_id, seq)`）
  - 修复：新增 `seq BIGINT` 自增列，主键从 `(run_id, trace_id, timestamp)` 改为 `(run_id, trace_id, seq)`，`get_causal_chain` 按 `seq` 排序；旧表自动迁移 `ALTER TABLE ADD COLUMN seq`。测试：`test_duckdb_audit.py::TestSeqMonotonicity`（墙钟回退 + 重复 timestamp 不覆盖）。

- [x] **AUDIT-P1-11** 异常路径未捕获 KeyboardInterrupt/SystemExit — 已修复
  - 位置：`src/long_earn/backtest/engine/core.py`（`except (KeyboardInterrupt, SystemExit):` 记录 `RUN_ERROR(status=INTERRUPTED)` 后 `raise`）
  - 修复：捕获用户中断/系统退出，记录审计后重新抛出，不返回虚假的 `BacktestResult(success=False)`。测试：`test_engine.py::test_run_keyboard_interrupt_not_swallowed`。

- [x] **AUDIT-P1-12** `_log_audit` 自身异常未保护 — 已修复（commit `45ab884`）
  - 位置：`src/long_earn/backtest/engine/core.py:902-905`（`try: self.audit_logger.log_transition(**entry) except Exception: logger.warning("InMemoryAuditTrail 写入失败，已降级")`）、`907-919`（`db_audit` 路径同样 `try/except` 包裹）
  - 修复：内部加 try/except，确保审计写入失败不阻断主流程但记录降级日志。

- [~] **AUDIT-P1-13** 无法完整重放回测 — 部分完成（commit `a0de9ad`）
  - 位置：`src/long_earn/backtest/engine/core.py:220-231`（RUN_START payload）
  - 已完成：RUN_START 含 `start_date, end_date, symbols_count, benchmark_symbol, stop_loss, max_drawdown_limit, max_position_pct, max_positions, strategy_id`。
  - 未完成：(1) `symbols_count` 而非完整 `symbols` 列表（仅数量）；(2) 无 `strategy_yaml` 或 `strategy_hash`（仅 `strategy_id` 字符串）；(3) MARKET_DATA slab 关键字段摘要、SIGNAL 保持 dict 写入 JSON 列未明确处理。
  - 修复：RUN_START 增加完整 symbols 列表、`strategy_yaml` 或 `strategy_hash`；MARKET_DATA payload 增加 slab 关键字段摘要；SIGNAL 的 signals 保持原 dict 类型写入 DuckDB JSON 列。

### 测试

- [x] **AUDIT-P1-14** A 股合规专项测试完全缺失 — 已修复（commit `45ab884` + `cda29cb`）
  - 位置：`tests/unit/test_backtest/test_compliance.py`（13 个测试函数）
  - 修复：新建 `tests/unit/test_backtest/test_compliance.py`，覆盖 T+1（158,225,304）、涨跌停（363,383,399,409）、停牌（462）、过户费（528,562）、成交量限制（585）、止盈（635）。

- [x] **AUDIT-P1-15** metrics_unreliable 无引擎层端到端测试 — 已修复（commit `cda29cb`）
  - 位置：`tests/unit/test_backtest/test_metrics.py:144 test_volume_limit_marks_metrics_unreliable`、`:167 test_high_skip_ratio_marks_metrics_unreliable`
  - 修复：使用 mock_data_provider 真实引擎路径，断言 `result.metrics_unreliable` 为 True。

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

### 引擎

- [~] **AUDIT-P1-19** 正确性证明文档与代码脱节 — 部分完成
  - 位置：`docs/research/backtest-engine-correctness-proof.md`
  - 已完成：文档存在，头部声明"基于当前代码实现（v1.0.1）"。
  - 未完成：(1) 文档 2.5.1 写 `annual_return = (1 + total_return) ** annual_factor - 1`（几何年化），而代码 `core.py:1283 annual_return = float(np.mean(returns)) * 252`（算术年化）— **直接矛盾**；(2) 文档 2.5.6 自承"Alpha 计算非标准 Jensen's Alpha"，代码 `core.py:1093 alpha = port_annual - beta * bm_annual` 未修正；(3) 无 commit hash 校验机制，无 CI 校验文档代码片段与源码一致。
  - 修复：同步文档与代码；CI 校验文档代码片段与源码一致，或文档顶部声明对应 commit hash。

---

## P2 — 中优先级（功能完整性 + 建模精度提升）

### 事件推理子图集成（ADR-007 后续）

Phase 2（多源采集器 + 事件推理子图 + 主图路由）与 Phase 3 数据层（财务字段全量提取 + PIT 修复）均已完成。

- [ ] **子图集成 + Dashboard**
  - stock_analysis / strategy_rd 调 `store.activate()` 注入事件上下文
  - Dashboard 事件流可视化

### 算子研发闭环完整性（ADR-009 后续）

- [ ] **operator_dev register 写盘**：register 节点写 `.py` 到 `operators/<category>/<name>.py`，产物持久化到代码库走 CI/审查（当前仅内存热注册，进程重启即丢）。
- [ ] **主图挂载**：`agent.py` 注册 operator_dev / strategy_optimization 子图入口，支持 CLI / 路由触发。
- [ ] **清理双套体系**：评估 `ml_strategy.py` / `strategy_templates.py` 是否可由算子目录 + 新 DSL 完全替代。
- [ ] **退役 evaluator**：策略全部迁移到算子路径后删除 `SafeExpressionEvaluator` + `_extract_field_names`（ADR-003 标记 Superseded by ADR-009）。

### 建模精度与测试质量

- [ ] **AUDIT-P2-01** 算子路径选空不记 failure（`operator_executor.py:98-122`）
  - 修复：filter 结果使 `selected_df.height` 变为 0 时记录 failure 并 break，与表达式路径对齐。

- [x] **AUDIT-P2-02** 现金不足抛异常终止整个回测 — 已修复（commit `a0de9ad`）
  - 位置：`src/long_earn/backtest/engine/portfolio.py:376-380`（`if cost > self.cash + 1e-6:` 分支 `logger.warning(f"现金不足跳过买入 {symbol}")` 并记录跳过返回，注释 "P2-02 + P0-04：现金不足时跳过该笔交易而非抛异常终止回测"）
  - 修复：改为拒绝该笔买入 + 记录审计，而非抛异常终止回测。

- [~] **AUDIT-P2-03** ORDER_SKIPPED 仅覆盖单一原因 — 部分完成
  - 位置：`src/long_earn/backtest/engine/core.py:771-785`（T+1 跳过）、`804-808`（现金不足跳过）、`826-838`（pre_trade 跳过），均记 `ORDER_SKIPPED` 并含 `reason` 字段；`portfolio.py:270-271` 返回 `skip_reason` 字符串
  - 已完成：覆盖多原因（T+1/现金不足/pre_trade/涨跌停/停牌）。
  - 未完成：`portfolio.py:324-328` skipped_reasons 仍仅传 reason 字符串，未完全统一为结构化原因枚举。
  - 修复：`Portfolio` 持有审计回调或返回跳过原因列表，由引擎统一记 `ORDER_SKIPPED`。

- [ ] **AUDIT-P2-04** 风控触发后整体跳过策略信号
  - 位置：`core.py:320-346`
  - 修复：将"风控清仓"与"策略信号生成"解耦，风控清仓后仍允许策略生成新信号。

- [ ] **AUDIT-P2-05** Walk-Forward 并行版无 failed_folds 追踪
  - 位置：`parallel.py:run_walk_forward_parallel`
  - 修复：与 `core.py:walk_forward_run` 对齐，增加 failed_folds 与退化检测。

- [ ] **AUDIT-P2-06** max_workers<=1 时环境变量泄漏
  - 位置：`parallel.py:73`
  - 修复：子函数内用 `os.environ` 上下文管理器包裹。

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

- [x] **AUDIT-P2-13** query_events 的 key 拼接存在 SQL 注入风险 — 已修复
  - 位置：`src/long_earn/backtest/engine/audit.py`（`_QUERY_FILTER_WHITELIST` 白名单 + ValueError 拒绝非白名单 key）
  - 修复：对 `filters` 的 key 做白名单校验（event_type/trace_id/parent_id/component/status/latency_ms）。测试：`test_duckdb_audit.py`。

- [x] **AUDIT-P2-14** DuckDB 单连接非线程安全 — 已修复
  - 位置：`src/long_earn/backtest/engine/audit.py`（`threading.Lock` 保护所有 DuckDB 连接访问）
  - 修复：所有 DuckDB 连接访问（`_init_db`/`log_event`/`query_events`/`get_causal_chain`/`close`）通过 `self._lock` 串行化。测试：`test_duckdb_audit.py`（4 线程并发写 20 条 + 并发读写）。

- [ ] **AUDIT-P2-15** 使用真实交易日历替代 freq="B"
  - 修复：使用 `exchange_calendars` 的 XSHG 日历。

- [ ] **AUDIT-P2-16** latency_ms 仅 RUN_END/RUN_ERROR 有值
  - 修复：为 MARKET_DATA、SIGNAL、ORDER、FILL、RISK_TRIGGER 等关键事件计算并写入单步延迟。

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

---

## 已完成（归档）

### 合规审计修复

- [x] **AUDIT-P0-01** 60天披露延迟对年报不足 — 已修复（commit `c97d40b`）。ADR-007 改用 miniqmt `m_anntime` 字段作为 `announce_date`（真实财报发布日），`_quarterly_to_daily` 以 `announce_date` 作为信息可见起点。财务接口统一到 miniqmt 后 akshare/ciccwm 财务方法已删除（commit `67c80d1`）。PIT 修复经实盘回测验证通过（commit `16b1d8d`）。

### 功能开发里程碑

- [x] **回测引擎**：事件驱动核心链路、Agent 友好 API、金融级可信验证、Walk-Forward OOS、状态化风控（ADR-005）。
- [x] **ciccwm 财经数据 Provider**（ADR-006）。
- [x] **记忆系统 v3.0 物质-运动架构**（ADR-007 Phase 1）。
- [x] **并行回测 + 统一模板渲染**（ADR-008，A 部分模板渲染已被 ADR-011 废弃，B 部分并行回测继续有效）。
- [x] **算子目录核心链路 + gap_detector 接入**（ADR-009）。
- [x] **HTR 假设树精炼** Phase 1-5（ADR-010）。
- [x] **统一 jinja2 + ChatPromptTemplate 提示词模板** Phase 1-5（ADR-011）。
- [x] **大师智能节点可复用技能包** Phase 1-4（ADR-012）。
- [x] **数据层架构整理**（commit `9a89e5a`）。
- [x] **量化数据分割规范**：训练/测试/验证三段式。
- [x] **ADR-007 Phase 2 新闻事件推理引擎**：多源采集器 + 事件推理子图 + 主图路由。
- [x] **ADR-007 Phase 3 财务接口统一到 miniqmt + 四表合并全量字段**（commit `67c80d1` + `107a891` + `c97d40b`）。
- [x] **数据下载并发能力**（commit `ae6475a`）。
- [x] **实时数据对接**：`RealtimeDataProvider` + `PriceAlertMonitor`（ADR-011）。
- [x] **增强分析视角**：`FundFlowAnalyst` 第 5 视角（ADR-011）。
