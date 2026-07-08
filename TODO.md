# TODO — 待办清单

> 最后更新：2026-07-09
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

- [ ] **AUDIT-P0-02** CompositeDataProvider ffill-before-sort（未来函数潜在泄漏）
  - 位置：`src/long_earn/backtest/data/provider.py:494-495`、`ciccwm_provider.py:350`、`akshare_provider.py:266`
  - 问题：`pd.merge(how="outer")` 不保证排序，`groupby.ffill()` 按行序填充，可能将未来值填到过去。`miniqmt_provider.py:888-892` 已修复（先 sort_index 后 ffill），其余三处未对齐。
  - 修复方案：将三处 `sort_index()` 移到 `ffill()` 之前，与 miniqmt 对齐。

### 回测引擎可信度

- [ ] **AUDIT-P0-03** metrics_unreliable 标志端到端冒泡断裂
  - 位置：`src/long_earn/backtest/models.py:8-44`、`engine/core.py:624-690`、`engine/parallel.py:51-68`
  - 问题：`BacktestResult` 模型无 `metrics_unreliable` 字段；引擎 `_build_result` 从不设置；并行回测 `BacktestOutcome` 也无该字段，退化策略混入结果集。
  - 修复方案：(1) `BacktestResult` 增加字段 `metrics_unreliable: bool = Field(default=False)`；(2) `core.py:_build_result` 接受策略诊断并设置；(3) `parallel.py:BacktestOutcome` 增加该字段，`GridResult.best` 过滤掉 `metrics_unreliable=True`。

- [ ] **AUDIT-P0-04** 撮合无成交量限制，永远全额成交
  - 位置：`src/long_earn/backtest/engine/broker.py:190-216`
  - 问题：`fill_quantity = order.quantity`，策略可在一个 bar 内买入当日成交量 1000% 的份额，仅扣 2bps 滑点。无冲击成本模型。
  - 修复方案：(1) `TradingCostConfig` 增加 `max_volume_participation: float = 0.1`；(2) Broker 读取当日 volume，`fill_quantity = min(order.quantity, volume * participation)`；(3) 引入平方根冲击模型 `impact = k * sqrt(order_amount / daily_volume)`；(4) `FillEvent` 增加 `partial_fill` 标记。

### A 股交易制度（违反交易规则，回测系统性失真）

- [ ] **AUDIT-P0-05** 前视偏差：T日信号 T日 close 成交
  - 位置：`src/long_earn/backtest/engine/core.py:321,529,548`
  - 问题：策略基于当日 close 决策并以当日 close 成交。`strategy_develop_prompt.md:196` 明确声称"T+1 执行"但未实现。
  - 修复方案：信号 T 日生成后订单进入 pending 队列，T+1 日 `_process_timestamp` 开头用当日 `open` 撮合。

- [ ] **AUDIT-P0-06** T+1 制度完全未实现
  - 位置：`src/long_earn/backtest/domain/entities.py:170-181`（Position 无 entry_date/available_date）
  - 问题：当日买入当日可卖出，违反 A 股 T+1 规则，高估日内反转策略。
  - 修复方案：`Position` 增加 `available_date` 字段（`fill_date + 1`），卖出前校验 `pos.available_date <= current_ts`，否则记 `ORDER_SKIPPED`。

- [ ] **AUDIT-P0-07** 涨跌停板完全未处理
  - 位置：全代码搜索 `limit_up|limit_down|涨停|跌停` 在 src 下零匹配
  - 问题：涨停可买入、跌停可卖出，回测业绩系统性虚高，封板策略严重失真。
  - 修复方案：数据层增加 `limit_up`/`limit_down` 字段（或用 `prev_close * 1.1` 计算），pre-trade 风控拒绝涨停买入/跌停卖出，记 `ORDER_SKIPPED`。

- [ ] **AUDIT-P0-08** Pre-trade 单笔风控缺失
  - 位置：`src/long_earn/backtest/engine/core.py:548`（撮合前无风控门）
  - 问题：风控检查在策略信号生成之前（检查已有持仓），订单生成后直接撮合，中间无单笔订单合规检查。
  - 修复方案：在撮合前插入 `_pre_trade_check`，覆盖涨跌停、停牌、成交量占比、单笔金额上限、行业集中度、T+1 约束。

### 审计可追溯性

- [ ] **AUDIT-P0-10** 并行回测无 DuckDB 持久化
  - 位置：`src/long_earn/backtest/engine/parallel.py:82-89`
  - 问题：并行 worker `audit_logger=InMemoryAuditTrail()` 未注入 `audit_provider`，进程结束后审计数据全部丢失。
  - 修复方案：并行 worker 注入 `DuckDBAuditProvider`（每个 worker 独立 db_path 或共享只读+独立写连接）。

