"""假设树持久化存储（HTR Phase 1）。

JSON 文件持久化，每次研究 run 一棵树。
路径：~/.long_earn/hypothesis_trees/{run_id}.json
"""

from __future__ import annotations

import json
from pathlib import Path

from loguru import logger

from long_earn.strategy_rd.hypothesis_tree import HypothesisTree

_DEFAULT_DIR = Path.home() / ".long_earn" / "hypothesis_trees"


class HypothesisTreeStore:
    """假设树 JSON 文件存储。

    save: 持久化一棵树。
    load: 按 run_id 加载一棵树。
    list_runs: 列出所有已持久化的 run_id。
    """

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self._base_dir = Path(base_dir) if base_dir else _DEFAULT_DIR

    def save(self, tree: HypothesisTree) -> Path:
        """持久化假设树，返回文件路径。"""
        self._base_dir.mkdir(parents=True, exist_ok=True)
        path = self._base_dir / f"{tree.run_id}.json"
        data = tree.serialize()
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info(f"假设树已保存: {path} ({tree.node_count} 节点)")
        return path

    def load(self, run_id: str) -> HypothesisTree | None:
        """按 run_id 加载假设树。文件不存在返回 None。"""
        path = self._base_dir / f"{run_id}.json"
        if not path.exists():
            logger.warning(f"假设树文件不存在: {path}")
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        tree = HypothesisTree.deserialize(data)
        logger.info(f"假设树已加载: {run_id} ({tree.node_count} 节点)")
        return tree

    def list_runs(self) -> list[str]:
        """列出所有已持久化的 run_id。"""
        if not self._base_dir.exists():
            return []
        return sorted(
            p.stem for p in self._base_dir.glob("*.json") if p.is_file()
        )
