"""SubstanceStore 单元测试 — 存储检索契约 + 持久化往返 + 文档加载。"""

import tempfile
from pathlib import Path

from long_earn.substance.model import Substance, SubstanceForm
from long_earn.substance.store import SubstanceStore


class TestSubstanceStoreCrud:
    def test_add_and_count(self):
        store = SubstanceStore()
        store.add(Substance(form=SubstanceForm.KNOWLEDGE, content="夏普比率"))
        assert store.count == 1

    def test_add_knowledge_convenience(self):
        store = SubstanceStore()
        sid = store.add_knowledge("动量策略", metadata={"term": "动量"})
        assert sid.startswith("sub_")
        assert store.fact_count == 1

    def test_get_by_sid(self):
        store = SubstanceStore()
        s = Substance(form=SubstanceForm.KNOWLEDGE, content="测试")
        store.add(s)
        assert store.get_by_sid(s.sid) is not None
        assert store.get_by_sid("nonexistent") is None


class TestSubstanceStoreSearch:
    def test_search_returns_results(self):
        store = SubstanceStore()
        store.add_knowledge("动量策略根据近期涨幅选股", metadata={"term": "动量策略"})
        store.add_knowledge("均值回归基于价格偏离买入", metadata={"term": "均值回归"})
        store.add_knowledge("夏普比率衡量风险调整收益", metadata={"term": "夏普比率"})

        results = store.search("动量因子", k=2)
        assert len(results) >= 1
        assert "similarity" in results[0]
        assert "content" in results[0]
        assert "metadata" in results[0]

    def test_search_category_filter(self):
        store = SubstanceStore()
        store.add_knowledge("策略A", metadata={"category": "趋势跟踪"})
        store.add_knowledge("策略B", metadata={"category": "均值回归"})
        results = store.search("策略", categories=["趋势跟踪"])
        assert len(results) == 1
        assert results[0]["metadata"]["category"] == "趋势跟踪"

    def test_search_as_strings_format(self):
        store = SubstanceStore()
        store.add_knowledge(
            "动量策略选股", metadata={"source_file": "test.md", "term": "动量"}
        )
        results = store.search_as_strings("动量", k=1)
        assert len(results) >= 1
        assert "【来源: test.md" in results[0]


class TestSubstanceStorePersistence:
    def test_save_and_load_jsonl(self):
        store = SubstanceStore()
        store.add_knowledge("持久化测试内容", metadata={"key": "value"})
        store.add_relation("A", "B", weight=0.5)

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            tmp_path = f.name

        try:
            store.save(tmp_path)
            assert Path(tmp_path).exists()

            store2 = SubstanceStore()
            ok = store2.load(tmp_path)
            assert ok
            assert store2.count >= 2
        finally:
            Path(tmp_path).unlink(missing_ok=True)
            meta = Path(tmp_path).parent / "meta.json"
            if meta.exists():
                meta.unlink(missing_ok=True)


class TestSubstanceStoreDocumentLoading:
    def test_load_markdown_headings(self):
        store = SubstanceStore()
        md = """# 一级标题
内容A

## 二级标题
内容B

# 另一个一级
内容C"""
        import tempfile

        with tempfile.NamedTemporaryFile(
            suffix=".md", delete=False, mode="w", encoding="utf-8"
        ) as f:
            f.write(md)
            tmp = f.name

        try:
            count = store.load_markdown(tmp)
            assert count == 3
        finally:
            Path(tmp).unlink(missing_ok=True)


class TestSubstanceStoreRelations:
    def test_add_relation_and_get_related(self):
        store = SubstanceStore()
        store.add_relation("entity_a", "entity_b", weight=0.8)
        related = store.get_related("entity_a", depth=1)
        assert "entity_b" in related