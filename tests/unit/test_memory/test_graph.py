"""RelationGraph 单元测试"""

from long_earn.memory.graph import RelationGraph


class TestRelationGraph:
    def test_add_node(self):
        g = RelationGraph()
        idx = g.add_node("entity_1", type="strategy", name="动量策略")
        assert idx == 0
        assert g.node_count == 1
        assert g.nodes == ["entity_1"]

    def test_get_neighbors(self):
        g = RelationGraph()
        g.add_edge("中心", "邻居1", weight=0.8)
        g.add_edge("中心", "邻居2", weight=0.3)
        g.add_edge("中心", "邻居3", weight=0.5)

        neighbors = g.get_neighbors("中心", top_k=2)
        assert len(neighbors) == 2
        assert neighbors[0][0] == "邻居1"  # 最高权重

    def test_get_related_bfs(self):
        g = RelationGraph()
        g.add_edge("A", "B")
        g.add_edge("B", "C")
        g.add_edge("C", "D")

        related = g.get_related("A", depth=2)
        assert "B" in related
        assert "C" in related
        assert "D" not in related  # depth=2 到不了 D(A→B→C→D)

    def test_to_dataframe(self):
        g = RelationGraph()
        g.add_edge("A", "B", weight=0.5)
        g.add_edge("B", "C", weight=0.3)

        df = g.to_dataframe()
        assert len(df) == 2
        assert list(df.columns) == ["source", "target", "weight"]
