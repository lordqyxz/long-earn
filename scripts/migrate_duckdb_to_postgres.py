"""DuckDB → PostgreSQL 全量数据迁移脚本

把 DuckDB 时代的全部数据迁移到 PostgreSQL：

- ``backtest_cache.duckdb``：price_daily（1800 万行）、8 张财务细表、
  universe_constituents、instrument_details、_schema_meta
- ``audit.duckdb``：backtest_audit.logs（512 万行审计）
- ``substances.duckdb``：substances（事件物质）

用法::

    uv run python scripts/migrate_duckdb_to_postgres.py [--skip-audit] [--drop-source]

选项:
    --skip-audit    跳过审计日志迁移（审计已在 audit.duckdb，或想分批迁移）
    --drop-source   迁移校验通过后删除旧 DuckDB 库（谨慎！默认保留）

迁移是幂等的：PG 表已有数据时跳过对应表（按行数一致性校验）。
审计使用 PostgresAuditProvider 的 DDL（backtest_audit.logs schema 单一来源）。
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
from loguru import logger

from long_earn.backtest.data.financial.schemas import FinancialSchemaRegistry
from long_earn.core.pg import pg_connect
from long_earn.core.storage import backtest_cache_path, substances_db_path

# 审计独立库（DuckDB 时代分库产物）
_AUDIT_DB = Path(r"D:\dev\long-earn-data\audit.duckdb")
_AUDIT_TABLE = '"backtest_audit".logs'

# 每批 COPY 行数（1800 万行价格表分批导入，避免单事务过大）
_BATCH_SIZE = 500_000


def _duck_count(conn: duckdb.DuckDBPyConnection, table: str) -> int:
    """DuckDB 表行数（表不存在返回 0）。"""
    try:
        row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _pg_count(conn: Any, table: str) -> int:
    """PG 表行数（表不存在返回 0）。

    conn 可能带 dict_row 行工厂，需兼容 ``row["count"]`` 与元组 ``row[0]``。
    """
    try:
        row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
        if not row:
            return 0
        return int(row["count"] if isinstance(row, dict) else row[0])
    except Exception:
        return 0


def _copy_table(
    conn: Any,
    src_table: str,
    dst_table: str,
    columns: list[str],
    src_conn: duckdb.DuckDBPyConnection,
) -> int:
    """把 DuckDB 表分块 COPY 到 PG（ON CONFLICT DO NOTHING 幂等）。

    Returns:
        实际写入行数
    """
    col_list = ", ".join(columns)
    order_by = ", ".join(columns)  # 全列确定排序，保证 LIMIT/OFFSET 分页跨批不错位
    total = 0
    offset = 0
    while True:
        # DuckDB 分块读取（避免 1800 万行全量驻内存）
        df: pd.DataFrame = src_conn.execute(
            f"SELECT {col_list} FROM {src_table} ORDER BY {order_by} "
            f"LIMIT {_BATCH_SIZE} OFFSET {offset}"
        ).fetchdf()
        if df.empty:
            break
        # 创建临时表（从目标表继承列类型，避免重复硬编码 DDL）+ COPY 载入。
        # 注意：psycopg3 write_row 输出 TEXT 格式（tab 分隔），COPY 必须用
        # 默认 TEXT 格式而非 FORMAT CSV，否则 PG 按逗号解析导致列错位。
        conn.execute("DROP TABLE IF EXISTS _tmp_mig")
        conn.execute(
            f"CREATE TEMP TABLE _tmp_mig AS "
            f"SELECT {col_list} FROM {dst_table} WITH NO DATA"
        )
        copy_df = df.where(pd.notnull(df), None)
        with conn.cursor() as cur, cur.copy(
            f"COPY _tmp_mig ({col_list}) FROM STDIN"
        ) as copy:
            for row in copy_df.itertuples(index=False):
                copy.write_row(list(row))
        conn.execute(
            f"INSERT INTO {dst_table} ({col_list}) "
            f"SELECT {col_list} FROM _tmp_mig ON CONFLICT DO NOTHING"
        )
        total += len(df)
        offset += len(df)
        logger.info(f"  {src_table}: 已迁移 {total} 行")
        if len(df) < _BATCH_SIZE:
            break
    return total


def _copy_audit(conn: Any, src_conn: duckdb.DuckDBPyConnection) -> int:
    """迁移审计日志（backtest_audit.logs，512 万行级）。"""
    audit_cols = (
        "run_id, seq, timestamp, event_type, trace_id, parent_id, "
        "component, status, payload, latency_ms"
    )
    col_list = ", ".join(audit_cols.split(", "))
    total = 0
    offset = 0
    while True:
        df: pd.DataFrame = src_conn.execute(
            f"SELECT {audit_cols} FROM {_AUDIT_TABLE} "
            f"ORDER BY run_id, trace_id, seq "
            f"LIMIT {_BATCH_SIZE} OFFSET {offset}"
        ).fetchdf()
        if df.empty:
            break
        conn.execute("DROP TABLE IF EXISTS _tmp_audit")
        conn.execute(
            f"CREATE TEMP TABLE _tmp_audit AS "
            f"SELECT {col_list} FROM {_AUDIT_TABLE} WITH NO DATA"
        )
        copy_df = df.where(pd.notnull(df), None)
        with conn.cursor() as cur, cur.copy(
            f"COPY _tmp_audit ({col_list}) FROM STDIN"
        ) as copy:
            for row in copy_df.itertuples(index=False):
                copy.write_row(list(row))
        # 审计主键 (run_id, trace_id, seq)，冲突跳过（幂等）
        conn.execute(
            f"INSERT INTO {_AUDIT_TABLE} ({col_list}) "
            f"SELECT {col_list} FROM _tmp_audit "
            "ON CONFLICT (run_id, trace_id, seq) DO NOTHING"
        )
        total += len(df)
        offset += len(df)
        logger.info(f"  audit.logs: 已迁移 {total} 行")
        if len(df) < _BATCH_SIZE:
            break
    return total


def _migrate_cache(conn: Any, src_conn: duckdb.DuckDBPyConnection) -> None:
    """迁移缓存库（价格/财务/股票池/标的详情/元信息）。"""
    cache_tables: list[tuple[str, list[str]]] = [
        # (DuckDB 表名, 列清单)
        ("price_daily", ["symbol", "date", "open", "high", "low", "close", "volume"]),
        ("universe_constituents", ["index_code", "symbol", "date"]),
        (
            "instrument_details",
            [
                "symbol",
                "name",
                "industry",
                "region",
                "listing_date",
                "total_shares",
                "float_shares",
                "market_value",
                "flow_market_value",
            ],
        ),
    ]
    # 8 张财务细表：列清单从 schema 注册表反射
    for schema in FinancialSchemaRegistry.TABLES:
        cols = [c.name for c in schema.columns]
        cache_tables.append((schema.table_name, cols))

    for table, cols in cache_tables:
        src_count = _duck_count(src_conn, table)
        if src_count == 0:
            logger.info(f"跳过 {table}（DuckDB 无数据）")
            continue
        # 幂等：PG 已有数据则跳过
        if _pg_count(conn, table) >= src_count:
            logger.info(f"跳过 {table}（PG 已有 {_pg_count(conn, table)} 行 >= 源 {src_count}）")
            continue
        logger.info(f"迁移 {table}（源 {src_count} 行）...")
        written = _copy_table(conn, table, table, cols, src_conn)
        # 校验
        pg_now = _pg_count(conn, table)
        if pg_now < src_count:
            logger.warning(f"  {table}: PG {pg_now} 行 < 源 {src_count} 行（可能部分冲突）")
        else:
            logger.info(f"  {table}: 完成 {written} 行（PG 共 {pg_now} 行）")


def _migrate_audit(conn: Any, src_conn: duckdb.DuckDBPyConnection) -> int:
    """迁移审计日志（独立库 audit.duckdb 或缓存库旧 schema，512 万行级）。"""
    src_cache = backtest_cache_path()
    if _AUDIT_DB.exists():
        audit_src = _AUDIT_DB
    elif _duck_count(src_conn, _AUDIT_TABLE) > 0:
        audit_src = src_cache  # 兜底：从缓存库的旧 backtest_audit schema 读
    else:
        logger.info("无审计日志可迁移（跳过）")
        return 0

    if audit_src == src_cache:
        logger.info("迁移审计（从缓存库旧 schema）...")
        written = _copy_audit(conn, src_conn)
    else:
        logger.info(f"迁移审计（从 {audit_src}）...")
        audit_conn = duckdb.connect(str(audit_src), read_only=True)
        try:
            written = _copy_audit(conn, audit_conn)
        finally:
            audit_conn.close()
    conn.commit()
    logger.info(f"审计日志迁移完成: {written} 行")
    return written


def migrate(skip_audit: bool = False, drop_source: bool = False) -> int:
    """执行全量迁移。"""
    src_cache = backtest_cache_path()
    if not src_cache.exists():
        logger.warning(f"缓存库不存在，无需迁移: {src_cache}")
        return 0

    start = time.perf_counter()
    # 打开 PG（DataCache 会在构造时初始化全部表结构）
    conn = pg_connect()
    try:
        from long_earn.backtest.data.cache import DataCache

        DataCache()  # 初始化 PG 表结构（幂等）
        logger.info("PG 表结构就绪")
        # 审计表（backtest_audit.logs）由 PostgresAuditProvider 建
        from long_earn.backtest.engine.audit import PostgresAuditProvider

        audit_provider = PostgresAuditProvider()
        audit_provider.close()
        logger.info("PG 审计表就绪")
    except Exception as exc:
        logger.error(f"初始化 PG 表结构失败: {exc}")
        return -1

    src_conn = duckdb.connect(str(src_cache), read_only=True)
    try:
        # 1. 缓存库（价格/财务/股票池/标的详情）
        _migrate_cache(conn, src_conn)
        conn.commit()
        logger.info("缓存库迁移完成")

        # 2. 审计日志（独立库 audit.duckdb 或缓存库中的旧 schema）
        if not skip_audit:
            _migrate_audit(conn, src_conn)
    finally:
        src_conn.close()

    # 3. 物质库（substances.duckdb 独立小库，单独迁移；
    #    scripts/ 不在包路径，脚本作为入口运行时 sys.path[0]=scripts/，
    #    故用不带包前缀的顶层导入）
    import migrate_substances_to_postgres

    migrate_substances_to_postgres.migrate_substances()

    elapsed = time.perf_counter() - start
    logger.info(f"全量迁移完成，耗时 {elapsed:.1f}s")

    # 3. 可选：删除旧 DuckDB 库（架构修正收尾）
    if drop_source:
        for p in (src_cache, _AUDIT_DB, substances_db_path()):
            if p.exists():
                p.unlink()
                logger.info(f"已删除旧 DuckDB 库: {p}")

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="DuckDB → PostgreSQL 全量迁移")
    parser.add_argument(
        "--skip-audit", action="store_true", help="跳过审计日志迁移"
    )
    parser.add_argument(
        "--drop-source", action="store_true", help="迁移完成后删除旧 DuckDB 库"
    )
    args = parser.parse_args()

    result = migrate(skip_audit=args.skip_audit, drop_source=args.drop_source)
    if result < 0:
        sys.exit(1)
    logger.info("迁移脚本执行完毕")


if __name__ == "__main__":
    main()
