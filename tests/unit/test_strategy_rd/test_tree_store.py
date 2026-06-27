"""假设树存储测试（HTR Phase 1）。"""

from __future__ import annotations

import tempfile

from long_earn.strategy_rd.hypothesis_tree import HypothesisTree
from long_earn.strategy_rd.tree_store import HypothesisTreeStore


class TestHypothesisTreeStore:
    def _make_tree(self) -> HypothesisTree:
        tree = HypothesisTree(run_id="test_save_load")
        tree.init_root(hypothesis="初始策略")
        tree.add_child("root", "假设A")
        return tree

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = HypothesisTreeStore(base_dir=tmpdir)
            tree = self._make_tree()
            path = store.save(tree)
            assert path.exists()

            loaded = store.load("test_save_load")
            assert loaded is not None
            assert loaded.run_id == "test_save_load"
            assert loaded.node_count == tree.node_count

    def test_load_nonexistent_returns_none(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = HypothesisTreeStore(base_dir=tmpdir)
            assert store.load("nonexistent") is None

    def test_list_runs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = HypothesisTreeStore(base_dir=tmpdir)
            t1 = HypothesisTree(run_id="run_001")
            t1.init_root()
            t2 = HypothesisTree(run_id="run_002")
            t2.init_root()
            store.save(t1)
            store.save(t2)
            runs = store.list_runs()
            assert "run_001" in runs
            assert "run_002" in runs
