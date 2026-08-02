"""已废弃的线性 strategy_rd 子图（兼容 shim）。

ADR-014 起由 HTR 取代；ADR-018 起策略研发控制器为 ResearchAgent。
实现已移至 ``strategy_rd._archive.subgraph``。新代码请使用：

- ``strategy_rd.research_agent.ResearchAgent``（推荐）
- ``strategy_rd.htr_subgraph.create_htr_subgraph``（兼容脚手架）
"""

from __future__ import annotations

import warnings

warnings.warn(
    "long_earn.strategy_rd.subgraph 已废弃（ADR-018）；"
    "请使用 ResearchAgent 或 htr_subgraph.create_htr_subgraph",
    DeprecationWarning,
    stacklevel=2,
)

from long_earn.strategy_rd._archive.subgraph import (  # noqa: E402
    _backtest_node,
    _backtest_optimized_cond,
    _backtest_optimized_node,
    _develop_node,
    _develop_optimized_node,
    _initial_retrieval_node,
    _optimize_node,
    _refine_node,
    _refine_optimized_cond,
    _reflection_node,
    _save_experience_node,
    _supervisor_node,
    create_strategy_rd_subgraph,
)

__all__ = [
    "_backtest_node",
    "_backtest_optimized_cond",
    "_backtest_optimized_node",
    "_develop_node",
    "_develop_optimized_node",
    "_initial_retrieval_node",
    "_optimize_node",
    "_refine_node",
    "_refine_optimized_cond",
    "_reflection_node",
    "_save_experience_node",
    "_supervisor_node",
    "create_strategy_rd_subgraph",
]
