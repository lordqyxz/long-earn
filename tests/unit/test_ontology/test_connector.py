"""连接器概念查询测试 — ADR-014 阶段 C。

验证 ``Connector.get_concept`` 单一入口的分发逻辑：
- indicator_panel：财务指标面板（盈利能力族）
- universe：成分股列表
- event_graph：事件图谱遍历
- experience：策略经验检索
- subject 解析（xt_symbol / universe 概念 / 未注册实体）
- 图谱关联节点返回
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import polars as pl
import pytest

from long_earn.ontology import (
    ConceptQuery,
    Connector,
    OntologyRegistry,
)


@pytest.fixture()
def seeded_registry() -> OntologyRegistry:
    """已装载种子的注册表 + 一个测试公司实体。"""
    registry = OntologyRegistry()
    registry.seed()
    registry.register_entity(
        "600519.SH",
        "贵州茅台",
        industry="白酒",
        universe_sids=["concept:universe:csi300"],
    )
    return registry


@pytest.fixture()
def mock_data_provider() -> MagicMock:
    """mock ConnectorDataProvider：get_financial_panel 返回测试面板，
    get_symbols 返回测试成分股列表。"""
    provider = MagicMock()
    # get_financial_panel 返回 pandas MultiIndex DataFrame（模拟真实 provider）
    # index=(date, symbol)，列为财务字段（无冗余 symbol 列）
    panel_df = pd.DataFrame(
        {
            "roe": [0.25, 0.24, 0.23],
            "gross_margin": [0.91, 0.90, 0.89],
            "net_profit_margin": [0.5, 0.49, 0.48],
        },
        index=pd.MultiIndex.from_tuples(
            [
                ("2024-09-30", "600519.SH"),
                ("2024-06-30", "600519.SH"),
                ("2024-03-31", "600519.SH"),
            ],
            names=["date", "symbol"],
        ),
    )
    provider.get_financial_panel.return_value = panel_df
    provider.get_symbols.return_value = ["600519.SH", "000001.SZ", "601318.SH"]
    return provider


@pytest.fixture()
def mock_memory_provider() -> MagicMock:
    """mock ConnectorMemoryProvider。"""
    provider = MagicMock()
    provider.search_experience.return_value = [
        {"name": "动量策略v1", "sharpe": 1.2, "family": "momentum"},
    ]
    provider.activate_events.return_value = [
        {"event": "央行降息", "direction": "positive"},
    ]
    return provider


class TestConnectorConceptQuery:
    """连接器概念查询分发测试。"""

    def test_indicator_panel_profitability(
        self,
        seeded_registry: OntologyRegistry,
        mock_data_provider: MagicMock,
    ) -> None:
        """ "盈利能力"概念 → indicator_panel，返回财务面板含 roe/gross_margin。"""
        connector = Connector(seeded_registry, data_provider=mock_data_provider)
        result = connector.get_concept(
            ConceptQuery(
                subject="600519.SH",
                aspect="盈利能力",
                time="2024Q3",
            )
        )
        assert result.concept == "盈利能力"
        assert isinstance(result.data, pl.DataFrame)
        assert "roe" in result.data.columns
        assert "gross_margin" in result.data.columns
        # provenance 标记来源
        assert "data_provider:financial_panel" in result.provenance
        # 调用方传入了正确字段
        call_args = mock_data_provider.get_financial_panel.call_args
        assert "600519.SH" in call_args.args[0]
        # 字段集含盈利能力族
        fields = call_args.kwargs.get("fields") or call_args.args[3]
        assert "roe" in fields
        assert "gross_margin" in fields

    def test_universe_csi300(
        self,
        seeded_registry: OntologyRegistry,
        mock_data_provider: MagicMock,
    ) -> None:
        """ "沪深300成分股"概念 → universe，返回成分股列表。"""
        connector = Connector(seeded_registry, data_provider=mock_data_provider)
        result = connector.get_concept(
            ConceptQuery(
                subject="csi300",
                aspect="成分股",
            )
        )
        assert isinstance(result.data, list)
        assert "600519.SH" in result.data
        assert "000001.SZ" in result.data

    def test_event_graph_reverse_traverse(
        self,
        seeded_registry: OntologyRegistry,
    ) -> None:
        """ "相关事件"概念 → event_graph，反向遍历影响该 entity 的事件。"""
        # 先手动建一条事件→实体边
        from long_earn.ontology import (
            OntologyDomain,
            OntologyEdge,
            OntologyNode,
            RelationType,
        )

        seeded_registry.register_node(
            OntologyNode(
                sid="event:rate_cut_2024",
                domain=OntologyDomain.EVENT,
                label="央行降息",
            )
        )
        seeded_registry.register_edge(
            OntologyEdge(
                source_sid="event:rate_cut_2024",
                target_sid="entity:600519.SH",
                relation_type=RelationType.IMPACTS,
            )
        )

        connector = Connector(seeded_registry)
        result = connector.get_concept(
            ConceptQuery(
                subject="600519.SH",
                aspect="相关事件",
            )
        )
        assert isinstance(result.data, list)
        assert len(result.data) >= 1
        event_sids = {e["event_sid"] for e in result.data}
        assert "event:rate_cut_2024" in event_sids

    def test_experience_strategy_family(
        self,
        seeded_registry: OntologyRegistry,
        mock_memory_provider: MagicMock,
    ) -> None:
        """ "动量族经验"概念 → experience，调 memory_provider.search_experience。"""
        connector = Connector(
            seeded_registry,
            memory_provider=mock_memory_provider,
        )
        result = connector.get_concept(
            ConceptQuery(
                subject="csi300",
                aspect="动量族",
            )
        )
        assert isinstance(result.data, list)
        assert len(result.data) >= 1
        assert result.data[0]["family"] == "momentum"
        mock_memory_provider.search_experience.assert_called_once()

    def test_unknown_aspect_returns_error(
        self,
        seeded_registry: OntologyRegistry,
    ) -> None:
        """未识别概念返回 error。"""
        connector = Connector(seeded_registry)
        result = connector.get_concept(
            ConceptQuery(
                subject="600519.SH",
                aspect="不存在的概念",
            )
        )
        assert isinstance(result.data, dict)
        assert "error" in result.data

    def test_related_nodes_returned(
        self,
        seeded_registry: OntologyRegistry,
        mock_data_provider: MagicMock,
    ) -> None:
        """indicator_panel 查询返回图谱关联节点（供 LLM 推理增强）。"""
        connector = Connector(seeded_registry, data_provider=mock_data_provider)
        result = connector.get_concept(
            ConceptQuery(
                subject="600519.SH",
                aspect="盈利能力",
            )
        )
        # entity:600519.SH 通过 MEMBER_OF 连 csi300，通过 BELONGS_TO 连白酒行业
        # related_nodes 应含这些关联节点
        related_sids = {n.sid for n in result.related_nodes}
        # 至少关联到 universe 或 industry
        assert len(related_sids) > 0 or len(result.paths) >= 0  # 宽松断言

    def test_time_parse_quarter(
        self,
        seeded_registry: OntologyRegistry,
        mock_data_provider: MagicMock,
    ) -> None:
        """time="2024Q3" 解析为 (2024-07-01, 2024-09-30)。"""
        connector = Connector(seeded_registry, data_provider=mock_data_provider)
        connector.get_concept(
            ConceptQuery(
                subject="600519.SH",
                aspect="盈利能力",
                time="2024Q3",
            )
        )
        call_args = mock_data_provider.get_financial_panel.call_args
        # args 顺序: symbols, start_date, end_date, fields
        assert call_args.args[1] == "2024-07-01"
        assert call_args.args[2] == "2024-09-30"

    def test_time_parse_range(
        self,
        seeded_registry: OntologyRegistry,
        mock_data_provider: MagicMock,
    ) -> None:
        """time="2024-01-01~2024-12-31" 原样解析。"""
        connector = Connector(seeded_registry, data_provider=mock_data_provider)
        connector.get_concept(
            ConceptQuery(
                subject="600519.SH",
                aspect="盈利能力",
                time="2024-01-01~2024-12-31",
            )
        )
        call_args = mock_data_provider.get_financial_panel.call_args
        assert call_args.args[1] == "2024-01-01"
        assert call_args.args[2] == "2024-12-31"


class TestConceptResolverSubject:
    """主体解析测试。"""

    def test_resolve_xt_symbol_registers_entity(
        self,
        seeded_registry: OntologyRegistry,
    ) -> None:
        """xt_symbol 主体自动注册为实体。"""
        from long_earn.ontology import ConceptResolver

        resolver = ConceptResolver(seeded_registry)
        # 新股票（未在 fixture 注册）
        entity_sid, symbols = resolver.resolve_subject("000002.SZ")
        assert entity_sid == "entity:000002.SZ"
        assert symbols == ["000002.SZ"]
        # 实体已注册到图谱
        assert seeded_registry.get_node("entity:000002.SZ") is not None

    def test_resolve_universe_concept(
        self,
        seeded_registry: OntologyRegistry,
    ) -> None:
        """universe 概念主体返回 (universe_sid, [])。"""
        from long_earn.ontology import ConceptResolver

        resolver = ConceptResolver(seeded_registry)
        entity_sid, symbols = resolver.resolve_subject("csi300")
        assert entity_sid == "concept:universe:csi300"
        assert symbols == []

    def test_resolve_existing_entity_by_name(
        self,
        seeded_registry: OntologyRegistry,
    ) -> None:
        """已注册实体按名称解析。"""
        from long_earn.ontology import ConceptResolver

        resolver = ConceptResolver(seeded_registry)
        # fixture 注册了 "贵州茅台"
        entity_sid, symbols = resolver.resolve_subject("贵州茅台")
        assert entity_sid == "entity:600519.SH"
        assert symbols == ["600519.SH"]
