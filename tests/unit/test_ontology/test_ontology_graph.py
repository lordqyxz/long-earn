"""本体论图谱与注册表测试 — ADR-014。

测试覆盖接口层与系统关键环节：
- OntologyGraph 遍历能力（类型过滤 / 反向 / PIT / 域过滤）
- OntologyRegistry 种子装载 + 校验拦截
- 概念解析与路径查找
"""

from __future__ import annotations

from datetime import datetime

import pytest

from long_earn.ontology import (
    OntologyDomain,
    OntologyEdge,
    OntologyGraph,
    OntologyNode,
    OntologyRegistry,
    RelationType,
)

# ── OntologyGraph 基础遍历 ──────────────────────────────────────────────


class TestOntologyGraph:
    """图谱遍历能力测试。"""

    def test_traverse_forward_returns_paths(self) -> None:
        """正向遍历返回带路径与权重的结果。"""
        graph = OntologyGraph()
        graph.add_node(OntologyNode(sid="a", domain=OntologyDomain.ENTITY, label="A"))
        graph.add_node(
            OntologyNode(sid="b", domain=OntologyDomain.INDICATOR, label="B")
        )
        graph.add_node(
            OntologyNode(sid="c", domain=OntologyDomain.INDICATOR, label="C")
        )
        graph.add_edge(
            OntologyEdge(
                source_sid="a",
                target_sid="b",
                relation_type=RelationType.REPORTS_INDICATOR,
                weight=0.9,
            )
        )
        graph.add_edge(
            OntologyEdge(
                source_sid="b",
                target_sid="c",
                relation_type=RelationType.DERIVED_FROM,
                weight=0.8,
            )
        )

        paths = graph.traverse("a", max_depth=2, min_weight=0.0)
        sids = {p.sid for p in paths}
        assert sids == {"b", "c"}
        b_path = next(p for p in paths if p.sid == "b")
        c_path = next(p for p in paths if p.sid == "c")
        assert b_path.weight == pytest.approx(0.9)
        assert c_path.weight == pytest.approx(0.9 * 0.8)
        assert c_path.distance == 2
        assert c_path.path == ["b", "c"]

    def test_traverse_relation_type_filter(self) -> None:
        """relation_types 过滤只走指定类型的边。"""
        graph = OntologyGraph()
        graph.add_node(OntologyNode(sid="a", domain=OntologyDomain.ENTITY, label="A"))
        graph.add_node(
            OntologyNode(sid="b", domain=OntologyDomain.INDICATOR, label="B")
        )
        graph.add_node(OntologyNode(sid="c", domain=OntologyDomain.EVENT, label="C"))
        graph.add_edge(
            OntologyEdge(
                source_sid="a",
                target_sid="b",
                relation_type=RelationType.REPORTS_INDICATOR,
            )
        )
        graph.add_edge(
            OntologyEdge(
                source_sid="a", target_sid="c", relation_type=RelationType.IMPACTS
            )
        )

        paths = graph.traverse(
            "a",
            max_depth=1,
            min_weight=0.0,
            relation_types={RelationType.IMPACTS},
        )
        assert {p.sid for p in paths} == {"c"}

    def test_traverse_domain_filter(self) -> None:
        """domain_filter 只保留目标节点属于指定域的路径。"""
        graph = OntologyGraph()
        graph.add_node(OntologyNode(sid="a", domain=OntologyDomain.ENTITY, label="A"))
        graph.add_node(
            OntologyNode(sid="b", domain=OntologyDomain.INDICATOR, label="B")
        )
        graph.add_node(OntologyNode(sid="c", domain=OntologyDomain.EVENT, label="C"))
        graph.add_edge(
            OntologyEdge(
                source_sid="a",
                target_sid="b",
                relation_type=RelationType.REPORTS_INDICATOR,
            )
        )
        graph.add_edge(
            OntologyEdge(
                source_sid="a", target_sid="c", relation_type=RelationType.IMPACTS
            )
        )

        paths = graph.traverse(
            "a",
            max_depth=1,
            min_weight=0.0,
            domain_filter={OntologyDomain.INDICATOR},
        )
        assert {p.sid for p in paths} == {"b"}

    def test_traverse_reverse_direction(self) -> None:
        """反向遍历：从 target 回溯到 source。"""
        graph = OntologyGraph()
        graph.add_node(
            OntologyNode(sid="entity", domain=OntologyDomain.ENTITY, label="E")
        )
        graph.add_node(
            OntologyNode(sid="event1", domain=OntologyDomain.EVENT, label="Ev1")
        )
        graph.add_node(
            OntologyNode(sid="event2", domain=OntologyDomain.EVENT, label="Ev2")
        )
        graph.add_edge(
            OntologyEdge(
                source_sid="event1",
                target_sid="entity",
                relation_type=RelationType.IMPACTS,
            )
        )
        graph.add_edge(
            OntologyEdge(
                source_sid="event2",
                target_sid="entity",
                relation_type=RelationType.IMPACTS,
            )
        )

        # 从 entity 反向找哪些事件影响它
        paths = graph.traverse(
            "entity", max_depth=1, min_weight=0.0, direction="reverse"
        )
        assert {p.sid for p in paths} == {"event1", "event2"}

    def test_traverse_visible_at_filters_edges(self) -> None:
        """PIT 过滤：visible_from 晚于 visible_at 的边不走过。"""
        graph = OntologyGraph()
        graph.add_node(OntologyNode(sid="a", domain=OntologyDomain.ENTITY, label="A"))
        graph.add_node(
            OntologyNode(sid="b", domain=OntologyDomain.INDICATOR, label="B")
        )
        # 边在 2025-01-01 才可见
        graph.add_edge(
            OntologyEdge(
                source_sid="a",
                target_sid="b",
                relation_type=RelationType.REPORTS_INDICATOR,
                visible_from=datetime(2025, 1, 1),
            )
        )

        # 2024 年查询：边不可见
        paths_before = graph.traverse(
            "a",
            max_depth=1,
            min_weight=0.0,
            visible_at=datetime(2024, 6, 1),
        )
        assert paths_before == []

        # 2025 年查询：边可见
        paths_after = graph.traverse(
            "a",
            max_depth=1,
            min_weight=0.0,
            visible_at=datetime(2025, 6, 1),
        )
        assert {p.sid for p in paths_after} == {"b"}

    def test_resolve_concept_returns_members(self) -> None:
        """resolve_concept 展开抽象概念为成员 sid 集合。"""
        graph = OntologyGraph()
        graph.add_node(
            OntologyNode(
                sid="concept:profitability",
                domain=OntologyDomain.CONCEPT,
                label="盈利能力",
            )
        )
        graph.add_node(
            OntologyNode(
                sid="indicator:roe", domain=OntologyDomain.INDICATOR, label="ROE"
            )
        )
        graph.add_node(
            OntologyNode(
                sid="indicator:roa", domain=OntologyDomain.INDICATOR, label="ROA"
            )
        )
        graph.add_edge(
            OntologyEdge(
                source_sid="indicator:roe",
                target_sid="concept:profitability",
                relation_type=RelationType.RELATES_TO_CONCEPT,
            )
        )
        graph.add_edge(
            OntologyEdge(
                source_sid="indicator:roa",
                target_sid="concept:profitability",
                relation_type=RelationType.RELATES_TO_CONCEPT,
            )
        )

        members = graph.resolve_concept("concept:profitability")
        assert members == {"indicator:roe", "indicator:roa"}

    def test_find_path_returns_shortest(self) -> None:
        """find_path 返回两点间最短路径。"""
        graph = OntologyGraph()
        graph.add_node(OntologyNode(sid="a", domain=OntologyDomain.ENTITY, label="A"))
        graph.add_node(
            OntologyNode(sid="b", domain=OntologyDomain.INDICATOR, label="B")
        )
        graph.add_node(
            OntologyNode(sid="c", domain=OntologyDomain.INDICATOR, label="C")
        )
        graph.add_edge(
            OntologyEdge(
                source_sid="a",
                target_sid="b",
                relation_type=RelationType.REPORTS_INDICATOR,
            )
        )
        graph.add_edge(
            OntologyEdge(
                source_sid="b", target_sid="c", relation_type=RelationType.DERIVED_FROM
            )
        )

        path = graph.find_path("a", "c", max_depth=4)
        assert path == ["a", "b", "c"]

        no_path = graph.find_path("c", "a", max_depth=4)
        assert no_path is None  # 无反向边

    def test_find_by_label_or_alias(self) -> None:
        """find_by_label_or_alias 大小写不敏感匹配 label 与 alias。"""
        graph = OntologyGraph()
        graph.add_node(
            OntologyNode(
                sid="indicator:roe",
                domain=OntologyDomain.INDICATOR,
                label="净资产收益率 ROE",
                aliases=["roe", "ReturnOnEquity"],
            )
        )
        assert graph.find_by_label_or_alias("roe") is not None
        assert graph.find_by_label_or_alias("ROE") is not None
        assert graph.find_by_label_or_alias("净资产收益率 ROE") is not None
        assert graph.find_by_label_or_alias("不存在") is None


