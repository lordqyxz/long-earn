"""统一存储路径辅助 — 所有生成数据的落盘位置由此模块唯一裁决。

单一数据源：``LONG_EARN_DATA_DIR`` 环境变量 → 默认 repo 同级 ``long-earn-data``。

设计约束（import-linter）：
- ``core`` 是最底层，**不得** import ``config``/``services``/``backtest``/
  ``strategy_rd``/``tools`` 等上层模块（``backtest.data`` 与 ``substance`` 的
  independence 合约要求它们只能依赖更底层的模块）。
- 本模块只负责"路径在哪"的裁决，不负责读写（DuckDB 连接、JSONL 解析等仍在
  各自业务模块），保持职责单一、零上层依赖。

Usage::

    from long_earn.core.storage import backtest_cache_path, substances_db_path
    cache = DataCache(db_path=backtest_cache_path())
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
        ``{data_dir, backtest_cache, backtest_audit, substances_db,
          hypothesis_tree_dir, strategy_results, best_strategy}`` 路径字典
    """
    base = Path(data_dir).expanduser().resolve() if data_dir else get_data_dir()
    base.mkdir(parents=True, exist_ok=True)
    return {
        "data_dir": base,
        "backtest_cache_path": base / "backtest_cache.duckdb",
        # 审计日志独立库：与价格缓存分库，避免 Web 只读连接与回测写连接
        # 竞争同一文件锁（单写者纪律：审计库仅 DuckDBAuditProvider 写入，
        # 其余消费者一律 read_only 连接）。
        # 注意：文件名不能是 backtest_audit.duckdb —— DuckDB catalog 名取
        # 文件名 stem，会与 schema 名 backtest_audit 冲突（ambiguous reference）。
        "backtest_audit_path": base / "audit.duckdb",
        "substances_db_path": base / "substances.duckdb",
        "hypothesis_tree_dir": base / "hypothesis_trees",
        "strategy_results_path": base / "strategy_research_results.json",
        "best_strategy_path": base / "best_strategy.yaml",
    }


# ── 派生路径（单一真相源，其他模块只读不重算）──────────────────────


def backtest_cache_path() -> Path:
    """回测行情/财务缓存 DuckDB 路径。"""
    return get_data_dir() / "backtest_cache.duckdb"


def backtest_audit_path() -> Path:
    """回测审计日志独立 DuckDB 路径。

    审计与价格缓存分库存储：高频小写入的审计日志（backtest_audit.logs）
    不再与 GB 级列式价格缓存共享一个文件，消除「Web 只读连接 vs 回测写
    连接」在同一文件上的锁竞争与 WAL checkpoint 数据丢失风险。

    文件名取 ``audit.duckdb`` 而非 ``backtest_audit.duckdb``：DuckDB catalog
    名取文件名 stem，若与 schema 名 ``backtest_audit`` 相同会导致
    ``"backtest_audit".logs`` 引用歧义（BinderError）。
    """
    return get_data_dir() / "audit.duckdb"


def substances_db_path() -> Path:
    """物质-运动记忆库 DuckDB 路径（ADR-007 Phase 4）。"""
    return get_data_dir() / "substances.duckdb"


def hypothesis_tree_dir() -> Path:
    """假设树 JSON 存储目录（ADR-010 HTR）。"""
    d = get_data_dir() / "hypothesis_trees"
    d.mkdir(parents=True, exist_ok=True)
    return d


def panel_cache_dir() -> Path:
    """merged panel 跨 run 缓存目录（Arrow IPC 文件）。

    同 (symbols, start, end) 的合并面板在参数寻优 / 并行回测中被
    反复构建；此处落盘复用，key 语义见 ``backtest.data.panel_cache``。
    """
    d = get_data_dir() / "panel_cache"
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
