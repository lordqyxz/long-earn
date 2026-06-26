"""GraphIndex — 波态检索，基于 dict 邻接表的知识关系图。

替代旧 RelationGraph 的 numpy 矩阵方案：
- 无 400MB 预分配，内存按实际边数线性增长
- 无容量上限
- BFS 返回完整路径 (sid, path, distance, weight)
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any


class GraphIndex:
    """图索引 — 邻接表存储 substance 间的关系边。

    边的权值/类型/provenance 从对应 relation 形态的 Substance 读取，
    本索引只维护 source_id → [(target_id, relation_sid, weight)] 的映射。
    """

    def __init__(self) -> None:
        self._adj: dict[str, list[tuple[str, str, float]]] = defaultdict(list)
        self._reverse: dict[str, list[tuple[str, str, float]]] = defaultdict(list)

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        relation_sid: str = "",
        weight: float = 1.0,
    ) -> None:
        """添加关系边。

        Args:
            source_id: 源物质 sid
            target_id: 目标物质 sid
            relation_sid: 对应 relation 物质的 sid（用于读取元数据）
            weight: 边权重 (0-1)
        """
        self._adj[source_id].append((target_id, relation_sid, weight))
        self._reverse[target_id].append((source_id, relation_sid, weight))

    def neighbors(self, sid: str, min_weight: float = 0.0) -> list[tuple[str, float]]:
        """获取直接邻居（一跳）。

        Returns:
            [(target_sid, weight), ...]
        """
        return [(tgt, w) for tgt, _, w in self._adj.get(sid, []) if w >= min_weight]

    def bfs(
        self,
        start_sid: str,
        max_depth: int = 2,
        min_weight: float = 0.1,
    ) -> list[dict[str, Any]]:
        """广度优先遍历，返回带路径的关联节点。

        Args:
            start_sid: 起始物质 sid
            max_depth: 最大遍历深度
            min_weight: 最小边权重

        Returns:
            [{sid, path, distance, weight}, ...]，不含起点
        """
        visited: dict[str, tuple[list[str], float]] = {start_sid: ([], 1.0)}
        queue: deque[tuple[str, list[str], float, int]] = deque(
            [(start_sid, [], 1.0, 0)]
        )

        while queue:
            current, path, path_weight, depth = queue.popleft()
            if depth >= max_depth:
                continue
            for tgt, _rel_sid, w in self._adj.get(current, []):
                if w < min_weight:
                    continue
                new_weight = path_weight * w
                new_path = [*path, tgt]
                if tgt not in visited or new_weight > visited[tgt][1]:
                    visited[tgt] = (new_path, new_weight)
                    queue.append((tgt, new_path, new_weight, depth + 1))

        visited.pop(start_sid, None)
        return [
            {"sid": sid, "path": info[0], "distance": len(info[0]), "weight": info[1]}
            for sid, info in visited.items()
        ]

    def edge_count(self) -> int:
        """总边数。"""
        return sum(len(edges) for edges in self._adj.values())

    def clear(self) -> None:
        """清空索引。"""
        self._adj.clear()
        self._reverse.clear()
