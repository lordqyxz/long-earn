"""概念解析器 — ADR-014 阶段 C。

把上层传入的"概念"（如"盈利能力"/"成分股"/"相关事件"/"动量族经验"）翻译为
连接器可执行的取数指令：

- ``indicator_panel`` → 财务指标面板（字段集 + 时间窗 + PIT）
- ``universe`` → 成分股列表
- ``event_graph`` → 事件图谱遍历
- ``experience`` → 策略经验图谱检索
- ``intelligence`` → 市场情报方法调用

解析表是声明式的，由本体种子数据驱动（``OntologyNode.properties.resolution``）。
新增概念只需在种子数据里加节点 + resolution，无需改业务代码。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Literal

from long_earn.ontology.registry import OntologyRegistry

ResolutionKind = Literal[
    "indicator_panel",  # 财务指标面板
    "universe",  # 成分股列表
    "event_graph",  # 事件图谱遍历
    "experience",  # 策略经验图谱检索
    "intelligence",  # 市场情报方法
    "unknown",  # 未识别概念
]


@dataclass
class ConceptResolution:
    """概念解析结果 — 连接器据此分发取数。"""

    kind: ResolutionKind
    # indicator_panel: 标准字段名列表（如 ["roe","gross_margin"]）
    # universe: universe_type 字符串（如 "csi300"）
    # event_graph / experience: 起始节点 sid 或 concept sid
    # intelligence: 情报方法名列表（如 ["get_fund_flow","get_hot_rank"]）
    payload: dict[str, object]
    # 解析到的概念节点 sid（若有）
    concept_sid: str = ""
    # 图谱关联节点域过滤（供 Connector 取 related_nodes 用）
    related_domains: set[str] | None = None


class ConceptResolver:
    """概念解析器 — 把 aspect 字符串翻译为 ``ConceptResolution``。

    解析顺序：
    1. 本体种子节点 label/alias 精确匹配（如 "盈利能力" → concept:profitability）
    2. 通配规则（如 "成分股" 任何主体 → universe 类型）
    3. 未识别 → unknown
    """

    # aspect → ResolutionKind 的通配规则（无本体节点匹配时用）
    _ASPECT_KIND_RULES: ClassVar[dict[str, ResolutionKind]] = {
        "成分股": "universe",
        "相关事件": "event_graph",
        "事件": "event_graph",
        "市场情绪": "intelligence",
        "资金流向": "intelligence",
        "热榜": "intelligence",
    }

    def __init__(self, registry: OntologyRegistry) -> None:
        self._registry = registry

    def resolve(self, aspect: str) -> ConceptResolution:
        """解析 aspect 字符串为 ConceptResolution。"""
        # 1. 本体节点精确匹配
        node = self._registry.find_by_label_or_alias(aspect)
        if node is not None:
            return self._resolve_from_node(node.sid, node.properties)

        # 2. 通配规则
        kind = self._ASPECT_KIND_RULES.get(aspect, "unknown")
        if kind == "universe":
            return ConceptResolution(kind="universe", payload={"universe_type": ""})
        if kind == "event_graph":
            return ConceptResolution(kind="event_graph", payload={})
        if kind == "intelligence":
            return ConceptResolution(
                kind="intelligence",
                payload={"methods": [aspect]},
            )
        return ConceptResolution(kind="unknown", payload={})

    def resolve_subject(self, subject: str) -> tuple[str, list[str]]:
        """解析主体标识为 (entity_sid, symbols)。

        - 主体是 universe 概念（如 "csi300"）→ 返回 (universe_sid, [])
          连接器再调 universe 解析为成分股
        - 主体是公司实体（如 "600519.SH" 或 "贵州茅台"）→ 返回 (entity_sid, [xt_symbol])
        - 主体是普通股票代码 → 注册为实体并返回
        """
        # 先查本体是否已有该实体（按 alias）
        node = self._registry.find_by_label_or_alias(subject)
        if node is not None:
            # 概念节点（如 universe/industry）
            if (
                node.domain.value == "concept"
                and node.properties.get("kind") == "universe"
            ):
                return node.sid, []
            # 实体节点
            if node.domain.value == "entity":
                xt_symbol = node.properties.get("xt_symbol", subject)
                return node.sid, [xt_symbol]
        # 未注册实体，按 xt_symbol 格式注册
        # 简单启发：含 "." 视为 xtquant 格式
        if "." in subject:
            entity_sid = self._registry.register_entity(subject, subject)
            return entity_sid, [subject]
        # 否则视为待解析的名称，返回空（连接器可降级为 universe 查询）
        return "", [subject] if subject else []

    def _resolve_from_node(  # noqa: PLR0911
        self,
        sid: str,
        properties: dict[str, object],
    ) -> ConceptResolution:
        """从本体节点 properties.resolution 解析。"""
        # resolution 字段在种子节点 properties 里
        resolution = properties.get("resolution", {})
        if not isinstance(resolution, dict):
            return ConceptResolution(kind="unknown", payload={}, concept_sid=sid)

        # indicator_family（财务指标族）
        if properties.get("kind") == "indicator_family":
            fields = resolution.get("fields", [])
            return ConceptResolution(
                kind="indicator_panel",
                payload={"fields": list(fields)},
                concept_sid=sid,
                related_domains={"indicator", "concept"},
            )

        # universe
        if properties.get("kind") == "universe":
            universe_type = resolution.get("universe_type", "")
            return ConceptResolution(
                kind="universe",
                payload={"universe_type": universe_type},
                concept_sid=sid,
            )

        # strategy_family
        if properties.get("kind") == "strategy_family":
            family = resolution.get("family", sid.rsplit(":", maxsplit=1)[-1])
            return ConceptResolution(
                kind="experience",
                payload={"strategy_family": family},
                concept_sid=sid,
                related_domains={"experience", "strategy", "concept"},
            )

        # event_type
        if properties.get("kind") == "event_type":
            return ConceptResolution(
                kind="event_graph",
                payload={"event_type_sid": sid},
                concept_sid=sid,
                related_domains={"event", "entity", "indicator"},
            )

        # industry
        if properties.get("kind") == "industry":
            return ConceptResolution(
                kind="event_graph",
                payload={"industry_sid": sid},
                concept_sid=sid,
                related_domains={"entity", "event"},
            )

        return ConceptResolution(kind="unknown", payload={}, concept_sid=sid)
