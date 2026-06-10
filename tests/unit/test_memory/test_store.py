"""MemoryStore 单元测试"""

import tempfile
from pathlib import Path

from long_earn.memory.store import MemoryStore


class TestMemoryStoreFacts:
    def test_add_fact(self):
        store = MemoryStore()
        idx = store.add_fact("夏普比率衡量风险调整收益", metadata={"term": "夏普比率"})
        assert idx == 0
        assert store.fact_count == 1


class TestMemoryStoreSearch:
    def test_search_returns_results(self):
        store = MemoryStore()
        store.add_fact("动量策略根据近期涨幅选股", metadata={"term": "动量策略"})
        store.add_fact("均值回归基于价格偏离买入", metadata={"term": "均值回归"})
        store.add_fact("夏普比率衡量风险调整收益", metadata={"term": "夏普比率"})

        results = store.search("动量因子", k=2)
        assert len(results) >= 1
        assert "similarity" in results[0]

    def test_search_category_filter(self):
        store = MemoryStore()
        store.add_fact("策略A", metadata={"category": "趋势跟踪"})
        store.add_fact("策略B", metadata={"category": "均值回归"})
        results = store.search("策略", categories=["趋势跟踪"])
        assert len(results) == 1
        assert results[0]["metadata"]["category"] == "趋势跟踪"


class TestMemoryStorePersistence:
    def test_save_and_load(self):
        store = MemoryStore()
        store.add_fact("持久化测试内容", metadata={"key": "value"})
        store.add_relation("A", "B", weight=0.5)

        with tempfile.NamedTemporaryFile(suffix=".npz", delete=False) as f:
            tmp_path = f.name

        try:
            store.save(tmp_path)
            assert Path(tmp_path).exists()

            store2 = MemoryStore()
            ok = store2.load(tmp_path)
            assert ok
            assert store2.fact_count == 1
            fact = store2.get_fact(0)
            assert fact is not None
            assert fact["content"] == "持久化测试内容"
        finally:
            Path(tmp_path).unlink(missing_ok=True)
            Path(tmp_path).with_suffix(".facts.pkl").unlink(missing_ok=True)
            Path(tmp_path).with_suffix(".relations.pkl").unlink(missing_ok=True)


class TestMemoryStoreDocumentLoading:
    def test_split_markdown_headings(self):
        md = """# 一级标题
内容A

## 二级标题
内容B

# 另一个一级
内容C"""
        chunks = MemoryStore._split_markdown(md, "test.md")
        assert len(chunks) == 3
