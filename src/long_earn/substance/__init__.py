"""物质-运动统一架构（Substance-Motion）。

Substance 统一 event / relation / knowledge / strategy / backtest 五种形态。
motion 函数施加运算（activate/decay/conflict/compress），不持久化。
双索引：RetrievalIndex（keyword + semantic）+ GraphIndex（邻接表）。
持久化：JSONL，无 pickle，有 schema 版本号。
"""

from long_earn.substance.indices.graph import GraphIndex
from long_earn.substance.indices.retrieval import RetrievalIndex
from long_earn.substance.model import FilterLogic, Substance, SubstanceForm
from long_earn.substance.persistence import load_jsonl, load_meta, save_jsonl
from long_earn.substance.store import SubstanceStore

__all__ = [
    "FilterLogic",
    "GraphIndex",
    "RetrievalIndex",
    "Substance",
    "SubstanceForm",
    "SubstanceStore",
    "load_jsonl",
    "load_meta",
    "save_jsonl",
]
