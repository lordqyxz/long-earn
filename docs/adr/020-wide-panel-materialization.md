---
id: 20
title: PostgreSQL 宽表物化与 ADBC 直读
status: Accepted
date: 2026-08
summary: panel_daily 宽表物化与 ADBC 零拷贝直读；失败时回退同源合并算法。
related: ["ADR-007", "ADR-013", "ADR-018", "ADR-019"]
---

# ADR-020: PostgreSQL 宽表物化与 ADBC 直读


## 背景

`get_merged_panel_as_polars`（合并面板：行情加 PIT 财务）旧路径每次运行均重算：经 psycopg 行协议拉取 price_daily 与财务五表 UNION，Python 侧 pandas `merge_asof` 季频展开与 `groupby.ffill`。500 只股票 × 4 年约 49 万行 × 32 列的面板，耗时达分钟级，且并行回测各 worker 重复劳动。

期间尝试的文件缓存（Arrow IPC，key 为 sha256(symbols, start, end)）在代码评审中暴露两个结构性缺陷：

1. **组合爆炸**：key 空间为 (symbols, start, end) 笛卡尔积，任意新参数组合均 miss；
2. **陈旧命中**：key 无数据版本指纹，底层数据更新后缓存不失效（后续以版本水位表修补，但引入额外计数器协议）。

张力在于：合并面板须跨运行复用、任意参数组合可命中、与底表强一致——文件缓存三者不可兼得。

## 决策

### A. panel_daily 物理宽表（手工增量物化视图）

在 PostgreSQL 内物化 `panel_daily`（主键 `(symbol, date)`）：price_daily 行情列加 PIT as-of 财务 24 字段（`PANEL_FINANCIAL_FIELDS` 单一事实源）。任意 (symbols, start, end) 查询均为该表的子集查询，组合爆炸从根上消除。

PIT 对齐以纯 SQL 实现（`fin_span` CTE）：财务有效期展开为 `[announce_date, 下一公告日)` 半开区间，K 线日期落入区间即取该期财报，与旧路径 `merge_asof backward` 逐位等价（等价性测试锁定）；`DISTINCT ON ... report_date DESC` 处理同公告日多报。

不采用 PostgreSQL 原生物化视图：`REFRESH MATERIALIZED VIEW` 为全量重算（`CONCURRENTLY` 亦然）且获取 ACCESS EXCLUSIVE 锁；物化视图无「哪部分脏了」的感知，只能整表刷新。本方案为 symbol 粒度增量的物化视图，是原生物化视图语义的超集。

### B. 更新协议：脏标记与惰性增量重建

| 路径 | 机制 |
|------|------|
| 写路径 | `save_prices` / `save_financials` 在同一写事务内写 `panel_dirty(symbol)`——数据与脏标记原子生效，无竞态窗口 |
| 读路径 | `ensure_panel_fresh()` 查脏标记 → 有脏行则在 `pg_advisory_xact_lock` 互斥下仅重建脏 symbol（DELETE + INSERT SELECT）→ 清脏标记 |
| 覆盖引导 | `panel_uncovered_symbols()` 发现新 symbol 缺口（行数与 price_daily 不一致）→ 增量 bootstrap |
| 批量路径 | `download_data.py` 全量下载完成 → `rebuild_panel_symbols(None)` 显式全量重建 |

advisory lock 与所有重建路径共用同一 lock ID，跨进程互斥；MVCC 下重建期间读者看到旧版本，读不阻塞。写 `holdernum` / `top10` 等非 panel 列集细表不触发脏标记。

### C. ADBC Arrow 直读

消费侧用 `adbc-driver-postgresql` 替代 psycopg 行协议：`fetch_arrow_table()` → `pl.from_arrow()` 零拷贝，数百万行传输显著提速。SQL 用 ADBC 原生 `$N` 占位符（非 psycopg `%s`）。

### D. 同源算法备用读路径契约

`read_wide_panel` 任一步失败或数据不足返回 `None`，`CompositeDataConnector` 切换至同库旧路径（pandas merge + ffill，含 miniqmt 增量下载）。数据充足性门控：price_daily 末端距请求 `end_date` 超过 10 日历日容忍（覆盖长假与更新滞后）视为缓存缺口，走旧路径补数。宽表路径本身不做下载。

与 ADR-018 的关系：018 禁止跨源静默换源；本条是同一 PostgreSQL Cache 上的两条读算法（宽表物化与即时 merge），失败时走另一条实现，非数据源切换。宽表侧仍保持「读不到已缓存数据即返回 None」，由调用方决定是否走旧路径。

### E. 删除文件缓存与版本水位

`panel_cache.py`（L3 Arrow IPC 文件缓存）、`enable_panel_cache` 配置、`storage.panel_cache_dir`、`data_version` 版本水位表全部删除——由脏标记协议整体取代。

## 后果

**正面**

- 读性能提升约一个数量级（500 股 × 4 年 49 万行：warm 约 2.7s，旧路径分钟级）；
- 任意参数组合可命中（单一物理表子集查询）；
- 无陈旧命中（脏标记与数据同事务原子提交）；
- 旧路径保留为同源算法备用读路径，宽表故障不阻塞回测。

**负面**

- 新增 `adbc-driver-postgresql` 依赖；
- PostgreSQL 存储增加约一倍 price 行宽（panel_daily 约等于 price_daily 行数 × 宽列）；
- 首次访问脏 symbol 有惰性重建延迟。

**中性**

- 所有写 price、财务五表的入口须同事务打脏标记，漏打将导致宽表陈旧（缓解：宽表与旧路径 PIT 逐位等价性测试与覆盖一致性检查双守护）；
- `_PANEL_REBUILD_LOCK_ID` 为 int64 全局约定值，不可复用为其他用途；
- `panel_dirty` 与 advisory lock 对外不可见，对外契约仅为「`read_wide_panel` 返回 None 表示调用方可走同源旧路径」。

## 参考

- ADR-007：PIT 语义（announce_date 可见起点）——宽表 `fin_span` CTE 的等价 SQL 实现。
- ADR-013：回测准确性——宽表输出与旧路径逐位等价性测试是该原则的具体落实。
