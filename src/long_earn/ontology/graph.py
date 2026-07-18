"""本体论图谱 — ADR-014。

升级版 ``GraphIndex``，支持跨域遍历：

- 边是 ``OntologyEdge`` 对象（含 ``relation_type`` / ``visible_from``），非裸元组
- 支持按 ``relation_types`` / ``domain_filter`` 过滤（跨域查询）
- 支持反向遍历（``direction="reverse"``，查"哪些事件影响了我"）
- 支持 PIT 过滤（``visible_at`` 参数）
- 节点旁路索引支持按域过滤
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from long_earn.ontology.model import (
    OntologyDomain,
    OntologyEdge,
    OntologyNode,
    RelationType,
)

Direction = Literal["forward", "reverse", "both"]


@dataclass
class GraphPath:
    """图遍历到达一个节点的路径。"""

    sid: str
    node: OntologyNode | None  # 节点对象（若图谱已注册）
    path: list[str] = field(
        default_factory=list
    )  # 起点到此节点的 sid 序列（不含起点，含此节点）
    distance: int = 0  # 跳数
    weight: float = 1.0  # 路径权重连乘
    edges: list[OntologyEdge] = field(default_factory=list)  # 经过的边


class OntologyGraph:
    """本体论图谱 — 邻接表 + 反向邻接表 + 节点旁路索引。

    替代 ``substance/indices/graph.py`` 的 ``GraphIndex``。旧实现边是裸元组且无类型
    过滤 / 无反向遍历 API；本类为跨域本体推理提供基础。
    """

    def __init__(self) -> None:
        # source_sid -> [edge, ...]；反向用 _reverse
        self._adj: dict[str, list[OntologyEdge]] = defaultdict(list)
        self._reverse: dict[str, list[OntologyEdge]] = defaultdict(list)
        self._nodes: dict[str, OntologyNode] = {}

    # ── 节点管理 ────────────────────────────────────────────────────────

    def add_node(self, node: OntologyNode) -> None:
        """注册节点。若 sid 已存在则覆盖（以最新定义为准）。"""
        self._nodes[node.sid] = node

    def get_node(self, sid: str) -> OntologyNode | None:
        return self._nodes.get(sid)

    def has_node(self, sid: str) -> bool:
        return sid in self._nodes

    def nodes_by_domain(self, domain: OntologyDomain) -> list[OntologyNode]:
        """按域过滤节点。"""
        return [n for n in self._nodes.values() if n.domain == domain]

    def find_by_label_or_alias(self, term: str) -> OntologyNode | None:
        """通过 label 或 alias 模糊定位节点（大小写不敏感）。

        供 Connector 实体解析使用：用户传入 "贵州茅台" / "roe" 时定位节点。
        """
        for node in self._nodes.values():
            if node.matches_alias(term):
                return node
        return None

    # ── 边管理 ──────────────────────────────────────────────────────────

    def add_edge(self, edge: OntologyEdge) -> None:
        """添加边。同时写入正向与反向邻接表。

        不要求端点节点已注册（某些 substance 物质 sid 可能未显式 add_node），
        但 ``traverse`` 返回的 ``GraphPath.node`` 会是 None。
        """
        self._adj[edge.source_sid].append(edge)
        self._reverse[edge.target_sid].append(edge)

    def edge_count(self) -> int:
        return sum(len(edges) for edges in self._adj.values())

    def neighbors(
        self,
        sid: str,
        *,
        direction: Direction = "forward",
        relation_types: set[RelationType] | None = None,
        min_weight: float = 0.0,
    ) -> list[OntologyEdge]:
        """一跳邻居边（带类型 / 权重过滤）。"""
        result: list[OntologyEdge] = []
        seen: set[tuple[str, str, str]] = set()
        sources: list[OntologyEdge] = []
        if direction in ("forward", "both"):
            sources.extend(self._adj.get(sid, []))
        if direction in ("reverse", "both"):
            sources.extend(self._reverse.get(sid, []))
        for edge in sources:
            if edge.weight < min_weight:
                continue
            if relation_types is not None and edge.relation_type not in relation_types:
                continue
            key = (edge.source_sid, edge.target_sid, edge.relation_type.value)
            if key in seen:
                continue
            seen.add(key)
            result.append(edge)
        return result

    # ── 遍历 ────────────────────────────────────────────────────────────

    def traverse(  # noqa: PLR0913
        self,
        start_sid: str,
        *,
        max_depth: int = 2,
        min_weight: float = 0.1,
        relation_types: set[RelationType] | None = None,
        domain_filter: set[OntologyDomain] | None = None,
        direction: Direction = "forward",
        visible_at: datetime | None = None,
    ) -> list[GraphPath]:
        """广度优先遍历，返回带路径的关联节点。

        Args:
            start_sid: 起始节点 sid（不在结果中）
            max_depth: 最大遍历深度（跳数）
            min_weight: 最小边权重（低于此值的边不走过）
            relation_types: 只走这些类型的边（None = 全部）
            domain_filter: 只保留目标节点属于这些域的路径（None = 全部）
            direction: forward = 正向边，reverse = 反向边，both = 双向
            visible_at: PIT 时刻，边和节点都要可见（None = 不过滤）

        Returns:
            ``[GraphPath, ...]``，按权重降序排列
        """
        # visited[sid] = (path, weight, edges)
        visited: dict[str, tuple[list[str], float, list[OntologyEdge]]] = {
            start_sid: ([], 1.0, [])
        }
        queue: deque[tuple[str, list[str], float, list[OntologyEdge], int]] = deque(
            [(start_sid, [], 1.0, [], 0)]
        )

        while queue:
            current, path, path_weight, path_edges, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for edge in self._iter_edges(current, direction):
                if edge.weight < min_weight:
                    continue
                if (
                    relation_types is not None
                    and edge.relation_type not in relation_types
                ):
                    continue
                if visible_at is not None and not edge.is_visible_at(visible_at):
                    continue
                # 确定下一节点 sid（正向时是 target，反向时是 source）
                next_sid = (
                    edge.target_sid if edge.source_sid == current else edge.source_sid
                )
                # PIT 过滤下一节点（若节点已注册且 substance 契约要求）
                # OntologyNode 本身无 visible_from（那是 substance 的概念），
                # 此处仅过滤边；节点级 PIT 由调用方在消费时用 substance.is_visible_at
                new_weight = path_weight * edge.weight
                new_path = [*path, next_sid]
                new_edges = [*path_edges, edge]
                existing = visited.get(next_sid)
                if existing is None or new_weight > existing[1]:
                    visited[next_sid] = (new_path, new_weight, new_edges)
                    queue.append((next_sid, new_path, new_weight, new_edges, depth + 1))

        visited.pop(start_sid, None)

        results: list[GraphPath] = []
        for sid, (path, weight, edges) in visited.items():
            node = self._nodes.get(sid)
            if domain_filter is not None and (
                node is None or node.domain not in domain_filter
            ):
                continue
            results.append(
                GraphPath(
                    sid=sid,
                    node=node,
                    path=path,
                    distance=len(path),
                    weight=weight,
                    edges=edges,
                )
            )
        results.sort(key=lambda p: p.weight, reverse=True)
        return results

    def resolve_concept(self, concept_sid: str) -> set[str]:
        """展开抽象概念为成员 sid 集合。

        概念节点通过 ``RELATES_TO_CONCEPT`` 边连接成员，本方法返回所有指向
        ``concept_sid`` 的边的 source sid（即"属于该概念的成员"）。
        """
        members: set[str] = set()
        for edge in self._reverse.get(concept_sid, []):
            if edge.relation_type == RelationType.RELATES_TO_CONCEPT:
                members.add(edge.source_sid)
        return members

    def find_path(
        self,
        source_sid: str,
        target_sid: str,
        max_depth: int = 4,
    ) -> list[str] | None:
        """两点间最短路径（BFS，用于溯源）。返回 sid 序列（含端点），无路径返回 None。"""
        if source_sid == target_sid:
            return [source_sid]
        visited: dict[str, list[str]] = {source_sid: [source_sid]}
        queue: deque[tuple[str, list[str], int]] = deque(
            [(source_sid, [source_sid], 0)]
        )
        while queue:
            current, path, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for edge in self._iter_edges(current, "forward"):
                next_sid = (
                    edge.target_sid if edge.source_sid == current else edge.source_sid
                )
                if next_sid in visited:
                    continue
                new_path = [*path, next_sid]
                if next_sid == target_sid:
                    return new_path
                visited[next_sid] = new_path
                queue.append((next_sid, new_path, depth + 1))
        return None

    def clear(self) -> None:
        """清空图谱。"""
        self._adj.clear()
        self._reverse.clear()
        self._nodes.clear()

    # ── 私有 ────────────────────────────────────────────────────────────

    def _iter_edges(self, sid: str, direction: Direction) -> list[OntologyEdge]:
        """按方向枚举从 sid 出发的边。both 时正向 + 反向合并去重。"""
        if direction == "forward":
            return list(self._adj.get(sid, []))
        if direction == "reverse":
            return list(self._reverse.get(sid, []))
        # both
        merged: list[OntologyEdge] = []
        merged.extend(self._adj.get(sid, []))
        merged.extend(self._reverse.get(sid, []))
        return merged
