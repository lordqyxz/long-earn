"""持久化层 — JSONL 读写 + meta.json 版本管理。

无 pickle，无反序列化安全风险。
每行一个 Substance JSON（Pydantic 序列化）。
索引从 JSONL 重建，不持久化。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from long_earn.substance.model import Substance

SCHEMA_VERSION = 1


def save_jsonl(substances: list[Substance], path: str | Path) -> None:
    """将物质列表保存为 JSONL 文件。

    Args:
        substances: 物质列表
        path: JSONL 文件路径
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8") as f:
        for s in substances:
            f.write(s.model_dump_json() + "\n")

    _save_meta(path.parent, len(substances))
    logger.info(f"物质已持久化: {path} ({len(substances)} 条)")


def load_jsonl(path: str | Path) -> list[Substance]:
    """从 JSONL 文件加载物质列表。

    Args:
        path: JSONL 文件路径

    Returns:
        物质列表，文件不存在或为空时返回空列表
    """
    path = Path(path)
    if not path.exists():
        logger.warning(f"物质文件不存在: {path}")
        return []

    substances: list[Substance] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line_num, raw_line in enumerate(f, 1):
                stripped = raw_line.strip()
                if not stripped:
                    continue
                try:
                    substances.append(Substance.model_validate_json(stripped))
                except Exception as e:
                    logger.warning(f"跳过无效行 {line_num}: {e}")
    except Exception as e:
        logger.error(f"加载物质文件失败: {e}")
        return []

    logger.info(f"物质已加载: {path} ({len(substances)} 条)")
    return substances


def _save_meta(directory: Path, substance_count: int) -> None:
    """保存元数据文件（schema 版本 + 计数 + 最后操作时间）。"""
    meta: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "substance_count": substance_count,
        "last_decay_run": None,
        "updated_at": datetime.now().isoformat(),
    }
    meta_path = directory / "meta.json"
    meta_path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_meta(directory: str | Path) -> dict[str, Any] | None:
    """加载元数据文件。

    Returns:
        元数据字典，不存在时返回 None
    """
    meta_path = Path(directory) / "meta.json"
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"加载元数据失败: {e}")
        return None
