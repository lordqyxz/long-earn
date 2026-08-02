"""持久化层 — DuckDB 事务式存储（替代 JSONL 全量重写）。

ADR-007 Phase 4：原子追加写 + 事务保证 + 列式检索。
- 单条 ``INSERT`` O(log n)，不再每次 add 全量重写整个文件；
- DuckDB WAL 保证崩溃安全，杜绝 JSONL 截断到一半的数据丢失；
- ``meta.json`` 由 ``SELECT COUNT(*)`` 派生，消灭元数据与数据脱钩问题；
- 索引（TF-IDF / Graph）仍从 DuckDB 全量重建，保持内存热存储不变。

向后兼容：``load_jsonl`` / ``save_jsonl`` 保留为迁移入口，主路径改为 DuckDB。
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
from loguru import logger

from long_earn.substance.model import Substance

SCHEMA_VERSION = 2

# ── DDL ────────────────────────────────────────────────────────────
# 列式存储结构化字段，metadata 用 JSON 列承载任意字典。
# 主键 sid 保证幂等 INSERT OR REPLACE，杜绝重复追加。
_DDL = """
CREATE TABLE IF NOT EXISTS substances (
    sid              VARCHAR PRIMARY KEY,
    form             VARCHAR NOT NULL,
    content          VARCHAR,
    keys             JSON,
    filter_keys      JSON,
    filter_logic     VARCHAR,
    created_at       TIMESTAMP,
    visible_from     TIMESTAMP,
    expires_at       TIMESTAMP,
    source           VARCHAR,
    confidence       DOUBLE,
    source_id        VARCHAR,
    target_id        VARCHAR,
    relation_type    VARCHAR,
    conflict_group   VARCHAR,
    insertion_order  INTEGER,
    decay_half_life_days DOUBLE,
    metadata         JSON
);
"""

# 时间过滤索引：visible_from 防未来函数的高频谓词
_INDEX_VISIBLE = (
    "CREATE INDEX IF NOT EXISTS idx_substances_visible_from "
    "ON substances(visible_from);"
)
_INDEX_FORM = "CREATE INDEX IF NOT EXISTS idx_substances_form ON substances(form);"
_INDEX_CREATED = (
    "CREATE INDEX IF NOT EXISTS idx_substances_created ON substances(created_at);"
)


def _connect(path: str | Path) -> duckdb.DuckDBPyConnection:
    """打开（或创建）DuckDB 文件并建表。"""
    p = Path(path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = duckdb.connect(str(p))
    conn.execute(_DDL)
    conn.execute(_INDEX_VISIBLE)
    conn.execute(_INDEX_FORM)
    conn.execute(_INDEX_CREATED)
    return conn


def _row_to_substance(row: Any) -> Substance:
    """把 DuckDB 查询行转回 Substance（Pydantic 反序列化）。"""
    return Substance(
        sid=row[0],
        form=row[1],
        content=row[2] or "",
        keys=json.loads(row[3]) if row[3] else [],
        filter_keys=json.loads(row[4]) if row[4] else [],
        filter_logic=row[5] or "and_any",
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
        metadata=json.loads(row[17]) if row[17] else {},
    )


def save_substance(substance: Substance, path: str | Path) -> None:
    """原子追加单条物质到 DuckDB（INSERT OR REPLACE，O(log n)）。"""
    conn = _connect(path)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO substances VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            _substance_params(substance),
        )
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def save_many(substances: Iterable[Substance], path: str | Path) -> int:
    """批量原子追加（单事务，失败回滚）。返回写入条数。"""
    substances_list = list(substances)
    if not substances_list:
        return 0
    conn = _connect(path)
    try:
        conn.execute("BEGIN TRANSACTION;")
        for s in substances_list:
            conn.execute(
                """
                INSERT OR REPLACE INTO substances VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                _substance_params(s),
            )
        conn.execute("COMMIT;")
        return len(substances_list)
    except Exception:
        conn.execute("ROLLBACK;")
        raise
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def _substance_params(s: Substance) -> list[Any]:
    """Substance → DuckDB 绑定参数列表（顺序匹配 DDL 列序）。"""
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
        json.dumps(s.metadata, ensure_ascii=False),
    ]


def load_all(path: str | Path) -> list[Substance]:
    """从 DuckDB 全量加载物质（启动时一次性载入内存热存储）。"""
    p = Path(path).expanduser()
    if not p.exists():
        logger.warning(f"物质数据库不存在: {p}")
        return []
    conn = _connect(p)
    try:
        rows = conn.execute(
            "SELECT sid, form, content, keys, filter_keys, filter_logic, "
            "created_at, visible_from, expires_at, source, confidence, "
            "source_id, target_id, relation_type, conflict_group, "
            "insertion_order, decay_half_life_days, metadata "
            "FROM substances ORDER BY created_at;"
        ).fetchall()
    finally:
        with contextlib.suppress(Exception):
            conn.close()

    substances: list[Substance] = []
    for row in rows:
        try:
            substances.append(_row_to_substance(row))
        except Exception as e:
            logger.warning(f"跳过无效物质行 {row[0]}: {e}")
    logger.info(f"物质已加载: {p} ({len(substances)} 条)")
    return substances


def count_substances(path: str | Path) -> int:
    """返回 DuckDB 中物质总数（替代 meta.json 的权威计数）。"""
    p = Path(path).expanduser()
    if not p.exists():
        return 0
    conn = _connect(p)
    try:
        result = conn.execute("SELECT COUNT(*) FROM substances;").fetchone()
        return int(result[0]) if result else 0
    except Exception:
        return 0
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def drop_all(path: str | Path) -> None:
    """清空物质表（迁移/重置专用，业务代码不应调用）。"""
    conn = _connect(path)
    try:
        conn.execute("DELETE FROM substances;")
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def delete_substance(sid: str, path: str | Path) -> bool:
    """从 DuckDB 删除单条物质（motion.compress 压缩后调用）。"""
    conn = _connect(path)
    try:
        conn.execute("DELETE FROM substances WHERE sid = ?", [sid])
        return True
    except Exception:
        return False
    finally:
        with contextlib.suppress(Exception):
            conn.close()


def save_meta(directory: str | Path, substance_count: int) -> None:
    """保存元数据 — 与 DuckDB COUNT 对齐，不再独立权威。

    保留兼容旧消费者；新代码应直接调 ``count_substances``。
    """
    meta: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "substance_count": substance_count,
        "last_decay_run": None,
        "updated_at": datetime.now().isoformat(),
        "backend": "duckdb",
    }
    Path(directory).mkdir(parents=True, exist_ok=True)
    (Path(directory) / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_meta(directory: str | Path) -> dict[str, Any] | None:
    """加载元数据文件（兼容旧路径）。"""
    meta_path = Path(directory) / "meta.json"
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"加载元数据失败: {e}")
        return None


# ── 向后兼容：JSONL 迁移入口 ─────────────────────────────────────
# 仅用于一次性把旧 substances.jsonl 导入 DuckDB，主路径不再走 JSONL。


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
