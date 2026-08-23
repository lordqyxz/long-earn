# TODO — 待办清单

> 最后更新：2026-08-23
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
> 次线：Web 前端开发（`web/`，React 18 + Vite + TypeScript + Tailwind + Radix UI + Recharts，对接 FastAPI `/api` 与 WebSocket；三页面骨架、OpenAPI 客户端、归因面板等已完成）。

- [ ] **regime relative/combined 通过 OOS 门**（`mode: relative`/`combined`）— 开发已落地（relative/combined 模式 + warmup 盲区回归修复已沉淀进上"当前冲刺"说明）；剩余任务：训练集内迭代，通过 OOS 门产出合并候选
- [ ] **指数行情纳入正式下载管线**（`download_data.py`）— regime benchmark（000300/000905/000001/399006，2015-01-05 起经临时脚本入库）无增量维护；数据过期会使门控静默退化为永远牛市（数据正确性威胁）
- [~] **Web 前端开发**（`web/`）— 次线，按需继续开发

---

## 观察项（需跟进）

- [~] **后台测试/冒烟 run 持续堆积**：持续有进程向共享 PG 写测试/冒烟回测 run（曾观测 dry-run 187 个无效 run 堆积），现均带 `test` 标签可被清理接口清除；建议排查写入源头进程，或定期使用看板清理按钮。
- [ ] **并行 run_candidates 偶发 worker 失败**：哑铃网格阶段 2 中第 9/10 号任务（W250_511260 / W250_CASH）ERR——worker 内失败、无 RUN_START，疑似 worker 复用时环境性失败；单进程复现可跑通。影响面小（同窗结论不受影响），但动摇并行结果可信度，下轮大网格前值得排查。（2026-08-23：并行数据底座已从 SharedMemory 换为 mmap IPC 文件，Windows 句柄类环境性失败可能同源消除，待大网格复验）

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
