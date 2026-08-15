"""DuckDB → PostgreSQL 物质库迁移脚本。

把 ``<数据目录>/substances.duckdb`` 中的物质（substances 表）迁移到
PostgreSQL 的 ``substances`` 表。

为什么独立于 migrate_duckdb_to_postgres.py：主迁移脚本聚焦审计+价格/财务
缓存（体积大），物质库是独立小库（KB~MB 级），单独跑避免主脚本耦合；
且物质库日常由 ``save_many`` 幂等 UPSERT 维护，本脚本可重复执行不产生重复。

用法::

    uv run python scripts/migrate_substances_to_postgres.py [--source PATH]

``--source`` 可选：显式指定源库路径。默认按 `<数据目录>/backup/substances.duckdb`
（归档优先）→ `<数据目录>/substances.duckdb` 顺序查找。
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import duckdb
from loguru import logger

from long_earn.core.storage import substances_db_path
from long_earn.substance.model import FilterLogic, Substance, SubstanceForm
from long_earn.substance.persistence import count_substances, save_many

# duckdb 列 → Substance 字段 的类型转换辅助


def _as_list(value: object) -> list[str]:
    """duckdb LIST 列 → list[str]（duckdb 返回 python list）。"""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return []


def _as_datetime(value: object) -> datetime | None:
    """duckdb TIMESTAMP 列 → datetime | None。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _as_dict(value: object) -> dict:
    """duckdb JSON/STRUCT 列 → dict。"""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        import json

        try:
            data = json.loads(value)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}
    return dict(value or {})


def _substance_from_row(row: tuple) -> Substance:
    """把 substances.duckdb 的一行转成 Substance（字段顺序对齐表结构）。"""
    (
        sid,
        form_raw,
        content,
        keys,
        filter_keys,
        filter_logic,
        created_at,
        visible_from,
        expires_at,
        source,
        confidence,
        source_id,
        target_id,
        relation_type,
        conflict_group,
        insertion_order,
        decay_half_life_days,
        metadata,
    ) = row[:18]
    try:
        form = SubstanceForm(form_raw)
    except ValueError:
        form = SubstanceForm.KNOWLEDGE
    try:
        fl = FilterLogic(filter_logic) if filter_logic else FilterLogic.AND_ANY
    except ValueError:
        fl = FilterLogic.AND_ANY
    return Substance(
        sid=sid,
        form=form,
        content=content or "",
        keys=_as_list(keys),
        filter_keys=_as_list(filter_keys),
        filter_logic=fl,
        created_at=_as_datetime(created_at) or datetime.now(),
        visible_from=_as_datetime(visible_from),
        expires_at=_as_datetime(expires_at),
        source=source or "manual",
        confidence=float(confidence) if confidence is not None else 1.0,
        source_id=source_id,
        target_id=target_id,
        relation_type=relation_type,
        conflict_group=conflict_group,
        insertion_order=int(insertion_order) if insertion_order is not None else 0,
        decay_half_life_days=float(decay_half_life_days)
        if decay_half_life_days is not None
        else 90.0,
        metadata=_as_dict(metadata),
    )


def _default_source() -> Path:
    """定位源库：优先 backup/ 归档（迁移完成后默认路径已无文件），
    其次默认存储路径（迁移前 / 未归档场景）。"""
    default = substances_db_path()
    backup = default.parent / "backup" / default.name
    if backup.exists():
        return backup
    return default


def migrate_substances(
    src: str | Path | None = None,
) -> int:
    """迁移物质库：duckdb → PostgreSQL。返回成功迁移条数。"""
    src_path = Path(src) if src else _default_source()
    if not src_path.exists():
        logger.error(f"源物质库不存在: {src_path}")
        return -1

    src_conn = duckdb.connect(str(src_path), read_only=True)
    try:
        try:
            rows = src_conn.execute("SELECT * FROM substances").fetchall()
        except Exception as exc:
            logger.error(f"读取 substances.duckdb 失败: {exc}")
            return -1
        logger.info(f"源物质库读取: {len(rows)} 条")
        if not rows:
            logger.info("源物质库为空，无需迁移")
            return 0

        substances = [_substance_from_row(r) for r in rows]
        # 校验：过滤掉 sid 缺失的异常行
        valid = [s for s in substances if s.sid]
        if len(valid) != len(substances):
            logger.warning(f"跳过 {len(substances) - len(valid)} 条无 sid 的异常行")

        # 幂等 UPSERT 写入 PG（save_many 单事务，失败回滚）
        written = save_many(valid)
        pg_count = count_substances()
        logger.info(
            f"物质迁移完成: 写入 {written} 条, PG 物质总数 {pg_count}"
        )
        return written
    finally:
        src_conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="DuckDB → PostgreSQL 物质库迁移")
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help="源 substances.duckdb 路径（默认：数据目录 backup/ 归档优先，其次默认存储路径）",
    )
    args = parser.parse_args()

    result = migrate_substances(src=args.source)
    if result < 0:
        sys.exit(1)
    logger.info("物质迁移脚本执行完毕")


if __name__ == "__main__":
    main()
