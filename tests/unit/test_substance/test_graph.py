"""GraphIndex 单元测试 — 邻接表 + BFS 返回路径。"""

from long_earn.substance.indices.graph import GraphIndex


class TestGraphIndex:
    def test_add_edge_and_neighbors(self):
        g = GraphIndex()
        g.add_edge("A", "B", weight=0.8)
        g.add_edge("A", "C", weight=0.3)
        neighbors = g.neighbors("A")
        assert len(neighbors) == 2
        assert ("B", 0.8) in neighbors

    def test_bfs_returns_path(self):
        g = GraphIndex()
        g.add_edge("A", "B")
        g.add_edge("B", "C")
        g.add_edge("C", "D")

        result = g.bfs("A", max_depth=2)
        sids = [r["sid"] for r in result]
        assert "B" in sids
        assert "C" in sids
        assert "D" not in sids  # depth=2 到不了 D

    def test_bfs_path_contains_route(self):
        g = GraphIndex()
        g.add_edge("A", "B")
        g.add_edge("B", "C")
        result = g.bfs("A", max_depth=3)
        c_entry = next(r for r in result if r["sid"] == "C")
        assert "B" in c_entry["path"]
        assert c_entry["distance"] == 2

    def test_edge_count(self):
        g = GraphIndex()
        g.add_edge("A", "B")
        g.add_edge("B", "C")
        assert g.edge_count() == 2