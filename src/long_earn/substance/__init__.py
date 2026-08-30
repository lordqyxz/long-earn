"""物质-运动统一架构（Substance-Motion）。

Substance 统一 event / relation / knowledge / strategy / backtest 五种形态。
motion 函数施加运算（activate/decay/conflict/compress），不持久化。
双索引：RetrievalIndex（keyword + semantic）+ GraphIndex（邻接表）。
持久化：DuckDB（原子追加 + WAL 崩溃安全，ADR-007 Phase 4）。
"""

from long_earn.substance.indices.graph import GraphIndex
from long_earn.substance.indices.retrieval import RetrievalIndex
from long_earn.substance.model import (
    Claim,
    FilterLogic,
    ReviewStatus,
    Substance,
    SubstanceForm,
)
from long_earn.substance.persistence import (
    count_substances,
    load_all,
    load_jsonl,
    load_meta,
    save_many,
    save_meta,
    save_substance,
)
from long_earn.substance.store import SubstanceStore

__all__ = [
    "Claim",
    "FilterLogic",
    "GraphIndex",
    "RetrievalIndex",
    "ReviewStatus",
    "Substance",
    "SubstanceForm",
    "SubstanceStore",
    "count_substances",
    "load_all",
    "load_jsonl",
    "load_meta",
    "save_many",
    "save_meta",
    "save_substance",
]
