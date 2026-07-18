"""本体论注册表 — ADR-014。

``OntologyRegistry`` 是节点与边的类型/唯一性校验入口，也是种子数据的装载点。

职责：
- 注册节点（sid 唯一性 + domain 合法）
- 校验边（relation_type 在 ``RelationType`` enum 内 + 端点已注册）
- 装载种子本体（``ontology/seed/`` 下各领域本体定义）
- 对外提供查询（按 sid / alias / domain 查节点）

与 ``OntologyGraph`` 的分工：``OntologyGraph`` 是纯粹的图数据结构（邻接表 + 遍历），
``OntologyRegistry`` 是规则层（校验 + 种子装载 + 单一注册入口）。
``OntologyRegistry`` 内部持有一个 ``OntologyGraph`` 实例，所有 add 操作经校验后落图。
"""

from __future__ import annotations

from loguru import logger

from long_earn.ontology.graph import OntologyGraph
from long_earn.ontology.model import (
    OntologyDomain,
    OntologyEdge,
    OntologyNode,
    RelationType,
)


class OntologyRegistry:
    """本体论注册表 — 单进程内本体图谱的唯一注册入口。

    用法：
        registry = OntologyRegistry()
        registry.seed()                      # 装载种子本体
        registry.register_node(my_node)
        registry.register_edge(my_edge)
        graph = registry.graph               # 获取图谱用于遍历
    """

    def __init__(self) -> None:
        self._graph: OntologyGraph = OntologyGraph()
        self._seeded: bool = False

    @property
    def graph(self) -> OntologyGraph:
        """底层图谱（只读视图，外部不应通过此句柄直接 add）。"""
        return self._graph

    # ── 种子装载 ────────────────────────────────────────────────────────

    def seed(self, *, force: bool = False) -> None:
        """装载本体种子数据（财务指标 / 实体 / 策略族 / 事件类型）。

        幂等：默认只装载一次；``force=True`` 时清空重装。
        """
        if self._seeded and not force:
            return
        if force:
            self._graph.clear()
        # 延迟 import 避免循环依赖（seed 模块可能 import registry 做类型标注）
        from long_earn.ontology.seed import (  # noqa: PLC0415
            build_entity_ontology,
            build_event_ontology,
            build_financial_ontology,
            build_strategy_ontology,
        )

        for builder in (
            build_financial_ontology,
            build_entity_ontology,
            build_strategy_ontology,
            build_event_ontology,
        ):
            nodes, edges = builder()
            for node in nodes:
                self._register_node_internal(node)
            for edge in edges:
                self._register_edge_internal(edge, skip_endpoint_check=True)

        self._seeded = True
        logger.info(
            f"本体种子装载完成: {len(self._graph._nodes)} 节点, "
            f"{self._graph.edge_count()} 边"
        )

    # ── 节点注册 ────────────────────────────────────────────────────────

    def register_node(self, node: OntologyNode) -> None:
        """注册节点（经校验）。"""
        self._register_node_internal(node)

    def _register_node_internal(self, node: OntologyNode) -> None:
        if not node.sid:
            raise ValueError("OntologyNode.sid 不能为空")
        if not isinstance(node.domain, OntologyDomain):
            raise ValueError(f"OntologyNode.domain 非法: {node.domain!r}")
        self._graph.add_node(node)

    # ── 边注册 ──────────────────────────────────────────────────────────

    def register_edge(self, edge: OntologyEdge) -> None:
        """注册边（经校验：端点已注册 + relation_type 合法 + 自环检查）。"""
        self._register_edge_internal(edge, skip_endpoint_check=False)

    def _register_edge_internal(
        self,
        edge: OntologyEdge,
        *,
        skip_endpoint_check: bool = False,
    ) -> None:
        if not isinstance(edge.relation_type, RelationType):
            raise ValueError(
                f"OntologyEdge.relation_type 非法: {edge.relation_type!r}，"
                f"必须是 RelationType enum 成员"
            )
        if edge.source_sid == edge.target_sid:
            raise ValueError(f"自环边不允许: source==target=={edge.source_sid}")
        if not skip_endpoint_check:
            # 种子数据允许端点未显式注册（跨 builder 引用），运行时注册则要求端点存在
            if not self._graph.has_node(edge.source_sid):
                raise ValueError(
                    f"边端点未注册: source_sid={edge.source_sid} (请先 register_node)"
                )
            if not self._graph.has_node(edge.target_sid):
                raise ValueError(
                    f"边端点未注册: target_sid={edge.target_sid} (请先 register_node)"
                )
        self._graph.add_edge(edge)

    # ── 查询代理 ────────────────────────────────────────────────────────

    def get_node(self, sid: str) -> OntologyNode | None:
        return self._graph.get_node(sid)

    def find_by_label_or_alias(self, term: str) -> OntologyNode | None:
        return self._graph.find_by_label_or_alias(term)

    def nodes_by_domain(self, domain: OntologyDomain) -> list[OntologyNode]:
        return self._graph.nodes_by_domain(domain)

    def resolve_concept(self, concept_sid: str) -> set[str]:
        """展开抽象概念为成员 sid 集合。"""
        return self._graph.resolve_concept(concept_sid)

    # ── 实体动态注册 ────────────────────────────────────────────────────

    def register_entity(
        self,
        xt_symbol: str,
        name: str,
        industry: str = "",
        sector: str = "",
        *,
        universe_sids: list[str] | None = None,
    ) -> str:
        """动态注册公司实体节点 + 自动建 BELONGS_TO / MEMBER_OF 边。

        Args:
            xt_symbol: xtquant 格式代码（如 ``600519.SH``）
            name: 公司名（如 ``贵州茅台``）
            industry: 所属行业 sid 或可读名（如 ``白酒``）
            sector: 板块（如 ``沪市主板``）
            universe_sids: 该实体所属的 universe 概念 sid 列表

        Returns:
            实体节点 sid（``entity:{xt_symbol}``）
        """
        entity_sid = f"entity:{xt_symbol}"
        node = OntologyNode(
            sid=entity_sid,
            domain=OntologyDomain.ENTITY,
            label=name,
            aliases=[xt_symbol, name],
            properties={
                "xt_symbol": xt_symbol,
                "industry": industry,
                "sector": sector,
            },
        )
        self._register_node_internal(node)

        # 公司 → 行业（BELONGS_TO）
        if industry:
            industry_sid = (
                industry
                if industry.startswith("concept:industry:")
                else f"concept:industry:{industry}"
            )
            # 若行业节点不存在，先注册为概念节点
            if not self._graph.has_node(industry_sid):
                self._register_node_internal(
                    OntologyNode(
                        sid=industry_sid,
                        domain=OntologyDomain.CONCEPT,
                        label=industry,
                        aliases=[industry],
                        properties={"kind": "industry"},
                    )
                )
            self._register_edge_internal(
                OntologyEdge(
                    source_sid=entity_sid,
                    target_sid=industry_sid,
                    relation_type=RelationType.BELONGS_TO,
                    provenance="dynamic",
                )
            )

        # 公司 → Universe（MEMBER_OF）
        for universe_sid in universe_sids or []:
            if not self._graph.has_node(universe_sid):
                continue
            self._register_edge_internal(
                OntologyEdge(
                    source_sid=entity_sid,
                    target_sid=universe_sid,
                    relation_type=RelationType.MEMBER_OF,
                    provenance="dynamic",
                )
            )

        return entity_sid
