"""财务指标本体种子 — ADR-014。

定义财务指标节点（盈利能力 / 成长性 / 估值 / 质量 / 现金流 / 资本结构族），
以及杜邦分解等 ``DERIVED_FROM`` 关系 + ``SAME_DIMENSION`` 同维关联 +
``RELATES_TO_CONCEPT`` 概念组归属。

这些节点是 ``Connector`` 解析 "盈利能力" / "成长性" 等概念为具体字段集的依据：
``ConceptResult.data`` 的字段清单由概念节点的 ``properties.resolution.fields`` 决定。
"""

from __future__ import annotations

from long_earn.ontology.model import (
    OntologyDomain,
    OntologyEdge,
    OntologyNode,
    RelationType,
)


def _indicator(  # noqa: PLR0913
    sid: str,
    label: str,
    aliases: list[str],
    *,
    dimension: str,
    formula: str = "",
    typical_range: str = "",
    xt_fields: list[str] | None = None,
) -> OntologyNode:
    """构造财务指标节点。"""
    return OntologyNode(
        sid=sid,
        domain=OntologyDomain.INDICATOR,
        label=label,
        aliases=aliases,
        properties={
            "dimension": dimension,
            "formula": formula,
            "typical_range": typical_range,
            "xt_fields": xt_fields or [],
        },
    )


def _concept(
    sid: str,
    label: str,
    aliases: list[str],
    *,
    fields: list[str],
    description: str = "",
) -> OntologyNode:
    """构造抽象概念节点（指标族）。``fields`` 是该概念对应的财务面板字段集。"""
    return OntologyNode(
        sid=sid,
        domain=OntologyDomain.CONCEPT,
        label=label,
        aliases=aliases,
        properties={
            "kind": "indicator_family",
            "resolution": {"aspect": label, "fields": fields},
            "description": description,
        },
    )


