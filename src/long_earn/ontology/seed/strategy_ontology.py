"""策略族 + 经验本体种子 — ADR-014。

定义 4 大策略族（动量 / 价值 / 质量 / 成长）概念节点及其典型因子，
供策略经验保存时自动建 ``RELATES_TO_CONCEPT`` 边，让 ``search_experience`` 能
按因子族 + universe 做图谱检索（替代旧文本 TF-IDF）。

策略经验本身是 ``SubstanceForm.STRATEGY`` 物质，sid 形如 ``sub_xxx``，
保存时通过 ``OntologyRegistry.register_edge`` 建立到策略族概念节点的边。
"""

from __future__ import annotations

from long_earn.ontology.model import (
    OntologyDomain,
    OntologyEdge,
    OntologyNode,
    RelationType,
)


def _strategy_family(
    sid: str,
    label: str,
    aliases: list[str],
    *,
    typical_factors: list[str],
    description: str,
) -> OntologyNode:
    """构造策略族概念节点。"""
    return OntologyNode(
        sid=sid,
        domain=OntologyDomain.CONCEPT,
        label=label,
        aliases=aliases,
        properties={
            "kind": "strategy_family",
            "typical_factors": typical_factors,
            "description": description,
            "resolution": {
                "aspect": "策略族经验",
                "family": sid.rsplit(":", maxsplit=1)[-1],
            },
        },
    )


def build_strategy_ontology() -> tuple[list[OntologyNode], list[OntologyEdge]]:
    """构建策略族本体。

    边在运行时由经验保存建立（经验 sid → 策略族 RELATES_TO_CONCEPT）。
    种子阶段定义族节点本身 + 族间互斥/补充关系（CORRELATES_WITH）。
    """
    nodes: list[OntologyNode] = []
    edges: list[OntologyEdge] = []

    momentum = _strategy_family(
        "concept:strategy:momentum",
        "动量族",
        ["momentum", "动量", "趋势跟随", "trend_following"],
        typical_factors=["returns", "shift", "sma_ema", "macd", "bollinger"],
        description="基于价格趋势与加速度的因子族，假设强者恒强",
    )
    value = _strategy_family(
        "concept:strategy:value",
        "价值族",
        ["value", "价值", "估值修复", "mean_reversion"],
        typical_factors=["roe", "bps", "debt_to_assets", "gross_margin"],
        description="基于基本面估值偏离的因子族，假设价格回归内在价值",
    )
    quality = _strategy_family(
        "concept:strategy:quality",
        "质量族",
        ["quality", "质量", "优质企业"],
        typical_factors=[
            "roe_weighted",
            "net_profit_margin",
            "earnings_quality",
            "ocf_per_share",
        ],
        description="基于盈利质量与现金创造能力的因子族",
    )
    growth = _strategy_family(
        "concept:strategy:growth",
        "成长族",
        ["growth", "成长", "成长股"],
        typical_factors=["net_profit_yoy", "revenue_yoy", "research_expenses"],
        description="基于业绩增速的因子族，偏好高增长标的",
    )
    nodes.extend([momentum, value, quality, growth])

    # ── 族间关系（推理用：族切换时找互补/对立族）──────────────────────
    edges.extend(
        [
            # 价值 vs 动量：风格对立，常用于风格轮动
            OntologyEdge(
                "concept:strategy:value",
                "concept:strategy:momentum",
                RelationType.CORRELATES_WITH,
                weight=0.3,  # 低权重表示对立
                metadata={"relation": "风格对立", "use_case": "风格轮动"},
            ),
            # 质量 + 成长：互补，常用 GARP（Growth at Reasonable Price）
            OntologyEdge(
                "concept:strategy:quality",
                "concept:strategy:growth",
                RelationType.CORRELATES_WITH,
                weight=0.7,
                metadata={"relation": "互补", "use_case": "GARP"},
            ),
            # 价值 + 质量：巴菲特式"以合理价格买优质公司"
            OntologyEdge(
                "concept:strategy:value",
                "concept:strategy:quality",
                RelationType.CORRELATES_WITH,
                weight=0.8,
                metadata={"relation": "互补", "use_case": "价值质量复合"},
            ),
        ]
    )

    return nodes, edges
