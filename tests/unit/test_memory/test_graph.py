"""RelationGraph 单元测试"""

import pytest

from long_earn.memory.graph import RelationGraph


class TestRelationGraph:
    def test_add_node(self):
        g = RelationGraph()
        idx = g.add_node("entity_1", type="strategy", name="动量策略")
        assert idx == 0
        assert g.node_count == 1
        assert g.nodes == ["entity_1"]

    def test_add_duplicate_node(self):
        g = RelationGraph()
        g.add_node("entity_1", type="strategy")
        g.add_node("entity_1", version=2)
        assert g.node_count == 1

    def test_add_edge_creates_nodes(self):
        g = RelationGraph()
        g.add_edge("A", "B", weight=0.8)
        assert g.node_count == 2

    def test_add_edge_updates_weight(self):
        g = RelationGraph()
        g.add_edge("A", "B", weight=0.5)
        g.add_edge("A", "B", weight=0.9)
        neighbors = g.get_neighbors("A")
        assert neighbors[0][1] == pytest.approx(0.9)

    def test_get_neighbors(self):
        g = RelationGraph()
        g.add_edge("中心", "邻居1", weight=0.8)
        g.add_edge("中心", "邻居2", weight=0.3)
        g.add_edge("中心", "邻居3", weight=0.5)

        neighbors = g.get_neighbors("中心", top_k=2)
        assert len(neighbors) == 2
        assert neighbors[0][0] == "邻居1"  # 最高权重

    def test_get_neighbors_min_weight(self):
        g = RelationGraph()
        g.add_edge("A", "B", weight=0.3)
        g.add_edge("A", "C", weight=0.8)

        result = g.get_neighbors("A", min_weight=0.5)
        assert len(result) == 1
        assert result[0][0] == "C"

    def test_get_neighbors_unknown_node(self):
        g = RelationGraph()
        assert g.get_neighbors("不存在") == []

    def test_get_related_bfs(self):
        g = RelationGraph()
        g.add_edge("A", "B")
        g.add_edge("B", "C")
        g.add_edge("C", "D")

        related = g.get_related("A", depth=2)
        assert "B" in related
        assert "C" in related
        assert "D" not in related  # depth=2 到不了 D(A→B→C→D)

    def test_get_related_depth_3(self):
        g = RelationGraph()
        g.add_edge("A", "B")
        g.add_edge("B", "C")
        g.add_edge("C", "D")

        related = g.get_related("A", depth=3)
        assert "D" in related

    def test_get_related_min_weight(self):
        g = RelationGraph()
        g.add_edge("A", "B", weight=0.9)
        g.add_edge("A", "C", weight=0.05)

        related = g.get_related("A", min_weight=0.1)
        assert "B" in related
        assert "C" not in related

    def test_get_related_unknown_node(self):
        g = RelationGraph()
        assert g.get_related("不存在") == []

    def test_to_dataframe(self):
        g = RelationGraph()
        g.add_edge("A", "B", weight=0.5)
        g.add_edge("B", "C", weight=0.3)

        df = g.to_dataframe()
        assert len(df) == 2
        assert list(df.columns) == ["source", "target", "weight"]

    def test_get_subgraph(self):
        g = RelationGraph()
        g.add_edge("A", "B", weight=0.7)
        g.add_edge("B", "C", weight=0.4)
        g.add_edge("A", "C", weight=0.2)

        sub = g.get_subgraph(["A", "B"])
        assert sub.shape == (2, 2)

    def test_get_subgraph_unknown_nodes(self):
        g = RelationGraph()
        g.add_edge("A", "B", weight=0.7)

        sub = g.get_subgraph(["X", "Y"])
        assert sub.shape == (1, 0)

    def test_max_nodes_limit(self):
        g = RelationGraph(max_nodes=3)
        g.add_node("A")
        g.add_node("B")
        g.add_node("C")
        with pytest.raises(ValueError, match="上限"):
            g.add_node("D")

    def test_empty_graph_properties(self):
        g = RelationGraph()
        assert g.node_count == 0
        assert g.nodes == []
