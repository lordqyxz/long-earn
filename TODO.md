# TODO — 待办清单

> 最后更新：2026-08-30
>
> 按 **紧急 × 重要** 四象限组织（艾森豪威尔矩阵），合并功能开发与合规审计。
> 判定准则：
> - **重要**：扭曲回测可信度 / 阻断模拟盘准入 / 破坏 ToG 飞轮证据链
> - **紧急**：正在污染当前结果，或架构翻转后若不马上验证会把错误路径固化
>
> 威胁优先级仍适用：金融合规 / 数据正确性 > 功能完整性 > 工程质量。
>
> **修复进度追踪约定**：`[ ]` 未开始、`[~]` 部分完成、`[x]` 已完成（完成即从本文件移除）。所有修复必须配套回归测试。
> 当前系统**不具备直接进入实盘交易的合规条件**，Q2 信任项闭环后需重新审计方可进入模拟盘验证。
>
> **架构现状（ADR-018 + ADR-019）**：策略研发控制面为 ToG `ResearchAgent`；HTR 降为脚手架；事件 `prepare_context` 与显式多源数据层已落地；**统一存储已全量迁移 PostgreSQL（ADR-019）**，DuckDB 三库与 `backup/` 归档已删除。总览见 [docs/architecture.md](docs/architecture.md)。

---

## 当前冲刺（进行中）

> **主线：策略研发 — 寻找收益率最佳策略**（动量 × 财务质量 × 牛熊门控）。
> 2026-08-22 战果：引擎 4 bug 修复（rebalance_freq 门控 / 创业板涨跌停 ±20% / PG 审计毒化自愈 / 并行重复 attach）+ regime 哑铃 DSL 落地 + 指数行情入库。训练集最优为哑铃 W120_512890（质量动量 + 沪深300×120日均线门控 + 熊市红利低波ETF：-27.86% → +30.08%），但测试集 Walk-Forward fold 0（2025Q1）-30.66%，被稳定性门拒绝、未合并——指数横盘期的风格崩盘，指数绝对 MA 门防不住。当前无通过 OOS 门的合并候选，`best_strategy.yaml` 未变更。
> 2026-08-23 工程修复：并行回测内存放大治理（SharedMemory→mmap IPC 文件，worker 私有面板拷贝从 ~3 份/worker 降到共享页缓存；网格峰值内存从 112GB 级回落）+ regime warmup 盲区回归修复；附带消除 pytest 单独跑 backtest 测试的 -1 退出码。
> 2026-08-23 性能优化（对标 NautilusTrader 调研，计划见 `docs/superpowers/plans/2026-08-23-backtest-perf-optimization.md`）：确定性事件 ID（时间戳派生 bar_trace_id 替代逐事件 uuid4，审计因果链贯穿）+ VisibilityGuard 窗口截断（O(T²)→O(T·W)）+ 因子全期预计算（O(T·U)，等价性由算子因果性证明背书，多 seed 测试守护）+ merged panel 跨 run Arrow 缓存（service 层开启，key 含 PG 数据版本水位，写事务原子自增自动失效）+ 审计批量写入（缓冲 500 条 executemany，查询前 flush 保 read-your-writes）。基准（5 标的 × 2 年小面板）：带审计端到端 2.27s→0.43s（-81%，审计开销 -98%）；大池下 O(T²) 消除收益随池规模放大。下一热点：`portfolio.update_market_values` 每 bar polars filter。
> 2026-08-30 数据正确性修复：基准指数行情（000300/000905/000001/399006）纳入正式下载管线（`DataIngestionService.INDEX_QUOTES` 显式点名 + 按交易日增量维护，随 `download_data.py` 每次运行自动补齐），消除 regime 门控 benchmark 数据过期的静默退化威胁；存量数据已真机补齐到 2026-08-28。
> 2026-08-30 引擎正确性修复：`run_walk_forward_parallel` test 折起点误用 `train_ts[0]`（test 回测覆盖训练期，OOS 指标污染，edff513）；此前所有折级 OOS 指标均受此影响，本轮合并门为首个干净窗口验证。
> 2026-08-30 策略研发轮次结果：relative/combined 网格（1 对照 + 18 组合，训练集内）冠军 `com_rw60_m0`（训练 +162.17%，夏普 1.495，回撤 -20.57%），但 OOS 合并门 **CONTINUE**——测试集三折全负（-26%~-30%/折，夏普 -2.1~-2.9），S1 稳定性门拒绝；归因：价格动量股票腿在 2025-2026 震荡市风格翻转（反复止损），指数级门控无法挽救选股层 alpha 反号。纯 relative 模式训练集即全负，已排除。`best_strategy.yaml` 未变更（现任基准 OOS mean sharpe 1.47，合并门槛极高）。
> 2026-08-30 数据层死循环治理：财务同步水位表落地（`financial_sync_watermark` + 双水位判定，46382d8）；启动同步与回测读路径（`financial/sync.py::is_financial_stale`）共享同一水位与 `FINANCIAL_RECHECK_DAYS=7` 常量（常量下沉至 backtest.data.financial.sync，遵循 AGENTS.md 6.2「同款判定共享同一水位」铁律）。沉默股票从每次同步全量重查（实测 4620 只 ≈ 20 分钟/次）降为每 7 天一次小窗检查；批次成功才推进水位（含合法 0 行），异常保留重试；PIT 对齐不受影响（行数据仍带真实 announce_date）。行情路径评估后不引入水位：日更域数据状态自愈，仅 ~12 只退市/停牌标的每次多查数秒，且水位会有损逐日精确补齐语义。
> 2026-08-30 全系统评审与修复（OCR delegate 模式，记录见 `docs/reviews/`）：后端 Critical 6 / High 21 / Medium 56 / Low 63，前端 High 3 / Medium 8 / Low 14。修复分支 `fix/review-critical-high` 关闭全部 Critical/High + 约 25 项 Medium/Low（43 文件，+1406/−661），验证 ruff/lint-imports/pyright/pytest 1132 全绿 + 前端 tsc 零错误。要点：4 个旧脚本数据分割违规收敛为 config 派生窗口、`backtest_recent.py` 覆写合并门基准逻辑删除、operator_dev 沙箱 builtins 逃逸封堵、`log_return` 负 period 解析期拦截、OOS 合并门风控参数污染修复、并行 walk-forward warmup 对齐、SELL 超持仓护栏、API 导出/进度广播/清理护栏修复、资金流向视角结果不再被丢弃、前端 WS 重连泄漏修复。**回测语义有正确性变化**（碎股消除/超卖截断/挂单成交量约束/warmup 对齐——历史并行 fold 级数字与新版不可直接比较），合并后需重生成 openapi.json（详见 remediation 记录「行为变化」）。
> 次线：Web 前端开发（`web/`，React 18 + Vite + TypeScript + Tailwind + Radix UI + Recharts，对接 FastAPI `/api` 与 WebSocket；三页面骨架、OpenAPI 客户端、归因面板等已完成）。

