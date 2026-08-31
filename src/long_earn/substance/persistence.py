"""持久化层 — PostgreSQL 事务式存储（替代 DuckDB/JSONL 全量重写）。

ADR-007 Phase 4：原子写入 + 事务保证 + 列式检索。
- 单条 ``INSERT ... ON CONFLICT`` O(log n)，不再每次 add 全量重写整个文件；
- PostgreSQL WAL 保证崩溃安全，杜绝 JSONL 截断到一半的数据丢失；
- ``meta.json`` 由 ``SELECT COUNT(*)`` 派生，消灭元数据与数据脱钩问题；
- 索引（TF-IDF / Graph）仍从 PostgreSQL 全量重建，保持内存热存储不变。

向后兼容：``load_jsonl`` / ``save_jsonl`` 保留为迁移入口，主路径改为 PostgreSQL。
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy.engine import Connection

from long_earn.core.db import exec_sql, read_connection, write_transaction
from long_earn.substance.model import FilterLogic, ReviewStatus, Substance

SCHEMA_VERSION = 4

# ── DDL ────────────────────────────────────────────────────────────
# 列式存储结构化字段，keys/filter_keys/metadata 用 JSONB 列承载任意结构。
# 主键 sid 保证幂等 UPSERT（ON CONFLICT DO UPDATE），杜绝重复追加。
_DDL = """
CREATE TABLE IF NOT EXISTS substances (
    sid              VARCHAR PRIMARY KEY,
    form             VARCHAR NOT NULL,
    content          VARCHAR,
    keys             JSONB,
    filter_keys      JSONB,
    filter_logic     VARCHAR,
    created_at       TIMESTAMP,
    visible_from     TIMESTAMP,
    expires_at       TIMESTAMP,
    source           VARCHAR,
    confidence       DOUBLE PRECISION,
    source_id        VARCHAR,
    target_id        VARCHAR,
    relation_type    VARCHAR,
    conflict_group   VARCHAR,
    insertion_order  INTEGER,
    decay_half_life_days DOUBLE PRECISION,
    review_status    VARCHAR,
    metadata         JSONB
);
"""

_ALTER_REVIEW_STATUS = (
    "ALTER TABLE substances ADD COLUMN IF NOT EXISTS review_status VARCHAR;"
)
_INDEX_VISIBLE = (
    "CREATE INDEX IF NOT EXISTS idx_substances_visible_from "
    "ON substances(visible_from);"
)
_INDEX_FORM = "CREATE INDEX IF NOT EXISTS idx_substances_form ON substances(form);"
_INDEX_CREATED = (
    "CREATE INDEX IF NOT EXISTS idx_substances_created ON substances(created_at);"
)

# UPSERT：主键 sid 冲突时整行覆盖（save_substance / save_many 均为幂等覆盖）
_UPSERT_SQL = """
INSERT INTO substances (
    sid, form, content, keys, filter_keys, filter_logic, created_at,
    visible_from, expires_at, source, confidence, source_id, target_id,
    relation_type, conflict_group, insertion_order, decay_half_life_days,
    review_status, metadata
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
)
ON CONFLICT (sid) DO UPDATE SET
    form = EXCLUDED.form,
    content = EXCLUDED.content,
    keys = EXCLUDED.keys,
    filter_keys = EXCLUDED.filter_keys,
    filter_logic = EXCLUDED.filter_logic,
    created_at = EXCLUDED.created_at,
    visible_from = EXCLUDED.visible_from,
    expires_at = EXCLUDED.expires_at,
    source = EXCLUDED.source,
    confidence = EXCLUDED.confidence,
    source_id = EXCLUDED.source_id,
    target_id = EXCLUDED.target_id,
    relation_type = EXCLUDED.relation_type,
    conflict_group = EXCLUDED.conflict_group,
    insertion_order = EXCLUDED.insertion_order,
    decay_half_life_days = EXCLUDED.decay_half_life_days,
    review_status = EXCLUDED.review_status,
    metadata = EXCLUDED.metadata
