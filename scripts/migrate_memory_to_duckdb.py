"""一次性迁移脚本：旧记忆存储 → DuckDB 物质库（ADR-007 Phase 4）。

将以下遗留格式迁移到 ``~/.long_earn/substances.duckdb``：
1. ``substances.jsonl``（ADR-007 Phase 1-3 的 JSONL）→ DuckDB substances 表
2. ``memory.facts.pkl`` / ``memory.npz``（ADR-004 旧系统）→ 跳过（pickle 不安全，已无代码读取）

用法::

    uv run python scripts/migrate_memory_to_duckdb.py [--data-dir ~/.long_earn]

迁移完成后会打印建议清理的遗留文件清单（不自动删除，需人工确认）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from loguru import logger

logger.remove()
logger.add(sys.stderr, format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | {message}")

from long_earn.core.storage import substances_db_path  # noqa: E402
from long_earn.substance.persistence import load_jsonl, save_many  # noqa: E402

LEGACY_FILES = [
    "substances.jsonl",
    "memory.facts.pkl",
    "memory.npz",
    "test_memory.facts.pkl",
    "test_memory.npz",
    "meta.json",
    "vectors.npy",
]


def migrate(legacy_dir: Path) -> int:
    """执行迁移：旧目录 → 统一数据目录（core.storage 裁决）。

    Args:
        legacy_dir: 旧数据目录（默认 ~/.long_earn，含 substances.jsonl）
    """
    target = substances_db_path()
    source_jsonl = legacy_dir / "substances.jsonl"

    if not source_jsonl.exists():
        logger.warning(f"未找到旧 JSONL 文件: {source_jsonl}（可能已迁移或从未创建）")
        return 0

    substances = load_jsonl(source_jsonl)
    if not substances:
        logger.warning(f"JSONL 无有效物质: {source_jsonl}")
        return 0

    count = save_many(substances, target)
    logger.info(f"迁移完成: {source_jsonl} → {target} ({count} 条物质)")

    # 列出可清理的遗留文件
    removable: list[Path] = []
    for name in LEGACY_FILES:
        f = legacy_dir / name
        if f.exists():
            removable.append(f)
    if removable:
        logger.info("可清理的遗留文件（迁移后可手动删除）:")
        for f in removable:
            logger.info(f"  rm {f}")
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="迁移旧记忆存储到 DuckDB")
    parser.add_argument(
        "--legacy-dir",
        default=str(Path.home() / ".long_earn"),
        help="旧数据目录（默认 ~/.long_earn）",
    )
    args = parser.parse_args()
    legacy_dir = Path(args.legacy_dir).expanduser()

    if not legacy_dir.exists():
        logger.error(f"旧数据目录不存在: {legacy_dir}")
        sys.exit(1)

    n = migrate(legacy_dir)
    logger.info(f"迁移结束，共 {n} 条物质")


if __name__ == "__main__":
    main()
