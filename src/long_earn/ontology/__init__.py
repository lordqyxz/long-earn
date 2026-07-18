"""全局本体论层（Ontology Layer）— ADR-014。

本体论统一财务指标、公司实体、市场事件、策略经验、抽象概念为 ``OntologyNode``，
通过受约束的 ``RelationType`` 建立 ``OntologyEdge``，由 ``OntologyGraph`` 提供跨域遍历。

上游（services / strategy_rd / stock_analysis）通过 ``Connector.get_concept`` 用
"概念"取数，屏蔽多数据源、字段命名差异、PIT 裁剪、降级链。

依赖方向（import-linter 契约）：``ontology`` 是最底层，不依赖 ``backtest`` /
``services`` / ``tools`` / ``strategy_rd`` 等上层模块；``substance`` / ``backtest.data``
可依赖 ``ontology``。
"""

from long_earn.ontology.graph import GraphPath, OntologyGraph
from long_earn.ontology.model import (
    OntologyDomain,
    OntologyEdge,
    OntologyNode,
    RelationType,
)
from long_earn.ontology.registry import OntologyRegistry

__all__ = [
    "GraphPath",
    "OntologyDomain",
    "OntologyEdge",
    "OntologyGraph",
    "OntologyNode",
    "OntologyRegistry",
    "RelationType",
]
