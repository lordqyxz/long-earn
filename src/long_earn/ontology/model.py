"""本体论核心模型 — ADR-014。

``OntologyNode`` 统一财务指标、公司实体、市场事件、策略经验、抽象概念为图节点；
``OntologyEdge`` 用受约束的 ``RelationType`` 枚举建立节点间关系（替代旧自由字符串）。

sid 约定：
- substance 物质的 sid 直接作为 OntologyNode sid（substance 是本体节点的子集）
- 本体内部概念节点用领域前缀 sid：``indicator:roe`` / ``concept:profitability`` /
  ``entity:600519.SH`` / ``concept:universe:csi300``
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class OntologyDomain(StrEnum):
    """本体论域 — 节点所属的知识领域。"""

    ENTITY = "entity"  # 公司 / 行业 / 板块 / Universe
    INDICATOR = "indicator"  # 财务指标 / 技术指标
    EVENT = "event"  # 市场事件 / 公告事件
    STRATEGY = "strategy"  # 策略实例
    EXPERIENCE = "experience"  # 策略经验 / 教训
    CONCEPT = "concept"  # 抽象概念（盈利能力 / 动量族 / 大盘蓝筹）


class RelationType(StrEnum):
    """受约束的关系类型枚举 — 替代旧 substance relation_type 自由字符串。

    旧实现 ``relation_type`` 是自由 str（impacts/propagates_to/correlates_with/
    related_to 语义重叠且无校验）。此处用 StrEnum 强约束，``OntologyRegistry``
    在 add_edge 入口校验。
    """

    IMPACTS = "impacts"  # 事件 → 标的 / 行业
    PROPAGATES_TO = "propagates_to"  # 事件 → 传导目标（多跳链）
    BELONGS_TO = "belongs_to"  # 公司 → 行业 / 板块
    MEMBER_OF = "member_of"  # 标的 → Universe
    REPORTS_INDICATOR = "reports_indicator"  # 公司 → 指标（某期财报）
    DERIVED_FROM = "derived_from"  # 指标 → 父指标（杜邦分解）
    SAME_DIMENSION = "same_dimension"  # 同维指标（ROE 同维 ROIC / ROA）
    APPLIES_STRATEGY = "applies_strategy"  # 经验 → 策略
    DERIVED_FROM_EXPERIENCE = "derived_from_experience"  # 策略 → 经验
    RELATES_TO_CONCEPT = "relates_to_concept"  # 任意节点 → 抽象概念
    CORRELATES_WITH = "correlates_with"  # 跨域关联


class OntologyNode(BaseModel):
    """本体论节点 — 可被图谱遍历、可被连接器解析。

    每个节点有唯一 sid、所属域、人类可读 label、别名列表、领域专属 properties。
    substance 物质的 sid 直接作为 OntologyNode sid（substance 是本体节点的子集）；
    本体内部概念节点用领域前缀 sid（如 ``indicator:roe``）。

    支持位置参数构造（``OntologyNode("sid", DOMAIN, "label")``）以简化种子数据，
    也支持纯关键字参数（与 Pydantic v2 默认行为一致）。
    """

    sid: str
    domain: OntologyDomain
    label: str
    aliases: list[str] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)

    def __init__(
        self,
        sid: str | None = None,
        domain: OntologyDomain | None = None,
        label: str | None = None,
        **kwargs: Any,
    ) -> None:
        """位置参数构造：前三个位置参对应 sid / domain / label，其余走关键字。"""
        if sid is not None:
            kwargs["sid"] = sid
        if domain is not None:
            kwargs["domain"] = domain
        if label is not None:
            kwargs["label"] = label
        super().__init__(**kwargs)

    def matches_alias(self, term: str) -> bool:
        """判断 term 是否命中 label 或任一 alias（大小写不敏感）。

        供 ``Connector`` 实体解析与概念解析使用：用户传入 "贵州茅台" / "roe" /
        "ReturnOnEquity" 等自然语言时，通过别名匹配定位节点。
        """
        term_lower = term.lower().strip()
        if term_lower == self.label.lower().strip():
            return True
        return any(term_lower == alias.lower().strip() for alias in self.aliases)


class OntologyEdge(BaseModel):
    """本体论边 — 受约束的有向带权关系。

    相比旧 ``GraphIndex`` 的 ``(target_id, relation_sid, weight)`` 元组：
    - 边是结构化对象，含 ``relation_type``（受 enum 约束）+ ``provenance`` + ``visible_from``
    - ``visible_from`` 支持 PIT 过滤（边何时可见，回测时不窥未来）
    - ``metadata`` 承载领域专属边属性（如事件影响的方向、置信度细节）

    支持位置参数构造（``OntologyEdge("src", "tgt", RELATION_TYPE)``）以简化种子数据，
    也支持纯关键字参数。
    """

    source_sid: str
    target_sid: str
    relation_type: RelationType
    weight: float = 1.0
    provenance: str = ""  # xtquant / wind / llm_inferred / manual
    visible_from: datetime | None = None  # PIT：边何时可见
    metadata: dict[str, Any] = Field(default_factory=dict)

    def __init__(
        self,
        source_sid: str | None = None,
        target_sid: str | None = None,
        relation_type: RelationType | None = None,
        **kwargs: Any,
    ) -> None:
        """位置参数构造：前三个位置参对应 source_sid / target_sid / relation_type。"""
        if source_sid is not None:
            kwargs["source_sid"] = source_sid
        if target_sid is not None:
            kwargs["target_sid"] = target_sid
        if relation_type is not None:
            kwargs["relation_type"] = relation_type
        super().__init__(**kwargs)

    def is_visible_at(self, when: datetime) -> bool:
        """PIT 可见性判断 — 与 ``Substance.is_visible_at`` 同语义。"""
        return not (self.visible_from is not None and when < self.visible_from)