def build_financial_ontology() -> tuple[list[OntologyNode], list[OntologyEdge]]:
    """构建财务指标本体：指标节点 + 概念族节点 + 杜邦分解/同维/概念归属边。"""
    nodes: list[OntologyNode] = []
    edges: list[OntologyEdge] = []

    # ── 盈利能力族指标 ──────────────────────────────────────────────────
    roe = _indicator(
        "indicator:roe",
        "净资产收益率 ROE",
        ["roe", "净资产收益率", "ReturnOnEquity"],
        dimension="盈利能力",
        formula="net_profit / total_equity * 年化系数",
        typical_range="0.08-0.25",
        xt_fields=["roe_weighted", "du_return_on_equity"],
    )
    roe_weighted = _indicator(
        "indicator:roe_weighted",
        "加权净资产收益率",
        ["roe_weighted", "加权ROE"],
        dimension="盈利能力",
        formula="Pershareindex 预计算值",
        typical_range="0.08-0.25",
        xt_fields=["equity_roe"],
    )
    gross_margin = _indicator(
        "indicator:gross_margin",
        "毛利率",
        ["gross_margin", "毛利率", "gross_profit"],
        dimension="盈利能力",
        formula="(revenue - total_operating_cost) / revenue",
        typical_range="0.15-0.60",
        xt_fields=["gross_profit"],
    )
    net_profit_margin = _indicator(
        "indicator:net_profit_margin",
        "净利率",
        ["net_profit_margin", "净利率"],
        dimension="盈利能力",
        formula="net_profit / revenue",
        typical_range="0.05-0.30",
        xt_fields=["net_profit"],  # 注意：Pershareindex 的 net_profit 列实为净利率
    )
    roic = _indicator(
        "indicator:roic",
        "投入资本回报率 ROIC",
        ["roic", "投入资本回报率"],
        dimension="盈利能力",
        formula="息前税后利润 / 投入资本",
        typical_range="0.06-0.20",
    )
    roa = _indicator(
        "indicator:roa",
        "总资产收益率 ROA",
        ["roa", "总资产收益率", "资产回报率"],
        dimension="盈利能力",
        formula="net_profit / total_assets",
        typical_range="0.03-0.15",
    )
    # 杜邦三分解的子指标
    asset_turnover = _indicator(
        "indicator:asset_turnover",
        "资产周转率",
        ["asset_turnover", "资产周转率"],
        dimension="运营效率",
        formula="revenue / total_assets",
        typical_range="0.3-1.5",
    )
    equity_multiplier = _indicator(
        "indicator:equity_multiplier",
        "权益乘数",
        ["equity_multiplier", "权益乘数"],
        dimension="资本结构",
        formula="total_assets / total_equity",
        typical_range="1.5-4.0",
    )

    # ── 成长性族指标 ────────────────────────────────────────────────────
    net_profit_yoy = _indicator(
        "indicator:net_profit_yoy",
        "净利润同比增长率",
        ["net_profit_yoy", "净利润同比", "净利YoY"],
        dimension="成长性",
        formula="(本期净利 - 上年同期) / |上年同期|",
        typical_range="-0.5~1.0",
        xt_fields=["du_profit_rate"],
    )
    revenue_yoy = _indicator(
        "indicator:revenue_yoy",
        "营收同比增长率",
        ["revenue_yoy", "营收同比", "营收YoY"],
        dimension="成长性",
        formula="(本期营收 - 上年同期) / |上年同期|",
        typical_range="-0.3~0.8",
        xt_fields=["inc_revenue_rate"],
    )

    # ── 现金流族指标 ────────────────────────────────────────────────────
    ocf = _indicator(
        "indicator:ocf",
        "经营现金流净额",
        ["ocf", "经营现金流", "经营现金流净额"],
        dimension="现金流",
        formula="现金流量表净额",
        xt_fields=["net_cash_flows_oper_act"],
    )
    capex = _indicator(
        "indicator:capex",
        "资本支出",
        ["capex", "资本支出"],
        dimension="现金流",
        formula="购建固定资产支付现金",
        xt_fields=["cash_pay_acq_const_fiolta"],
    )
    ocf_per_share = _indicator(
        "indicator:ocf_per_share",
        "每股经营现金流",
        ["ocf_per_share", "每股经营现金流"],
        dimension="现金流",
        formula="Pershareindex 预计算值",
        xt_fields=["s_fa_ocfps"],
    )
    # 利润质量：OCF / 净利润 > 1 视为高质量
    earnings_quality = _indicator(
        "indicator:earnings_quality",
        "利润质量",
        ["earnings_quality", "利润质量", " earnings_quality"],
        dimension="现金流",
        formula="ocf / net_profit",
        typical_range=">1 视为高质量",
    )

    # ── 资本结构族指标 ──────────────────────────────────────────────────
    debt_to_assets = _indicator(
        "indicator:debt_to_assets",
        "资产负债率",
        ["debt_to_assets", "资产负债率"],
        dimension="资本结构",
        formula="total_liabilities / total_assets",
        typical_range="0.2-0.7",
        xt_fields=["gear_ratio"],
    )
    bps = _indicator(
        "indicator:bps",
        "每股净资产",
        ["bps", "每股净资产"],
        dimension="资本结构",
        formula="Pershareindex 预计算值",
        xt_fields=["s_fa_bps"],
    )

    # ── 规模族指标 ──────────────────────────────────────────────────────
    revenue = _indicator(
        "indicator:revenue",
        "营业收入",
        ["revenue", "营收", "营业收入"],
        dimension="规模",
        formula="利润表",
        xt_fields=["revenue_inc", "revenue"],
    )
    net_profit = _indicator(
        "indicator:net_profit",
        "净利润",
        ["net_profit", "净利润"],
        dimension="规模",
        formula="利润表",
        xt_fields=["net_profit_incl_min_int_inc"],
    )
    eps = _indicator(
        "indicator:eps",
        "每股收益",
        ["eps", "每股收益", "EPS"],
        dimension="规模",
        formula="利润表",
        xt_fields=["s_fa_eps_basic"],
    )
    research_expenses = _indicator(
        "indicator:research_expenses",
        "研发费用",
        ["research_expenses", "研发费用"],
        dimension="规模",
        formula="利润表",
        xt_fields=["research_expenses"],
    )
    total_equity = _indicator(
        "indicator:total_equity",
        "所有者权益",
        ["total_equity", "所有者权益", "净资产"],
        dimension="规模",
        formula="资产负债表",
        xt_fields=["total_equity", "tot_shrhldr_eqy_excl_min_int"],
    )
    total_assets = _indicator(
        "indicator:total_assets",
        "总资产",
        ["total_assets", "总资产"],
        dimension="规模",
        formula="资产负债表",
        xt_fields=["tot_assets"],
    )
    total_liabilities = _indicator(
        "indicator:total_liabilities",
        "总负债",
        ["total_liabilities", "总负债"],
        dimension="规模",
        formula="资产负债表",
        xt_fields=["tot_liab"],
    )

    indicator_nodes = [
        roe,
        roe_weighted,
        gross_margin,
        net_profit_margin,
        roic,
        roa,
        asset_turnover,
        equity_multiplier,
        net_profit_yoy,
        revenue_yoy,
        ocf,
        capex,
        ocf_per_share,
        earnings_quality,
        debt_to_assets,
        bps,
        revenue,
        net_profit,
        eps,
        research_expenses,
        total_equity,
        total_assets,
        total_liabilities,
    ]
    nodes.extend(indicator_nodes)

    # ── 杜邦分解（ROE = 净利率 × 资产周转率 × 权益乘数）───────────────
    edges.extend(
        [
            OntologyEdge(
                source_sid="indicator:roe",
                target_sid="indicator:net_profit_margin",
                relation_type=RelationType.DERIVED_FROM,
            ),
            OntologyEdge(
                source_sid="indicator:roe",
                target_sid="indicator:asset_turnover",
                relation_type=RelationType.DERIVED_FROM,
            ),
            OntologyEdge(
                source_sid="indicator:roe",
                target_sid="indicator:equity_multiplier",
                relation_type=RelationType.DERIVED_FROM,
            ),
        ]
    )

    # ── 指标派生关系 ────────────────────────────────────────────────────
    edges.extend(
        [
            # roe_weighted 是 roe 的加权版本，同源派生
            OntologyEdge(
                source_sid="indicator:roe_weighted",
                target_sid="indicator:roe",
                relation_type=RelationType.DERIVED_FROM,
            ),
            # 净利率派生自净利润 / 营收
            OntologyEdge(
                source_sid="indicator:net_profit_margin",
                target_sid="indicator:net_profit",
                relation_type=RelationType.DERIVED_FROM,
            ),
            OntologyEdge(
                source_sid="indicator:net_profit_margin",
                target_sid="indicator:revenue",
                relation_type=RelationType.DERIVED_FROM,
            ),
            # 毛利率派生自营收 - 营业成本
            OntologyEdge(
                source_sid="indicator:gross_margin",
                target_sid="indicator:revenue",
                relation_type=RelationType.DERIVED_FROM,
            ),
            # ROA 派生自净利润 / 总资产
            OntologyEdge(
                source_sid="indicator:roa",
                target_sid="indicator:net_profit",
                relation_type=RelationType.DERIVED_FROM,
            ),
            OntologyEdge(
                source_sid="indicator:roa",
                target_sid="indicator:total_assets",
                relation_type=RelationType.DERIVED_FROM,
            ),
            # 权益乘数派生自总资产 / 净资产
            OntologyEdge(
                source_sid="indicator:equity_multiplier",
                target_sid="indicator:total_assets",
                relation_type=RelationType.DERIVED_FROM,
            ),
            OntologyEdge(
                source_sid="indicator:equity_multiplier",
                target_sid="indicator:total_equity",
                relation_type=RelationType.DERIVED_FROM,
            ),
            # 资产负债率派生自总负债 / 总资产
            OntologyEdge(
                source_sid="indicator:debt_to_assets",
                target_sid="indicator:total_liabilities",
                relation_type=RelationType.DERIVED_FROM,
            ),
            OntologyEdge(
                source_sid="indicator:debt_to_assets",
                target_sid="indicator:total_assets",
                relation_type=RelationType.DERIVED_FROM,
            ),
            # 利润质量派生自 OCF / 净利润
            OntologyEdge(
                source_sid="indicator:earnings_quality",
                target_sid="indicator:ocf",
                relation_type=RelationType.DERIVED_FROM,
            ),
            OntologyEdge(
                source_sid="indicator:earnings_quality",
                target_sid="indicator:net_profit",
                relation_type=RelationType.DERIVED_FROM,
            ),
        ]
    )

    # ── 同维指标关联（SAME_DIMENSION）──────────────────────────────────
    edges.extend(
        [
            OntologyEdge(
                source_sid="indicator:roe",
                target_sid="indicator:roic",
                relation_type=RelationType.SAME_DIMENSION,
            ),
            OntologyEdge(
                source_sid="indicator:roe",
                target_sid="indicator:roa",
                relation_type=RelationType.SAME_DIMENSION,
            ),
            OntologyEdge(
                source_sid="indicator:roe",
                target_sid="indicator:roe_weighted",
                relation_type=RelationType.SAME_DIMENSION,
            ),
        ]
    )

    # ── 抽象概念族节点 + 归属边 ────────────────────────────────────────
    profitability = _concept(
        "concept:profitability",
        "盈利能力",
        ["盈利能力", "profitability"],
        fields=[
            "roe",
            "roe_weighted",
            "gross_margin",
            "net_profit_margin",
            "net_profit_yoy",
            "revenue_yoy",
        ],
        description="衡量公司赚取利润的能力，含杜邦分解三因子",
    )
    growth = _concept(
        "concept:growth",
        "成长性",
        ["成长性", "growth"],
        fields=["net_profit_yoy", "revenue_yoy"],
        description="衡量公司业绩增长速度",
    )
    cashflow = _concept(
        "concept:cashflow",
        "现金流",
        ["现金流", "cashflow"],
        fields=["ocf", "capex", "ocf_per_share", "earnings_quality"],
        description="衡量公司现金创造与运用能力",
    )
    capital_structure = _concept(
        "concept:capital_structure",
        "资本结构",
        ["资本结构", "capital_structure"],
        fields=[
            "debt_to_assets",
            "bps",
            "total_equity",
            "total_assets",
            "total_liabilities",
        ],
        description="衡量公司财务杠杆与偿债能力",
    )
    scale = _concept(
        "concept:scale",
        "规模",
        ["规模", "scale"],
        fields=[
            "revenue",
            "net_profit",
            "eps",
            "research_expenses",
            "total_equity",
            "total_assets",
            "total_liabilities",
        ],
        description="公司体量与绝对规模指标",
    )
    concept_nodes = [profitability, growth, cashflow, capital_structure, scale]
    nodes.extend(concept_nodes)

    # 指标 → 概念族归属
    profitability_members = [
        "indicator:roe",
        "indicator:roe_weighted",
        "indicator:gross_margin",
        "indicator:net_profit_margin",
        "indicator:roic",
        "indicator:roa",
    ]
    growth_members = ["indicator:net_profit_yoy", "indicator:revenue_yoy"]
    cashflow_members = [
        "indicator:ocf",
        "indicator:capex",
        "indicator:ocf_per_share",
        "indicator:earnings_quality",
    ]
    capital_members = [
        "indicator:debt_to_assets",
        "indicator:bps",
        "indicator:total_equity",
        "indicator:total_assets",
        "indicator:total_liabilities",
        "indicator:equity_multiplier",
    ]
    scale_members = [
        "indicator:revenue",
        "indicator:net_profit",
        "indicator:eps",
        "indicator:research_expenses",
    ]

    for sid in profitability_members:
        edges.append(
            OntologyEdge(sid, "concept:profitability", RelationType.RELATES_TO_CONCEPT)
        )
    for sid in growth_members:
        edges.append(
            OntologyEdge(sid, "concept:growth", RelationType.RELATES_TO_CONCEPT)
        )
    for sid in cashflow_members:
        edges.append(
            OntologyEdge(sid, "concept:cashflow", RelationType.RELATES_TO_CONCEPT)
        )
    for sid in capital_members:
        edges.append(
            OntologyEdge(
                sid, "concept:capital_structure", RelationType.RELATES_TO_CONCEPT
            )
        )
    for sid in scale_members:
        edges.append(
            OntologyEdge(sid, "concept:scale", RelationType.RELATES_TO_CONCEPT)
        )

    return nodes, edges