- [ ] **AUDIT-P0-11** InMemoryAuditTrail 缺少 timestamp 字段
  - 位置：`src/long_earn/backtest/engine/core.py:587-596`
  - 问题：docstring 声称"保证内存审计与 DuckDB 审计字段一致"，但 `timestamp` 字段在内存审计中完全缺失。
  - 修复方案：在 `_log_audit` 的 entry 字典加入 `"timestamp": timestamp or datetime.now()`，并传给 `db_audit.log_transition`。

### CI / 测试基线

- [ ] **AUDIT-P0-12** CI Python 版本错配
  - 位置：`.github/workflows/ci.yml:16,35` 用 `3.11`，`pyproject.toml:8` 要求 `==3.13.*`
  - 修复方案：CI 改为 `python-version: "3.13"`。

- [ ] **AUDIT-P0-13** 覆盖率门禁未生效 + 集成测试不在 CI
  - 位置：`pyproject.toml:94` `fail_under = 0`；`ci.yml:38` 仅跑 `tests/unit/`
  - 修复方案：(1) `fail_under` 改为 `80`（与 README 目标一致），关键路径（broker/engine/causality）单独 `95`；(2) CI 加入 `tests/integration/`，为需凭证的测试加 `@pytest.mark.requires_credentials` 跳过。

- [ ] **AUDIT-P0-14** 489% 虚高回归测试不在 pytest 套件
  - 位置：`scripts/test_pit_fix_e2e.py` 是独立脚本，CI 不运行
  - 修复方案：转为 pytest 回归测试（用 mock 数据源替代真实 LLM/akshare），加入 `tests/unit/test_backtest/test_pit_regression.py`，标记 `@pytest.mark.regression`。

---

## P1 — 高优先级（A 股合规性 + 建模精度）

> 影响建模精度与合规性，P0 闭环后应立即推进。

### 数据层

- [ ] **AUDIT-P1-01** universe 缓存用当前成分股标注历史日期（幸存者偏差）
  - 位置：`akshare_provider.py:316,323`、`miniqmt_provider.py:978,991`
  - 修复：`save_universe` 用 `date.today()` 而非请求 `date` 作为快照日期；回测结果增加 `universe_pit_warning` 标志。

- [ ] **AUDIT-P1-02** Composite/Ciccwm provider 对所有列 ffill（含价格）
  - 位置：`provider.py:494`、`ciccwm_provider.py:350`
  - 修复：仅对财务列 ffill（参照 akshare 的 `fin_cols` 过滤），价格列保持 NaN。

### 交易执行

- [ ] **AUDIT-P1-03** 过户费缺失（沪市双向万分之 0.1）
  - 位置：`src/long_earn/backtest/engine/broker.py:22-48`
  - 修复：`TradingCostConfig` 增加 `transfer_fee_rate`，根据 symbol 后缀（.SH vs .SZ）判断征收。

- [ ] **AUDIT-P1-04** 行业集中度完全未实现（纸面约束无执行）
  - 位置：`dsl.py:87-101` RiskControlConfig 无行业字段
  - 修复：(1) RiskControlConfig 增加 `max_industry_pct`；(2) 数据层补充 `industry` 字段；(3) `Portfolio.process_signal` 生成订单前按行业聚合检查。

- [ ] **AUDIT-P1-05** 止盈缺失（仅有止损）
  - 位置：`core.py:389`、`dsl.py:87-101`
  - 修复：RiskControlConfig 增加 `take_profit`，`_run_risk_checks` 增加 `_check_take_profit` 分支。

- [ ] **AUDIT-P1-06** max_turnover 是"死配置"
  - 位置：`dsl.py:93` 定义但全代码无引用
  - 修复：要么在 `Portfolio.process_signal` 实现换手率检查（`sum(|new_weight - old_weight|) <= max_turnover`），要么删除该字段避免误导。

- [ ] **AUDIT-P1-07** 滑点固定 bps 无动态模型
  - 位置：`broker.py:34` `slippage_bps=2.0`
  - 修复：改为 `base_bps + impact_bps * sqrt(order_qty / (0.1 * ADV))`。

- [ ] **AUDIT-P1-08** 高级订单类型未接入引擎主流程
  - 位置：`broker.py:90-118`（LIMIT/STOP/OCO 实现）但 `core.py:548` 仅用市价单
  - 修复：扩展 `SignalEvent` 允许携带订单类型与价格，或提供 `Strategy.submit_order()` 接口。

