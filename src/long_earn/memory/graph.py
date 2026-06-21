"""关系图存储 — 基于 numpy 邻接矩阵的知识图谱

存储实体之间的语义关系，支持关联查询和路径遍历。
"""

import numpy as np
import pandas as pd


class RelationGraph:
    """知识关系图 — 存储实体间的关系边

    使用 numpy 邻接矩阵存储实体间的加权关系边。
    每个节点是一个知识实体（事实、策略、经验等）。
    """

    def __init__(self, max_nodes: int = 10000):
        self.max_nodes = max_nodes
        self._nodes: list[str] = []  # 节点 ID 列表
        self._node_attrs: list[dict] = []  # 节点属性
        self._adj_matrix = np.zeros((max_nodes, max_nodes), dtype=np.float32)
        self._current_size = 0

    @property
    def node_count(self) -> int:
        return self._current_size

    @property
    def nodes(self) -> list[str]:
        return self._nodes[: self._current_size]

    def add_node(self, node_id: str, **attrs) -> int:
        """添加节点，返回节点索引

        Args:
            node_id: 节点标识符
            **attrs: 节点属性（type, category, created_at 等）

        Returns:
            节点索引
        """
        # 检查是否已存在
        for i, nid in enumerate(self._nodes[: self._current_size]):
            if nid == node_id:
                self._node_attrs[i].update(attrs)
                return i

        if self._current_size >= self.max_nodes:
            raise ValueError(f"节点数量已达上限: {self.max_nodes}")

        idx = self._current_size
        self._nodes.append(node_id)
        self._node_attrs.append(attrs)
        self._current_size += 1
        return idx

    def add_edge(self, source: str, target: str, weight: float = 1.0) -> None:
        """添加关系边

        Args:
            source: 源节点 ID
            target: 目标节点 ID
            weight: 关系权重 (0-1)
        """
        src_idx = self._get_or_create_index(source)
        tgt_idx = self._get_or_create_index(target)
        self._adj_matrix[src_idx, tgt_idx] = max(
            self._adj_matrix[src_idx, tgt_idx], weight
        )

    def _get_or_create_index(self, node_id: str) -> int:
        """获取或创建节点索引"""
        for i, nid in enumerate(self._nodes[: self._current_size]):
            if nid == node_id:
                return i
        return self.add_node(node_id)

    def get_neighbors(
        self, node_id: str, min_weight: float = 0.0, top_k: int = 10
    ) -> list[tuple[str, float]]:
        """获取节点的邻居

        Args:
            node_id: 节点 ID
            min_weight: 最小权重过滤
            top_k: 最多返回 K 个

        Returns:
            [(node_id, weight), ...]
        """
        idx = self._find_index(node_id)
        if idx is None:
            return []

        row = self._adj_matrix[idx, : self._current_size]
        mask = row > min_weight
        indices = np.argsort(row[mask])[::-1][:top_k]
        neighbor_indices = np.where(mask)[0][indices]

        return [
            (self._nodes[i], float(self._adj_matrix[idx, i])) for i in neighbor_indices
        ]

    def get_related(
        self, node_id: str, depth: int = 2, min_weight: float = 0.1
    ) -> list[str]:
        """获取多跳关联节点（BFS）

        Args:
            node_id: 起始节点 ID
            depth: 最大遍历深度
            min_weight: 最小边权重

        Returns:
            关联节点 ID 列表
        """
        start_idx = self._find_index(node_id)
        if start_idx is None:
            return []

        visited = {start_idx}
        frontier = {start_idx}

        for _ in range(depth):
            next_frontier: set[int] = set()
            for u in frontier:
                row = self._adj_matrix[u, : self._current_size]
                for v in np.where(row > min_weight)[0]:
                    if v not in visited:
                        visited.add(v)
                        next_frontier.add(int(v))
            frontier = next_frontier
            if not frontier:
                break

        visited.discard(start_idx)
        return [self._nodes[i] for i in visited]

    def _find_index(self, node_id: str) -> int | None:
        """查找节点索引"""
        for i, nid in enumerate(self._nodes[: self._current_size]):
            if nid == node_id:
                return i
        return None

    def to_dataframe(self) -> pd.DataFrame:
        """导出为 DataFrame"""
        rows = []
        for i in range(self._current_size):
            for j in range(self._current_size):
                w = self._adj_matrix[i, j]
                if w > 0:
                    rows.append(
                        {
                            "source": self._nodes[i],
                            "target": self._nodes[j],
                            "weight": w,
                        }
                    )
        return pd.DataFrame(rows)

    def get_subgraph(self, node_ids: list[str]) -> np.ndarray:
        """提取子图的邻接矩阵"""
        indices = []
        for nid in node_ids:
            idx = self._find_index(nid)
            if idx is not None:
                indices.append(idx)

        if not indices:
            return np.array([[]])

        idx_arr = np.array(indices)
        return self._adj_matrix[idx_arr][:, idx_arr]
