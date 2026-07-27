"""事件类型本体种子 — ADR-014。

定义市场事件类型概念节点（宏观政策 / 行业事件 / 公司公告 / 财报事件），
每个类型带 ``sensitive_indicators`` 属性，让事件推理能自动关联到敏感财务指标。

例如"央行降息"事件 → ``macro_policy`` 类型 → ``sensitive_indicators=["debt_to_assets","capex"]``
→ 图谱遍历找到 debt_to_assets 高的标的受影响更大。

与 ``event_inference`` 子图的关系：``propagate`` 步骤产出的事件 RELATION 物质，
其 ``relation_type`` 必须是 ``RelationType.IMPACTS`` / ``PROPAGATES_TO``，
``target_id`` 改为 entity sid（先 upsert entity 物质），修复旧 ``target_id="600519.SH"``
字符串导致的图谱断裂。
"""

from __future__ import annotations

from long_earn.ontology.model import (
    OntologyDomain,
    OntologyEdge,
    OntologyNode,
    RelationType,
)


def _event_type(
    sid: str,
    label: str,
    aliases: list[str],
    *,
    sensitive_indicators: list[str],
    description: str,
) -> OntologyNode:
    """构造事件类型概念节点。

    ``sensitive_indicators`` 是 indicator sid 列表（如 ``["indicator:debt_to_assets"]``），
    供图谱遍历"事件类型 → 敏感指标 → 报告该指标的公司"链路使用。
    """
    return OntologyNode(
        sid=sid,
        domain=OntologyDomain.CONCEPT,
        label=label,
        aliases=aliases,
        properties={
            "kind": "event_type",
            "sensitive_indicators": sensitive_indicators,
            "description": description,
        },
    )


def build_event_ontology() -> tuple[list[OntologyNode], list[OntologyEdge]]:
    """构建事件类型本体 + 事件类型→敏感指标的边。"""
    nodes: list[OntologyNode] = []
    edges: list[OntologyEdge] = []

    macro_policy = _event_type(
        "event_type:macro_policy",
        "宏观政策",
        ["宏观政策", "macro_policy", "货币政策", "财政政策"],
        sensitive_indicators=["indicator:debt_to_assets", "indicator:capex"],
        description="央行利率 / 货币供应 / 财政刺激等宏观事件。降息利好高负债企业。",
    )
    industry_event = _event_type(
        "event_type:industry_event",
        "行业事件",
        ["行业事件", "industry_event", "行业政策", "供给侧"],
        sensitive_indicators=["indicator:revenue", "indicator:gross_margin"],
        description="行业限产 / 补贴 / 标准变更等。影响行业整体营收与利润率。",
    )
    company_announcement = _event_type(
        "event_type:company_announcement",
        "公司公告",
        ["公司公告", "company_announcement", "重大事项"],
        sensitive_indicators=["indicator:net_profit", "indicator:eps"],
        description="并购 / 增发 / 回购 / 管理层变动等公司层面事件。",
    )
    earnings_event = _event_type(
        "event_type:earnings_event",
        "财报事件",
        ["财报事件", "earnings_event", "业绩预告", "业绩快报"],
        sensitive_indicators=[
            "indicator:net_profit_yoy",
            "indicator:revenue_yoy",
            "indicator:roe",
            "indicator:earnings_quality",
        ],
        description="业绩预告 / 快报 / 正式财报披露。直接影响成长性与盈利能力评估。",
    )
    nodes.extend([macro_policy, industry_event, company_announcement, earnings_event])

    # ── 事件类型 → 敏感指标的 RELATES_TO_CONCEPT 边 ──────────────────
    # 这些边让图谱遍历能从事件类型走到指标，再走到报告该指标的公司
    event_indicator_edges = [
        (macro_policy.sid, "indicator:debt_to_assets"),
        (macro_policy.sid, "indicator:capex"),
        (industry_event.sid, "indicator:revenue"),
        (industry_event.sid, "indicator:gross_margin"),
        (company_announcement.sid, "indicator:net_profit"),
        (company_announcement.sid, "indicator:eps"),
        (earnings_event.sid, "indicator:net_profit_yoy"),
        (earnings_event.sid, "indicator:revenue_yoy"),
        (earnings_event.sid, "indicator:roe"),
        (earnings_event.sid, "indicator:earnings_quality"),
    ]
    for event_sid, indicator_sid in event_indicator_edges:
        edges.append(
            OntologyEdge(
                event_sid,
                indicator_sid,
                RelationType.RELATES_TO_CONCEPT,
                metadata={"relation": "sensitive_to"},
            )
        )

    return nodes, edges