- [ ] **AUDIT-P1-09** 停牌处理依赖数据缺失的隐式逻辑
  - 位置：全代码无 `停牌|suspend|halt|is_trading` 检查
  - 修复：数据层增加 `is_tradable`/`is_suspended` 布尔字段，pre-trade 风控拒绝停牌日订单。

### 审计日志

- [ ] **AUDIT-P1-10** DuckDB 使用非单调墙钟
  - 位置：`audit.py:165` `datetime.now()`
  - 修复：使用 `time.monotonic()` 或 `datetime.utcnow()` + 序列号；主键改为 `(run_id, trace_id, event_type)` 或添加自增序列号。

- [ ] **AUDIT-P1-11** 异常路径未捕获 KeyboardInterrupt/SystemExit
  - 位置：`core.py:204` `except Exception`
  - 修复：改用 `except BaseException` 或额外捕获 `KeyboardInterrupt`/`SystemExit`。

- [ ] **AUDIT-P1-12** `_log_audit` 自身异常未保护
  - 位置：`core.py:569-607`
  - 修复：内部加 try/except，确保审计写入失败不阻断主流程但记录降级日志。

- [ ] **AUDIT-P1-13** 无法完整重放回测
  - 位置：`core.py:124-134` RUN_START payload 不完整
  - 修复：(1) RUN_START 增加完整 symbols 列表、`strategy_yaml` 或 `strategy_hash`；(2) MARKET_DATA payload 增加 slab 关键字段摘要；(3) SIGNAL 的 signals 保持原 dict 类型写入 DuckDB JSON 列。

### 测试

- [ ] **AUDIT-P1-14** A 股合规专项测试完全缺失
  - 修复：新建 `tests/unit/test_backtest/test_compliance.py`，覆盖 T+1、涨跌停、成交量限制、最小成交单位。

- [ ] **AUDIT-P1-15** metrics_unreliable 无引擎层端到端测试
  - 修复：真实引擎 + 真实 DSLStrategy + filter 失败 → `result.metrics_unreliable=True`。

- [ ] **AUDIT-P1-16** 审计流测试极度单薄（仅 1 个测试函数）
  - 修复：覆盖全部 12 种事件类型，验证 parent_id 因果链和 run_id 关联性。

- [ ] **AUDIT-P1-17** 数值稳定性测试缺失
  - 修复：`test_numerics.py` 加入 NaN/Inf/极值/除零/窗口超长场景。

- [ ] **AUDIT-P1-18** 风控规则覆盖不足
  - 修复：补齐 `max_position_pct` / `max_positions` 触发测试；风控触发后断言 RISK_TRIGGER 审计事件、持仓清空、后续 bar 被跳过。

### 引擎

- [ ] **AUDIT-P1-19** 正确性证明文档与代码脱节
  - 位置：`docs/research/backtest-engine-correctness-proof.md`
  - 问题：年化口径（几何 vs 算术）、Alpha 定义、止损成交价描述与代码不符。
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

- [ ] **AUDIT-P2-02** 现金不足抛异常终止整个回测（`portfolio.py:299-303`）
  - 修复：改为拒绝该笔买入 + 记录审计，而非抛异常终止回测。

- [ ] **AUDIT-P2-03** ORDER_SKIPPED 仅覆盖单一原因
  - 位置：`core.py:532-546`
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

- [ ] **AUDIT-P2-09** 统一 test_data_provider.py 入契约套
  - 修复：将 `TestMergedPanelFfillSorted` 参数化为 3 provider 共用。

- [ ] **AUDIT-P2-10** 扩展算子数值正确性覆盖
  - 修复：EMA / RSI / MACD / Bollinger 在 test_numerics.py 中补齐公式对齐测试。

- [ ] **AUDIT-P2-11** 基准对比指标精度测试
  - 修复：Alpha/Beta/IR 与 numpy 直接计算一致（类似 `test_returns_match_numpy_formula`）。

- [ ] **AUDIT-P2-12** 因果性扰动方式单一
  - 位置：`causality.py:54-58` 仅置 NaN
  - 修复：补充极端值（1e308）、负数、随机大数扰动，检测 `fill_null(0)` 类隐藏泄漏。

- [ ] **AUDIT-P2-13** query_events 的 key 拼接存在 SQL 注入风险
  - 位置：`audit.py:86-88`
  - 修复：对 `filters` 的 key 做白名单校验。

- [ ] **AUDIT-P2-14** DuckDB 单连接非线程安全
  - 位置：`audit.py:19`
  - 修复：并行/异步场景使用连接池或每线程独立连接。

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
