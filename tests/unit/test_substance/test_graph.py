"""GraphIndex 测试 — 邻接表 + BFS 路径返回。"""

from long_earn.substance.indices.graph import GraphIndex


def test_neighbors():
    g = GraphIndex()
    g.add_edge("A", "B", weight=0.8)
    g.add_edge("A", "C", weight=0.3)
    neighbors = g.neighbors("A")
    assert ("B", 0.8) in neighbors
    assert len(neighbors) == 2


def test_bfs_depth_and_path():
    g = GraphIndex()
    g.add_edge("A", "B")
    g.add_edge("B", "C")
    g.add_edge("C", "D")

    result = g.bfs("A", max_depth=2)
    sids = [r["sid"] for r in result]
    assert "B" in sids
    assert "C" in sids
    assert "D" not in sids  # depth=2 到不了 D

    c_entry = next(r for r in result if r["sid"] == "C")
    assert "B" in c_entry["path"]
    assert c_entry["distance"] == 2
