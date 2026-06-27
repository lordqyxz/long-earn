"""假设树领域模型测试（HTR Phase 1）。

覆盖 CRUD + 序列化往返 + frontier/best_node/prune/backpropagate。
"""

from __future__ import annotations

from long_earn.strategy_rd.hypothesis_tree import (
    HypothesisNode,
    HypothesisTree,
    NodeStatus,
)


class TestHypothesisNode:
    def test_create_and_is_leaf(self):
        node = HypothesisNode(id="n1", hypothesis="test")
        assert node.is_leaf() is True
        assert node.is_frontier() is True
        assert node.status == NodeStatus.PENDING

    def test_serialize_roundtrip(self):
        node = HypothesisNode(
            id="n1",
            parent_id="root",
            hypothesis="加动量过滤",
            direction="收益增强",
            dev_score=1.2,
            oos_score=0.8,
        )
        d = node.to_dict()
        restored = HypothesisNode.from_dict(d)
        assert restored.id == "n1"
        assert restored.hypothesis == "加动量过滤"
        assert restored.dev_score == 1.2
        assert restored.oos_score == 0.8


class TestHypothesisTree:
    def _make_tree(self) -> HypothesisTree:
        tree = HypothesisTree(run_id="test_run")
        tree.init_root(hypothesis="初始策略", strategy_ref="yaml_0")
        return tree

    def test_init_root(self):
        tree = self._make_tree()
        assert tree.root is not None
        assert tree.root.id == "root"
        assert tree.root.status == NodeStatus.VALIDATED
        assert tree.current_best_id == "root"

    def test_add_child(self):
        tree = self._make_tree()
        child_id = tree.add_child("root", "加动量过滤", direction="收益增强")
        child = tree.get_node(child_id)
        assert child is not None
        assert child.parent_id == "root"
        assert child.depth == 1
        assert child.status == NodeStatus.PENDING
        assert child_id in tree.root.children_ids

    def test_update_evidence(self):
        tree = self._make_tree()
        child_id = tree.add_child("root", "加止损")
        tree.update_evidence(
            child_id,
            dev_score=1.5,
            oos_score=0.9,
            insight="止损有效降低回撤",
        )
        node = tree.get_node(child_id)
        assert node is not None
        assert node.dev_score == 1.5
        assert node.oos_score == 0.9
        assert node.status == NodeStatus.VALIDATED
        assert "止损有效" in node.insight

    def test_frontier(self):
        tree = self._make_tree()
        tree.add_child("root", "假设A")
        tree.add_child("root", "假设B")
        # root 是 validated 但非叶（有子节点），不是 frontier
        # 两个子节点是 pending + leaf → frontier
        frontier = tree.frontier()
        assert len(frontier) == 2

    def test_best_node(self):
        tree = self._make_tree()
        c1 = tree.add_child("root", "假设A")
        c2 = tree.add_child("root", "假设B")
        tree.update_evidence(c1, dev_score=1.0, oos_score=0.5)
        tree.update_evidence(c2, dev_score=1.5, oos_score=1.0)
        best = tree.best_node()
        assert best is not None
        assert best.id == c2

    def test_prune_subtree(self):
        tree = self._make_tree()
        c1 = tree.add_child("root", "假设A")
        c1a = tree.add_child(c1, "子假设A1")
        tree.prune_subtree(c1)
        assert tree.get_node(c1).status == NodeStatus.PRUNED
        assert tree.get_node(c1a).status == NodeStatus.PRUNED

    def test_backpropagate_insight(self):
        tree = self._make_tree()
        c1 = tree.add_child("root", "假设A")
        tree.update_evidence(c1, insight="动量过滤提升 sharpe 0.3")
        tree.backpropagate_insight(c1)
        assert "动量过滤" in tree.root.insight

    def test_serialize_roundtrip(self):
        tree = self._make_tree()
        c1 = tree.add_child("root", "假设A", direction="收益增强")
        tree.update_evidence(c1, dev_score=1.2, oos_score=0.8, insight="有效")
        data = tree.serialize()
        restored = HypothesisTree.deserialize(data)
        assert restored.run_id == "test_run"
        assert restored.root is not None
        assert restored.node_count == 2
        assert restored.get_node(c1).dev_score == 1.2
