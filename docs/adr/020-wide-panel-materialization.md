# ADR-020: PG 宽表物化 + ADBC 直读替代 panel 文件缓存

日期: 2026-08
状态: Accepted

## 背景

`get_merged_panel_as_polars`（合并面板：行情 + PIT 财务）旧路径每次运行都重算：psycopg 行协议拉取 price_daily + 财务五表 UNION，Python 侧 pandas `merge_asof` 季频展开 + `groupby.ffill`。500 只股票 × 4 年 ≈ 49 万行 × 32 列的面板，分钟级耗时，且并行回测每个 worker 重复劳动。

期间尝试的**文件缓存**（Arrow IPC，key = sha256(symbols, start, end)）在代码评审中暴露两个结构性缺陷：

1. **组合爆炸**：key 空间是 (symbols, start, end) 笛卡尔积，任意新参数组合都 miss；
2. **陈旧命中**：key 无数据版本指纹，底层数据更新后缓存不失效（评审 H1；后续以版本水位表修补，但引入额外计数器协议）。

张力：合并面板既要跨运行复用、又要任意参数组合命中、又要与底表强一致——文件缓存三者不可兼得。

## 决策

### A. panel_daily 物理宽表（手工增量物化视图）

在 PG 内物化 `panel_daily`（PK `(symbol, date)`）：price_daily 行情列 + PIT as-of 财务 24 字段（`PANEL_FINANCIAL_FIELDS` 单一事实源）。任意 (symbols, start, end) 查询都是该表的**子集查询**，组合爆炸从根上消失。

PIT 对齐以纯 SQL 实现（`fin_span` CTE）：财务有效期展开为 `[announce_date, 下一公告日)` 半开区间，K 线日期落入区间即取该期财报，与旧路径 `merge_asof backward` 逐位等价（等价性测试锁定）；`DISTINCT ON ... report_date DESC` 处理同公告日多报。

**不用 PG 原生物化视图**：`REFRESH MATERIALIZED VIEW` 是全量重算（`CONCURRENTLY` 亦然）且拿 ACCESS EXCLUSIVE 锁；物化视图无"哪部分脏了"的感知，只能整表刷。本方案本质是 symbol 粒度增量的物化视图，是原生物化视图语义的超集。

### B. 更新协议：脏标记 + 惰性增量重建

| 路径 | 机制 |
|------|------|
| 写路径 | `save_prices` / `save_financials` **同一写事务**内写 `panel_dirty(symbol)` —— 数据与脏标记原子生效，无竞态窗口 |
| 读路径 | `ensure_panel_fresh()` 查脏标记 → 有脏行则 `pg_advisory_xact_lock` 互斥下**只重建脏 symbol**（DELETE + INSERT SELECT）→ 清脏标记 |
| 覆盖引导 | `panel_uncovered_symbols()` 发现新 symbol 缺口（行数与 price_daily 不一致）→ 增量 bootstrap |
| 批量路径 | `download_data.py` 全量下载完成 → `rebuild_panel_symbols(None)` 显式全量重建 |

advisory lock 与所有重建路径共用同一 lock ID，跨进程互斥；MVCC 下重建期间读者看到旧版本，读不阻塞。写 `holdernum` / `top10` 等非 panel 列集细表不触发脏标记。

### C. ADBC Arrow 直读

消费侧用 `adbc-driver-postgresql` 替代 psycopg 行协议：`fetch_arrow_table()` → `pl.from_arrow()` 零拷贝，数百万行传输显著提速（二进制批量协议 vs 逐行物化 Python 对象）。SQL 用 ADBC 原生 `$N` 占位符（非 psycopg `%s`）。

### D. 降级契约（宽表只读已缓存数据）

`read_wide_panel` 任何一步失败 / 数据不足返回 `None`，`CompositeDataConnector` 回退旧路径（pandas merge + ffill，含 miniqmt 增量下载）。数据充足性门控：price_daily 末端距请求 `end_date` 超过 10 日历日容忍（覆盖长假 + 更新滞后）视为缓存缺口，回退旧路径补数。宽表路径本身**不做下载**，保持"失败即失败"叙事（ADR-018）。

### E. 删除文件缓存与版本水位

`panel_cache.py`（L3 Arrow IPC 文件缓存）、`enable_panel_cache` 配置、`storage.panel_cache_dir`、`data_version` 版本水位表全部删除——被脏标记协议整体取代。

## 后果

- **收益**：读性能提升一个数量级（500 股 × 4 年 49 万行：warm 2.7s vs 旧路径分钟级）；任意参数组合命中（单一物理表子集查询）；无陈旧命中（脏标记与数据同事务原子提交）；旧路径保留为降级路径，宽表故障不阻塞回测。
- **代价**：新增 `adbc-driver-postgresql` 依赖；PG 存储增加约一倍 price 行宽（panel_daily ≈ price_daily 行数 × 宽列）；首次访问脏 symbol 有惰性重建延迟。
- **风险与约束**：所有写 price / 财务五表的入口**必须**同事务打脏标记，漏打 → 宽表陈旧（缓解：宽表与旧路径 PIT 逐位等价性测试 + 覆盖一致性检查双守护）；`_PANEL_REBUILD_LOCK_ID` 是 int64 全局约定值，不可复用为其他用途。
- **中性**：`panel_dirty` / advisory lock 对外不可见，对外契约仅 "`read_wide_panel` 返回 None = 调用方回退"。

## 参考

- ADR-007：PIT 语义（announce_date 可见起点）—— 宽表 `fin_span` CTE 的等价 SQL 实现。
- ADR-013：回测准确性 —— 宽表输出与旧路径逐位等价性测试是该原则在此的落地。
- ADR-019：PG 统一存储 —— 本决策在其上新增物化层。
