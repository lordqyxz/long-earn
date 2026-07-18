"""本体种子数据 — ADR-014。

各领域本体的静态定义（财务指标 / 实体 / 策略族 / 事件类型），由
``OntologyRegistry.seed()`` 装载。种子数据是声明式的：每个 builder 返回
``(nodes, edges)`` 二元组，registry 负责校验与落图。

运行时动态节点（如公司实体从 universe 展开）通过 ``register_entity`` 注册，
不在种子数据里。
"""

from long_earn.ontology.seed.entity_ontology import build_entity_ontology
from long_earn.ontology.seed.event_ontology import build_event_ontology
from long_earn.ontology.seed.financial_ontology import build_financial_ontology
from long_earn.ontology.seed.strategy_ontology import build_strategy_ontology

__all__ = [
    "build_entity_ontology",
    "build_event_ontology",
    "build_financial_ontology",
    "build_strategy_ontology",
]
