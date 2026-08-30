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
> **架构现状（ADR-018 → ADR-022）**：策略研发控制面为 ToG `ResearchAgent`（ADR-018）；**ADR-010 HTR 编排已 Deprecated**，清退为当前冲刺项；事件 `prepare_context` 与显式多源数据层已落地；**统一存储 PostgreSQL（ADR-019）**；**宽表 `panel_daily` + ADBC（ADR-020）**；**LLM 分层（ADR-021）**；**统计验证门控用法 + 进化分期（ADR-022）**——WF 硬闸、DSR/PBO 诊断、进化 L0–L3。总览见 [docs/architecture.md](docs/architecture.md)。

---

## 当前冲刺（进行中）

> **主线：策略研发 — 寻找收益率最佳策略**（动量 × 财务质量 × 牛熊门控）。
> 2026-08-22 战果：引擎 4 bug 修复（rebalance_freq 门控 / 创业板涨跌停 ±20% / PG 审计毒化自愈 / 并行重复 attach）+ regime 哑铃 DSL 落地 + 指数行情入库。训练集最优为哑铃 W120_512890（质量动量 + 沪深300×120日均线门控 + 熊市红利低波ETF：-27.86% → +30.08%），但测试集 Walk-Forward fold 0（2025Q1）-30.66%，被稳定性门拒绝、未合并——指数横盘期的风格崩盘，指数绝对 MA 门防不住。当前无通过 OOS 门的合并候选，`best_strategy.yaml` 未变更。
> 2026-08-23 工程修复：并行回测内存放大治理（SharedMemory→mmap IPC 文件，worker 私有面板拷贝从 ~3 份/worker 降到共享页缓存；网格峰值内存从 112GB 级回落）+ regime warmup 盲区回归修复；附带消除 pytest 单独跑 backtest 测试的 -1 退出码。
> 2026-08-23 性能优化（对标 NautilusTrader）：确定性事件 ID（时间戳派生 bar_trace_id 替代逐事件 uuid4，审计因果链贯穿）+ VisibilityGuard 窗口截断（O(T²)→O(T·W)）+ 因子全期预计算（O(T·U)，等价性由算子因果性证明背书，多 seed 测试守护）+ merged panel 跨 run Arrow 缓存（service 层开启，key 含 PG 数据版本水位，写事务原子自增自动失效）+ 审计批量写入（缓冲 500 条 executemany，查询前 flush 保 read-your-writes）。基准（5 标的 × 2 年小面板）：带审计端到端 2.27s→0.43s（-81%，审计开销 -98%）；大池下 O(T²) 消除收益随池规模放大。下一热点：`portfolio.update_market_values` 每 bar polars filter。
> 2026-08-30 数据正确性修复：基准指数行情（000300/000905/000001/399006）纳入正式下载管线（`DataIngestionService.INDEX_QUOTES` 显式点名 + 按交易日增量维护，随 `download_data.py` 每次运行自动补齐），消除 regime 门控 benchmark 数据过期的静默退化威胁；存量数据已真机补齐到 2026-08-28。
> 2026-08-30 引擎正确性修复：`run_walk_forward_parallel` test 折起点误用 `train_ts[0]`（test 回测覆盖训练期，OOS 指标污染，edff513）；此前所有折级 OOS 指标均受此影响，本轮合并门为首个干净窗口验证。
> 2026-08-30 策略研发轮次结果：relative/combined 网格（1 对照 + 18 组合，训练集内）冠军 `com_rw60_m0`（训练 +162.17%，夏普 1.495，回撤 -20.57%），但 OOS 合并门 **CONTINUE**——测试集三折全负（-26%~-30%/折，夏普 -2.1~-2.9），S1 稳定性门拒绝；归因：价格动量股票腿在 2025-2026 震荡市风格翻转（反复止损），指数级门控无法挽救选股层 alpha 反号。纯 relative 模式训练集即全负，已排除。`best_strategy.yaml` 未变更（现任基准 OOS mean sharpe 1.47，合并门槛极高）。
> 2026-08-30 数据层死循环治理：财务同步水位表落地（`financial_sync_watermark` + 双水位判定，46382d8）；启动同步与回测读路径（`financial/sync.py::is_financial_stale`）共享同一水位与 `FINANCIAL_RECHECK_DAYS=7` 常量（常量下沉至 backtest.data.financial.sync，遵循 AGENTS.md 6.2「同款判定共享同一水位」铁律）。沉默股票从每次同步全量重查（实测 4620 只 ≈ 20 分钟/次）降为每 7 天一次小窗检查；批次成功才推进水位（含合法 0 行），异常保留重试；PIT 对齐不受影响（行数据仍带真实 announce_date）。行情路径评估后不引入水位：日更域数据状态自愈，仅 ~12 只退市/停牌标的每次多查数秒，且水位会有损逐日精确补齐语义。
> 2026-08-30 全系统评审与修复（OCR delegate 模式，记录见 `docs/reviews/`）：后端 Critical 6 / High 17 / Medium 56 / Low 63，前端 High 3 / Medium 8 / Low 14。第一轮分支 `fix/review-critical-high` 关闭全部 Critical/High + 约 25 项 Medium/Low；**第二轮**关闭剩余 Medium/Low（止盈对称成交、风控 pre_trade、TLS/pg/算子/策略/API Origin/前端竞态等），ruff/lint-imports/pyright/pytest **1134** 全绿 + 前端 tsc 零错误。回测语义继续变化（止盈不按日内 high 白送；风控卖出走 pre_trade，强制清仓允许跌停价卖出）。详见 remediation「第二轮」。
> 次线：Web 前端开发（`web/`，React 18 + Vite + TypeScript + Tailwind + Radix UI + Recharts，对接 FastAPI `/api` 与 WebSocket；三页面骨架、OpenAPI 客户端、归因面板等已完成）。

