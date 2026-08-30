---
id: 19
title: 统一存储迁移至 PostgreSQL
status: Accepted
date: 2026-08
summary: 将审计、缓存与物质记忆三库由 DuckDB 迁移至 PostgreSQL；上层设计不变。
related: ["ADR-005", "ADR-007", "ADR-008"]
---

# ADR-019: 统一存储迁移至 PostgreSQL


## 背景

long-earn 早期以 DuckDB 嵌入式文件库承载三类存储，分布于 `<LONG_EARN_DATA_DIR>` 下：

| 用途 | 文件 | 规模 |
|------|------|------|
| 回测审计日志 | `audit.duckdb` | 约 512 万行 |
| 价格与财务缓存 | `backtest_cache.duckdb` | 价格约 1800 万行加 8 张财务表 |
| 物质记忆库 | `substances.duckdb` | 188 条 |

并行回测（ADR-008）中，各 worker 写入独立 `audit.duckdb` 临时库，主进程完成后合并回主审计库。DuckDB 对多进程并发写的支持有限（嵌入式文件锁与单写者限制），批量回测并行度越高，审计日志丢行或重复的概率越大，须替换为原生支持多写者的数据库。

候选方案比较：PostgreSQL 具备 MVCC 多写者原生支持、JSONB（契合审计 payload）、成熟事务与锁管理，本机 Docker Desktop 部署成本较低；MySQL 运维负担重；SQLite 与 DuckDB 同属单写者问题；时序库（TDengine、Influx）面向纯时序场景，与事件型审计表不匹配。

## 决策

我们将 long-earn 全部存储统一迁移至 PostgreSQL（本机 Docker 容器 `pg`，命名卷 `pgdata` 持久化），DuckDB 文件归档保留。

### A. 连接与配置

- 新增 `src/long_earn/core/pg.py`：`resolve_pg_params()`、`pg_conninfo()`、`pg_connect()` 统一裁决连接（环境变量 `PG_HOST`/`PG_PORT`/`PG_DB`/`PG_USER`/`PG_PASSWORD`，默认 `127.0.0.1:5432/long_earn/postgres/postgres`），全部接入点不再各自硬编码；
- `pg_connect` 支持 `read_only`（只读连接，防误写）、`row_factory`（dict_row 或元组行）、`autocommit`（高频只读查询不持有 MVCC 快照锁）；
- 部署：Docker Desktop 容器 `postgres:16`，`--restart unless-stopped`，命名卷 `pgdata` 挂载 `/var/lib/postgresql/data`。

### B. 各存储接入点（psycopg 3）

| 存储 | 实现 | PostgreSQL 表 |
|------|------|---------------|
| 审计 | `PostgresAuditProvider` | `backtest_audit.logs`（JSONB payload，主键 `(run_id, trace_id, seq)`） |
| 缓存 | `DataCache` | `price_daily`、8 张财务标量表、`universe_constituents`、`instrument_details` |
| 物质库 | `substance/persistence.py` | `substances`（JSONB keys/metadata） |

并行回测 worker（ADR-008）直写 PostgreSQL 审计表，MVCC 原生支持多写者，删除 worker 临时 DuckDB 文件与合并步骤。关键实现细节：psycopg3 占位符 `%s`、`INSERT ... ON CONFLICT ... DO UPDATE`、COPY 用 TEXT 格式（`write_row` 默认制表符分隔，不可声明 `FORMAT CSV`）、临时表用 `AS SELECT ... WITH NO DATA` 继承列类型。并发死锁治理：`DataCache._get_conn()` 使用 `autocommit=True` 连接，DDL 不再被未提交读事务阻塞。只读消费侧（`BacktestAnalyzer`、`app/analyzer.py`、`audit.py` 查询）统一 `row_factory=None`（元组行），保持 DuckDB 时代 `fetchall` 契约。

### C. 迁移

- `scripts/migrate_duckdb_to_postgres.py`：价格、财务、审计全量迁移（分页 `ORDER BY` 全列确定性，COPY 批量灌入，行数与源精确匹配）；
- `scripts/migrate_substances_to_postgres.py`：物质库独立迁移（`save_many` 幂等 UPSERT）；
- 旧 DuckDB 文件迁移完成后已删除（PostgreSQL 为唯一权威存储；迁移脚本保留，可供历史数据重新迁移）。

## 后果

**正面**

- 多写者并发安全，并行回测审计不再丢行；
- JSONB 便于审计查询与聚合；
- 统一连接与事务语义；与 Web 仪表盘（`app/analyzer.py`）同库读取。

**负面**

- 须常驻 PostgreSQL 服务（Docker 容器，重启策略保证）；
- psycopg 无 `.pl()`/`fetchdf()`，查询结果须手工转 DataFrame（`_rows_to_pl` / `_fetchdf` helper）。

**中性**

- `core` 不依赖上层契约保持；
- 存储路径字段（`memory_path`、`backtest_cache_path`、`backtest_audit_path`）降级为兼容占位，不再作为运行时存储位置。

## 参考

- 迁移脚本：`scripts/migrate_duckdb_to_postgres.py`、`scripts/migrate_substances_to_postgres.py`。