# ── OntologyRegistry 校验与种子装载 ────────────────────────────────────


class TestOntologyRegistry:
    """注册表校验与种子装载测试。"""

    def test_seed_loads_financial_indicators(self) -> None:
        """种子装载后财务指标节点存在。"""
        registry = OntologyRegistry()
        registry.seed()
        roe = registry.get_node("indicator:roe")
        assert roe is not None
        assert roe.domain == OntologyDomain.INDICATOR
        profitability = registry.get_node("concept:profitability")
        assert profitability is not None
        assert profitability.domain == OntologyDomain.CONCEPT

    def test_seed_is_idempotent(self) -> None:
        """seed 幂等：重复调用不重复装载。"""
        registry = OntologyRegistry()
        registry.seed()
        count1 = len(registry.graph._nodes)
        registry.seed()
        count2 = len(registry.graph._nodes)
        assert count1 == count2

    def test_register_edge_rejects_invalid_relation_type(self) -> None:
        """register_edge 拒绝非 RelationType enum 的 relation_type。"""
        registry = OntologyRegistry()
        registry.register_node(
            OntologyNode(sid="a", domain=OntologyDomain.ENTITY, label="A")
        )
        registry.register_node(
            OntologyNode(sid="b", domain=OntologyDomain.INDICATOR, label="B")
        )
        # 构造一个 relation_type 不合法的边（绕过 enum 校验）
        bad_edge = OntologyEdge.model_construct(
            source_sid="a",
            target_sid="b",
            relation_type="not_a_relation",
        )
        with pytest.raises(ValueError, match="relation_type"):
            registry.register_edge(bad_edge)  # type: ignore[arg-type]

    def test_register_edge_rejects_self_loop(self) -> None:
        """register_edge 拒绝自环边。"""
        registry = OntologyRegistry()
        registry.register_node(
            OntologyNode(sid="a", domain=OntologyDomain.ENTITY, label="A")
        )
        with pytest.raises(ValueError, match="自环"):
            registry.register_edge(
                OntologyEdge(
                    source_sid="a",
                    target_sid="a",
                    relation_type=RelationType.CORRELATES_WITH,
                )
            )

    def test_register_edge_rejects_unregistered_endpoint(self) -> None:
        """register_edge 拒绝端点未注册的边（运行时注册场景）。"""
        registry = OntologyRegistry()
        registry.register_node(
            OntologyNode(sid="a", domain=OntologyDomain.ENTITY, label="A")
        )
        # b 未注册
        with pytest.raises(ValueError, match="端点未注册"):
            registry.register_edge(
                OntologyEdge(
                    source_sid="a",
                    target_sid="b",
                    relation_type=RelationType.REPORTS_INDICATOR,
                )
            )

    def test_register_entity_auto_builds_belongs_to(self) -> None:
        """register_entity 自动建立公司→行业 BELONGS_TO 边。"""
        registry = OntologyRegistry()
        registry.seed()
        entity_sid = registry.register_entity(
            "600519.SH",
            "贵州茅台",
            industry="白酒",
        )
        assert entity_sid == "entity:600519.SH"
        # 公司节点存在
        assert registry.get_node(entity_sid) is not None
        # 行业概念节点自动创建
        assert registry.get_node("concept:industry:白酒") is not None
        # BELONGS_TO 边存在
        edges = registry.graph.neighbors(
            entity_sid,
            relation_types={RelationType.BELONGS_TO},
        )
        assert any(e.target_sid == "concept:industry:白酒" for e in edges)

    def test_register_entity_links_to_universe(self) -> None:
        """register_entity 指定 universe_sids 时自动建 MEMBER_OF 边。"""
        registry = OntologyRegistry()
        registry.seed()
        registry.register_entity(
            "600519.SH",
            "贵州茅台",
            industry="白酒",
            universe_sids=["concept:universe:csi300"],
        )
        edges = registry.graph.neighbors(
            "entity:600519.SH",
            relation_types={RelationType.MEMBER_OF},
        )
        assert any(e.target_sid == "concept:universe:csi300" for e in edges)

    def test_seed_builds_dupont_derived_from_edges(self) -> None:
        """种子装载后杜邦分解边存在：ROE → 净利率 / 资产周转率 / 权益乘数。"""
        registry = OntologyRegistry()
        registry.seed()
        edges = registry.graph.neighbors(
            "indicator:roe",
            relation_types={RelationType.DERIVED_FROM},
        )
        targets = {e.target_sid for e in edges}
        assert "indicator:net_profit_margin" in targets
        assert "indicator:asset_turnover" in targets
        assert "indicator:equity_multiplier" in targets

    def test_seed_builds_event_type_sensitive_indicator_edges(self) -> None:
        """种子装载后事件类型→敏感指标边存在。"""
        registry = OntologyRegistry()
        registry.seed()
        edges = registry.graph.neighbors(
            "event_type:macro_policy",
            relation_types={RelationType.RELATES_TO_CONCEPT},
        )
        targets = {e.target_sid for e in edges}
        assert "indicator:debt_to_assets" in targets

    def test_resolve_concept_returns_financial_family_members(self) -> None:
        """resolve_concept 展开盈利能力族成员。"""
        registry = OntologyRegistry()
        registry.seed()
        members = registry.resolve_concept("concept:profitability")
        assert "indicator:roe" in members
        assert "indicator:gross_margin" in members
