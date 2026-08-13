"""审计日志分库迁移脚本

把 ``backtest_cache.duckdb`` 中的 ``backtest_audit.logs``（512 万行级）
整体迁移到独立审计库 ``backtest_audit.duckdb``，实现审计与价格缓存分库
（消除 Web 只读连接与回测写连接在同一文件上的锁竞争）。

用法::

    uv run python scripts/migrate_audit_to_separate_db.py [--drop-source]

默认只复制 + 校验行数，不动旧库；``--drop-source`` 在迁移校验通过后
删除旧库中的 ``backtest_audit`` schema（架构修正的明确必要理由，
AGENTS.md 缓存保护约定的例外）。

迁移是幂等的：新库已存在且行数一致时直接跳过复制。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
from loguru import logger

from long_earn.backtest.engine.audit import DuckDBAuditProvider
from long_earn.core.storage import backtest_audit_path, backtest_cache_path

_AUDIT_TABLE = '"backtest_audit".logs'


def _count(conn: duckdb.DuckDBPyConnection, table: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
    return int(row[0]) if row else 0


def migrate(drop_source: bool = False) -> int:
    src = backtest_cache_path()
    dst = backtest_audit_path()
    if not src.exists():
        logger.warning(f"缓存库不存在，无需迁移: {src}")
        return 0

    # 1. 初始化独立审计库（DDL 单一来源：DuckDBAuditProvider）
    boot = DuckDBAuditProvider(db_path=dst)
    boot.close()

    dst_conn = duckdb.connect(str(dst), read_only=False)
    try:
        dst_count = _count(dst_conn, _AUDIT_TABLE)
        if dst_count > 0:
            logger.info(f"审计库已有 {dst_count} 条记录，跳过复制（幂等）")
            return dst_count

        # 2. 只读 ATTACH 旧库，跨库整体搬运。
        # 显式列名映射：旧库 seq 列可能是后加列（ALTER TABLE ADD COLUMN 追加
        # 到表尾），SELECT * 按位置对应会类型错位（TIMESTAMP → BIGINT）。
        _COLS = (
            "run_id, seq, timestamp, event_type, trace_id, parent_id, "
            "component, status, payload, latency_ms"
        )
        esc_src = str(src).replace("'", "''")
        dst_conn.execute(f"ATTACH '{esc_src}' AS old (READ_ONLY)")
        try:
            src_count = _count(dst_conn, "old." + _AUDIT_TABLE)
            logger.info(f"旧库审计记录数: {src_count}")
            if src_count == 0:
                logger.info("旧库无审计记录，无需迁移")
                return 0
            dst_conn.execute(
                f"INSERT INTO {_AUDIT_TABLE} ({_COLS}) "
                f"SELECT {_COLS} FROM old.{_AUDIT_TABLE}"
            )
        finally:
            dst_conn.execute("DETACH old")

        # 3. 校验行数一致
        dst_count = _count(dst_conn, _AUDIT_TABLE)
        if dst_count != src_count:
            logger.error(
                f"迁移校验失败: 源 {src_count} 条 vs 目标 {dst_count} 条，请排查"
            )
            return -1
        logger.info(f"迁移完成: {dst_count} 条审计记录 → {dst}")
    finally:
        dst_conn.close()

    # 4. 可选：删除旧库 audit schema（架构修正收尾）
    if drop_source:
        conn = duckdb.connect(str(src), read_only=False)
        try:
            conn.execute('DROP SCHEMA IF EXISTS "backtest_audit" CASCADE')
            logger.info(f"已从旧缓存库删除 backtest_audit schema: {src}")
        finally:
            conn.close()

    return dst_count


def main() -> None:
    parser = argparse.ArgumentParser(description="审计日志分库迁移")
    parser.add_argument(
        "--drop-source",
        action="store_true",
        help="迁移校验通过后删除旧库中的 backtest_audit schema",
    )
    args = parser.parse_args()

    result = migrate(drop_source=args.drop_source)
    if result < 0:
        sys.exit(1)
    logger.info(f"迁移完成，审计库记录数: {result}")


if __name__ == "__main__":
    main()