"""


_schema_lock = threading.Lock()
_schema_state = {"ready": False}


def _ensure_schema(conn: Connection) -> None:
    """幂等建表与建索引（PostgreSQL 方言，可重复调用）。"""
    exec_sql(conn, _DDL)
    exec_sql(conn, _ALTER_REVIEW_STATUS)
    exec_sql(conn, _INDEX_VISIBLE)
    exec_sql(conn, _INDEX_FORM)
    exec_sql(conn, _INDEX_CREATED)


def _ensure_schema_once() -> None:
    """进程内只跑一次 DDL（幂等）；写路径与读路径共用。"""
    if _schema_state["ready"]:
        return
    with _schema_lock:
        if _schema_state["ready"]:
            return
        with write_transaction() as conn:
            _ensure_schema(conn)
        _schema_state["ready"] = True


def _as_dict(value: Any) -> dict[str, Any]:
    """把 JSONB 列值统一转 dict（psycopg 自动反序列化，兼容 str 旧数据）。"""
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}
    return dict(value or {})


def _as_list(value: Any) -> list[str]:
    """把 JSONB 列值统一转 list[str]（psycopg 自动反序列化，兼容 str 旧数据）。"""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            data = json.loads(value)
        except json.JSONDecodeError:
            return []
        return data if isinstance(data, list) else []
    return []


def _parse_review_status(value: Any) -> ReviewStatus:
    """旧行无列或空值视为 committed，避免存量知识从激活中消失。"""
    if value is None or value == "":
        return ReviewStatus.COMMITTED
    try:
        return ReviewStatus(str(value))
    except ValueError:
        return ReviewStatus.COMMITTED


def _row_to_substance(row: Any) -> Substance:
    """把 PostgreSQL 查询行转回 Substance（Pydantic 反序列化）。"""
    return Substance(
        sid=row[0],
        form=row[1],
        content=row[2] or "",
        keys=_as_list(row[3]),
        filter_keys=_as_list(row[4]),
        filter_logic=FilterLogic(row[5]) if row[5] else FilterLogic.AND_ANY,
        created_at=row[6],
        visible_from=row[7],
        expires_at=row[8],
        source=row[9] or "manual",
        confidence=row[10] if row[10] is not None else 1.0,
        source_id=row[11],
        target_id=row[12],
        relation_type=row[13],
        conflict_group=row[14],
        insertion_order=row[15] if row[15] is not None else 0,
        decay_half_life_days=row[16] if row[16] is not None else 90.0,
        review_status=_parse_review_status(row[17]),
        metadata=_as_dict(row[18]),
    )


def save_substance(substance: Substance) -> None:
    """原子写单条物质到 PostgreSQL（UPSERT，O(log n)）。

    Args:
        substance: 待持久化的物质
    """
    _ensure_schema_once()
    with write_transaction() as conn:
        exec_sql(conn, _UPSERT_SQL, _substance_params(substance))


def save_many(substances: Iterable[Substance]) -> int:
    """批量原子写入（单事务，失败回滚）。返回写入条数。

    Args:
        substances: 待持久化的物质集合
    """
    substances_list = list(substances)
    if not substances_list:
        return 0
    _ensure_schema_once()
    with write_transaction() as conn:
        for s in substances_list:
            exec_sql(conn, _UPSERT_SQL, _substance_params(s))
    return len(substances_list)


def _substance_params(s: Substance) -> list[Any]:
    """Substance → PostgreSQL 绑定参数列表（顺序匹配列序）。"""
    return [
        s.sid,
        s.form.value,
        s.content,
        json.dumps(s.keys, ensure_ascii=False),
        json.dumps(s.filter_keys, ensure_ascii=False),
        s.filter_logic.value,
        s.created_at,
        s.visible_from,
        s.expires_at,
        s.source,
        s.confidence,
        s.source_id,
        s.target_id,
        s.relation_type,
        s.conflict_group,
        s.insertion_order,
        s.decay_half_life_days,
        s.review_status.value,
        json.dumps(s.metadata, ensure_ascii=False),
    ]


def load_all() -> list[Substance]:
    """从 PostgreSQL 全量加载物质（启动时一次性载入内存热存储）。"""
    _ensure_schema_once()
    with read_connection() as conn:
        rows = exec_sql(
            conn,
            "SELECT sid, form, content, keys, filter_keys, filter_logic, "
            "created_at, visible_from, expires_at, source, confidence, "
            "source_id, target_id, relation_type, conflict_group, "
            "insertion_order, decay_half_life_days, review_status, metadata "
            "FROM substances ORDER BY created_at;",
        ).fetchall()

    substances: list[Substance] = []
    for row in rows:
        try:
            substances.append(_row_to_substance(row))
        except Exception as e:
            logger.warning(f"跳过无效物质行 {row[0]}: {e}")
    logger.info(f"物质已加载: PostgreSQL ({len(substances)} 条)")
    return substances


def count_substances() -> int:
    """返回 PostgreSQL 中物质总数（替代 meta.json 的权威计数）。"""
    try:
        _ensure_schema_once()
        with read_connection() as conn:
            result = exec_sql(conn, "SELECT COUNT(*) FROM substances;").fetchone()
        return int(result[0]) if result else 0
    except Exception:
        return 0


def drop_all() -> None:
    """清空物质表（迁移/重置专用，业务代码不应调用）。"""
    _ensure_schema_once()
    with write_transaction() as conn:
        exec_sql(conn, "DELETE FROM substances;")


def delete_substance(sid: str) -> bool:
    """从 PostgreSQL 删除单条物质（motion.compress 压缩后调用）。

    Args:
        sid: 物质唯一标识

    Returns:
        是否删除成功
    """
    try:
        _ensure_schema_once()
        with write_transaction() as conn:
            exec_sql(conn, "DELETE FROM substances WHERE sid = %s", [sid])
        return True
    except Exception:
        return False


def save_meta(directory: str | Path, substance_count: int) -> None:
    """保存元数据 — 与 PostgreSQL COUNT 对齐，不再独立权威。

    保留兼容旧消费者；新代码应直接调 ``count_substances``。
    """
    meta: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "substance_count": substance_count,
        "last_decay_run": None,
        "updated_at": datetime.now().isoformat(),
        "backend": "postgresql",
    }
    Path(directory).mkdir(parents=True, exist_ok=True)
    (Path(directory) / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_meta(directory: str | Path) -> dict[str, Any] | None:
    """加载元数据文件（兼容旧路径）。

    新代码应直接调 ``count_substances`` 获取权威计数。
    """
    meta_path = Path(directory) / "meta.json"
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"加载元数据失败: {e}")
        return None


# ── 向后兼容：JSONL 迁移入口 ─────────────────────────────────────
# 仅用于一次性把旧 substances.jsonl 导入 PostgreSQL，主路径不再走 JSONL。


def save_jsonl(substances: list[Substance], path: str | Path) -> None:
    """[已废弃] JSONL 全量重写 — 仅为迁移保留，新代码用 save_many。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for s in substances:
            f.write(s.model_dump_json() + "\n")
    logger.warning(f"JSONL 写入（已废弃路径）: {path} ({len(substances)} 条)")


def load_jsonl(path: str | Path) -> list[Substance]:
    """[迁移用] 从旧 JSONL 文件加载物质列表。"""
    path = Path(path)
    if not path.exists():
        return []
    substances: list[Substance] = []
    with path.open("r", encoding="utf-8") as f:
        for line_num, raw_line in enumerate(f, 1):
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                substances.append(Substance.model_validate_json(stripped))
            except Exception as e:
                logger.warning(f"跳过无效 JSONL 行 {line_num}: {e}")
    logger.info(f"JSONL 迁移加载: {path} ({len(substances)} 条)")
    return substances
