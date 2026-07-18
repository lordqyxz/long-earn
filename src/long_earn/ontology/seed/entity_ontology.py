"""实体本体种子 — ADR-014。

定义 Universe（指数 / 板块）概念节点和主要行业概念节点。
公司实体节点不在种子里——它们由 ``OntologyRegistry.register_entity`` 在
运行时从 universe 成分股动态注册。

这些节点是 ``Connector`` 解析 "沪深300" / "大盘蓝筹" / "白酒行业" 等概念为
成分股列表 / entity sid 集合的依据。
"""

from __future__ import annotations

from long_earn.ontology.model import (
    OntologyDomain,
    OntologyEdge,
    OntologyNode,
    RelationType,
)


def _universe(
    sid: str, label: str, aliases: list[str], universe_type: str
) -> OntologyNode:
    """构造 Universe 概念节点。"""
    return OntologyNode(
        sid=sid,
        domain=OntologyDomain.CONCEPT,
        label=label,
        aliases=aliases,
        properties={
            "kind": "universe",
            "universe_type": universe_type,
            "resolution": {"aspect": "成分股", "universe_type": universe_type},
        },
    )


def _industry(sid: str, label: str, aliases: list[str]) -> OntologyNode:
    """构造行业概念节点。"""
    return OntologyNode(
        sid=sid,
        domain=OntologyDomain.CONCEPT,
        label=label,
        aliases=aliases,
        properties={"kind": "industry"},
    )


def build_entity_ontology() -> tuple[list[OntologyNode], list[OntologyEdge]]:
    """构建实体本体：Universe 概念 + 主要行业概念。

    边主要在运行时由 ``register_entity`` 建立（公司 → 行业 BELONGS_TO /
    公司 → Universe MEMBER_OF）。种子阶段只定义概念节点本身，
    以及行业间的上游/下游关系（PROPAGATES_TO，供事件传导推理）。
    """
    nodes: list[OntologyNode] = []
    edges: list[OntologyEdge] = []

    # ── Universe 概念（指数）──────────────────────────────────────────
    universes = [
        _universe(
            "concept:universe:csi300",
            "沪深300",
            ["csi300", "沪深300", "大盘蓝筹"],
            "csi300",
        ),
        _universe(
            "concept:universe:csi500",
            "中证500",
            ["csi500", "中证500", "中盘"],
            "csi500",
        ),
        _universe(
            "concept:universe:sse50", "上证50", ["sse50", "上证50", "超级大盘"], "sse50"
        ),
        _universe(
            "concept:universe:csi1000",
            "中证1000",
            ["csi1000", "中证1000", "小盘"],
            "csi1000",
        ),
        _universe(
            "concept:universe:all_a", "全A股", ["all_a", "全A股", "全A"], "all_a"
        ),
        _universe("concept:universe:etf", "沪深ETF", ["etf", "沪深ETF"], "etf"),
    ]
    nodes.extend(universes)

    # ── 板块概念 ────────────────────────────────────────────────────────
    boards = [
        _universe(
            "concept:universe:main_board",
            "沪市主板",
            ["main_board", "沪市主板"],
            "main_board",
        ),
        _universe(
            "concept:universe:star_board",
            "科创板",
            ["star_board", "科创板"],
            "star_board",
        ),
        _universe(
            "concept:universe:chinext",
            "创业板",
            ["chinext", "创业板", "gem"],
            "chinext",
        ),
        _universe("concept:universe:bse", "北交所", ["bse", "北交所"], "bse"),
        _universe(
            "concept:universe:szse_main",
            "深市主板",
            ["szse_main", "深市主板"],
            "szse_main",
        ),
    ]
    nodes.extend(boards)

    # ── 主要行业概念（申万一级简化版）────────────────────────────────
    industries = [
        _industry("concept:industry:bank", "银行", ["银行", "bank"]),
        _industry("concept:industry:liquor", "白酒", ["白酒", "liquor", "酒类"]),
        _industry(
            "concept:industry:pharmaceutical",
            "医药",
            ["医药", "pharmaceutical", "生物医药"],
        ),
        _industry(
            "concept:industry:electronics", "电子", ["电子", "electronics", "半导体"]
        ),
        _industry(
            "concept:industry:new_energy",
            "新能源",
            ["新能源", "new_energy", "光伏", "锂电"],
        ),
        _industry(
            "concept:industry:real_estate", "房地产", ["房地产", "real_estate", "地产"]
        ),
        _industry(
            "concept:industry:consumer", "消费", ["消费", "consumer", "食品饮料"]
        ),
        _industry(
            "concept:industry:finance",
            "非银金融",
            ["非银金融", "finance", "券商", "保险"],
        ),
        _industry(
            "concept:industry:manufacturing",
            "制造业",
            ["制造业", "manufacturing", "机械"],
        ),
        _industry(
            "concept:industry:technology",
            "科技",
            ["科技", "technology", "计算机", "软件"],
        ),
    ]
    nodes.extend(industries)

    # ── 行业间传导关系（事件影响推理用）──────────────────────────────
    # 锂价上涨 → 电池厂成本上升 → 新能源车成本上升
    edges.extend(
        [
            OntologyEdge(
                "concept:industry:electronics",
                "concept:industry:new_energy",
                RelationType.PROPAGATES_TO,
                metadata={"channel": "原材料供应"},
            ),
            # 地产下行 → 银行资产质量担忧
            OntologyEdge(
                "concept:industry:real_estate",
                "concept:industry:bank",
                RelationType.PROPAGATES_TO,
                metadata={"channel": "资产质量"},
            ),
            # 消费 → 制造业（需求传导）
            OntologyEdge(
                "concept:industry:consumer",
                "concept:industry:manufacturing",
                RelationType.CORRELATES_WITH,
                metadata={"channel": "需求传导"},
            ),
        ]
    )

    return nodes, edges