- [ ] **regime relative/combined 通过 OOS 门**（`mode: relative`/`combined`）— 2026-08-30 轮次已收：combined 门控训练集显著占优但 OOS 全折崩溃（股票腿动量因子 OOS 反号，非门控问题）。下一轮方向：重设计股票腿（动量→基本面/反转混合，参考现任基准的净利增长选股），或放弃哑铃族转向基准增强
- [x] **ADR-022 §A 实施（统计验证门控）** — P0–P4 已落地（2026-08-30）：
  - **P0** 诊断契约：`dsr`/`pbo` 含 `status|passed|skipped|reason`；写回不再因 DSR 硬拒；PBO 缺料 `skipped`
  - **P1** PBO 迁入 ToG：`_oos_candidate_pairs` + `run_oos_gates`
  - **P2** 合并阈值：session `_current_best_oos` + `evaluate_merge_gate`（与 S1 串联为硬闸 `passed`）
  - **P3** 真日收益（equity→return）+ `_trial_fingerprints`/`N_eff` + DSR 可选 skew/kurt
  - **P4** 单测对齐（45 passed）；HTR 清退仍依赖本项且不得先于 P1（已满足）
- [ ] **HTR 遗留线清退（ADR-010 Deprecated / ADR-021 / ADR-022）**：PBO 已在 ToG；`cli`/`app` 迁 ResearchAgent 后删编排；白名单收紧。**迁移前冻结**
- [~] **Web 前端开发**（`web/`）— 次线，按需继续开发

---

## 观察项（需跟进）

- [~] **后台测试/冒烟 run 持续堆积**：持续有进程向共享 PG 写测试/冒烟回测 run（曾观测 dry-run 187 个无效 run 堆积），现均带 `test` 标签可被清理接口清除；建议排查写入源头进程，或定期使用看板清理按钮。
- [ ] **启动同步重复劳动（轻微）**：`_enrich_sectors_from_xtquant` 每次同步全量拉 THY1/DY1 板块映射（只回填空行，首轮后为 2 次 API 调用 + 空转）；`_is_price_stale`（`get_price_panel` 路径）被 ~12 只永久 stale 标的拖累反复进刷新分支（刷新增量，浪费有界）。均自限、非死循环。（2026-08-30 评估：行情路径不引入水位，见冲刺记录；板块回填空转留待后续）
- [ ] **并行 run_candidates 偶发 worker 失败**：哑铃网格阶段 2 中第 9/10 号任务（W250_511260 / W250_CASH）ERR——worker 内失败、无 RUN_START，疑似 worker 复用时环境性失败；单进程复现可跑通。影响面小（同窗结论不受影响），但动摇并行结果可信度，下轮大网格前值得排查。（2026-08-23：并行数据底座已从 SharedMemory 换为 mmap IPC 文件，Windows 句柄类环境性失败可能同源消除，待大网格复验；2026-08-30 大网格 19 任务两阶段未复现）

---

## Q4 — 不紧急不重要（明确搁置）

> 能力扩展或工程化愿望；前置未满足前不动。

### 能力扩展（门控）

- [ ] **AUDIT-P1-04** 行业集中度风控（ADR-013 P2）— **暂缓**（`instrument_details.industry` 已有板块回填，但引擎风控未贯通持仓行业暴露；覆盖率/质量门未建）
- [ ] **行业对比视角**（`stock_analysis`；可与 AUDIT-P1-04 同批联动）
- [ ] **多策略组合**
- [ ] **近实盘**：实时行情喂入引擎 `on_bar`
- [ ] **ADR-017 自我进化** — Deferred；技能规格见 ADR-017，**解锁节奏见 ADR-022 L0–L3**（当前至多 L0：S1 已多次拦截；尚无 ToG 路径测试集合并 → 未达 L1）

### 工程化与纵深防御

- [ ] **数据库引擎层第二阶段迁移**（SQLAlchemy Core，承接 ADR-019）：审计 `PostgresAuditProvider` / 记忆库 `substance.persistence` / 分析器 `app.analyzer` 迁移到 `core/db.py` 统一引擎层（第一阶段 DataCache 已完成，79142ba）；迁移时复用 read/write 上下文与 COPY 逃生舱模式，消解三处手工连接管理分叉
- [ ] **ADR-022 §A 残余（可选 hardening）**：跨 invoke 持久化 current best / 候选矩阵到盘；DSR 升硬性门控需单独 ADR 变更；网格指纹用真实渲染 YAML 替代 `grid:i` 近似
- [ ] **性能监控**：LLM Token + 回测耗时（`MonitoringService`）
- [ ] **配置中心化**：多环境 `config.yaml`
- [ ] **AUDIT-P3-01** `@pytest.mark.regression` 集中回归套件
- [ ] **AUDIT-P3-03** 部分成交（大单分批）— **暂不做**（2026-08 决定：大单分批暂不实现）
- [ ] **AUDIT-P3-04** 性能/压力（全 A、长周期、并发）
- [ ] **AUDIT-P3-05** 敏感信息脱敏（`password=` / `token=` / `api_key=`）— **暂不做**（2026-08 决定：非功能需求，暂不排期）
- [ ] **AUDIT-P3-06** telemetry 与审计集成
- [ ] **AUDIT-P3-07** miniqmt 内联常量清理
- [ ] **AUDIT-P3-09** 因果性切点扩展