- [ ] **regime relative/combined 通过 OOS 门**（`mode: relative`/`combined`）— 2026-08-30 轮次已收：combined 门控训练集显著占优但 OOS 全折崩溃（股票腿动量因子 OOS 反号，非门控问题）。下一轮方向：重设计股票腿（动量→基本面/反转混合，参考现任基准的净利增长选股），或放弃哑铃族转向基准增强
- [~] **Web 前端开发**（`web/`）— 次线，按需继续开发

---

## 观察项（需跟进）

- [~] **后台测试/冒烟 run 持续堆积**：持续有进程向共享 PG 写测试/冒烟回测 run（曾观测 dry-run 187 个无效 run 堆积），现均带 `test` 标签可被清理接口清除；建议排查写入源头进程，或定期使用看板清理按钮。
- [ ] **启动同步重复劳动（轻微）**：`_enrich_sectors_from_xtquant` 每次同步全量拉 THY1/DY1 板块映射（只回填空行，首轮后为 2 次 API 调用 + 空转）；`_is_price_stale`（`get_price_panel` 路径）被 ~12 只永久 stale 标的拖累反复进刷新分支（刷新增量，浪费有界）。均自限、非死循环。（2026-08-30 评估：行情路径不引入水位，见冲刺记录；板块回填空转留待后续）
- [ ] **并行 run_candidates 偶发 worker 失败**：哑铃网格阶段 2 中第 9/10 号任务（W250_511260 / W250_CASH）ERR——worker 内失败、无 RUN_START，疑似 worker 复用时环境性失败；单进程复现可跑通。影响面小（同窗结论不受影响），但动摇并行结果可信度，下轮大网格前值得排查。（2026-08-23：并行数据底座已从 SharedMemory 换为 mmap IPC 文件，Windows 句柄类环境性失败可能同源消除，待大网格复验；2026-08-30 大网格 19 任务两阶段未复现）
- [ ] **HTR 遗留线清退（ADR-021 联动）**（2026-08-30 立项）：`strategy_rd/htr_subgraph.py` + `strategy_rd/agents/`（约 9 处 LLM 调用点，含 `_should_retrieve` 检索路由、`decide` 循环控制流等 LLM 干确定性活的违例，及 `strategy_rd_supervisor.py` 死代码）+ `skills/personas/` 大师库，仍被 `cli.py` 与 `app/app.py` 的 research 端点使用。清退方案：调用方迁移至 ToG `ResearchAgent` 后整体删除遗留线，LLM 控制流决策按 ADR-021 规则化；`check_llm_call_sites.py` 白名单中遗留线条目随清退移除。迁移前冻结：不得新增调用方或在遗留线内扩展功能。
- [ ] **2026-08-30 评审暂缓项跟进**（记录见 `docs/reviews/2026-08-30-remediation.md` 第三节）：① 止盈成交价口径（日内 high 判定 + high 成交双重乐观）与风控卖出单贯通 `_pre_trade_check` 需专项决策——均改变回测成交语义，现有风控测试需同步重设计；② app 层绕 services 直连 `DataCache` 私有成员跑裸 SQL（六处）需下沉为公有方法；③ `tools/backtest_analyzer.py` 整模块死代码删除（与 app/analyzer.py 同名双轨）待所有权人确认；④ 合并后重生成 `web/openapi.json`（`scripts/_dump_openapi.py` + `npm run api:gen`，force 参数与分页边界已改变契约）；⑤ 其余 Medium（pg conninfo 转义、master_agent get_llm 单例、htr insight 覆盖、acceptance.primary_metric 失效、quality_momentum null→满分、ciccwm TLS 等）逐项独立修复。

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
- [ ] **AUDIT-P3-03** 部分成交（大单分批）— **暂不做**（2026-08 决定：大单分批暂不实现）
- [ ] **AUDIT-P3-04** 性能/压力（全 A、长周期、并发）
- [ ] **AUDIT-P3-05** 敏感信息脱敏（`password=` / `token=` / `api_key=`）— **暂不做**（2026-08 决定：非功能需求，暂不排期）
- [ ] **AUDIT-P3-06** telemetry 与审计集成
- [ ] **AUDIT-P3-07** miniqmt 内联常量清理
- [ ] **AUDIT-P3-09** 因果性切点扩展
