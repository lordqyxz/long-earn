"""统一存储路径辅助 — 所有生成数据的落盘位置由此模块唯一裁决。

单一数据源：``LONG_EARN_DATA_DIR`` 环境变量 → 默认 repo 同级 ``long-earn-data``。

设计约束（import-linter）：
- ``core`` 是最底层，**不得** import ``config``/``services``/``backtest``/
  ``strategy_rd``/``tools`` 等上层模块（``backtest.data`` 与 ``substance`` 的
  independence 合约要求它们只能依赖更底层的模块）。
- 本模块只负责"路径在哪"的裁决，不负责读写（PG 连接、JSONL 解析等仍在
  各自业务模块），保持职责单一、零上层依赖。

Usage::

    from long_earn.core.storage import hypothesis_tree_dir, checkpoint_db_path
"""

from __future__ import annotations

import os
from pathlib import Path

# repo 根目录（本文件位于 src/long_earn/core/，向上 3 级）
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
# 默认数据目录：repo 同级 long-earn-data（即 D:/dev/long-earn-data）
DEFAULT_DATA_DIR = _PROJECT_ROOT.parent / "long-earn-data"


def get_data_dir() -> Path:
    """裁决数据根目录。

    优先级：``LONG_EARN_DATA_DIR`` 环境变量 → repo 同级 ``long-earn-data``。
    确保目录存在。

    Returns:
        数据根目录绝对路径
    """
    raw = os.getenv("LONG_EARN_DATA_DIR", str(DEFAULT_DATA_DIR))
    d = Path(raw).expanduser().resolve()
    d.mkdir(parents=True, exist_ok=True)
    return d


def resolve_paths(data_dir: str | Path | None = None) -> dict[str, Path]:
    """从数据根目录派生全部生成数据路径（单一真相源）。

    ``LONG_EARN_DATA_DIR`` 是**唯一**控制存储位置的环境变量；其他所有路径
    均由此派生。本函数供 ``AppConfig.from_env`` 一次性解析后注入配置字段，
    避免各模块各自读 env 造成的分裂。

    Args:
        data_dir: 显式数据根目录；None 时读取 ``LONG_EARN_DATA_DIR`` env

    Returns:
        ``{data_dir, hypothesis_tree_dir, strategy_results_path,
          best_strategy_path}`` 路径字典
    """
    base = Path(data_dir).expanduser().resolve() if data_dir else get_data_dir()
    base.mkdir(parents=True, exist_ok=True)
    return {
        "data_dir": base,
        "hypothesis_tree_dir": base / "hypothesis_trees",
        "strategy_results_path": base / "strategy_research_results.json",
        "best_strategy_path": base / "best_strategy.yaml",
    }


# ── 派生路径（单一真相源，其他模块只读不重算）──────────────────────


def hypothesis_tree_dir() -> Path:
    """假设树 JSON 存储目录（ADR-010 HTR）。"""
    d = get_data_dir() / "hypothesis_trees"
    d.mkdir(parents=True, exist_ok=True)
    return d


def strategy_results_path() -> Path:
    """策略研发结果 JSON 路径。"""
    return get_data_dir() / "strategy_research_results.json"


def best_strategy_path() -> Path:
    """最佳策略 YAML 路径。"""
    return get_data_dir() / "best_strategy.yaml"


def checkpoint_db_path() -> Path:
    """LangGraph SqliteSaver checkpoint 数据库路径。

    用于策略研发循环的断点持久化与中断恢复。每个研究 thread 由
    ``thread_id`` 区分，可共享同一个 sqlite 文件。
    """
    return get_data_dir() / "checkpoints.sqlite"
