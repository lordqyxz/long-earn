"""MemoryStore 单元测试"""

import tempfile
from pathlib import Path

import pandas as pd

from long_earn.memory.store import MemoryStore


class TestMemoryStoreFacts:
    def test_add_fact(self):
        store = MemoryStore()
        idx = store.add_fact("夏普比率衡量风险调整收益", metadata={"term": "夏普比率"})
        assert idx == 0
        assert store.fact_count == 1

    def test_add_fact_auto_metadata(self):
        store = MemoryStore()
        store.add_fact("test content")
        fact = store.get_fact(0)
        assert fact is not None
        assert "created_at" in fact["metadata"]
        assert "fact_id" in fact["metadata"]

    def test_add_facts_batch(self):
        store = MemoryStore()
        items = [("事实1", {"term": "A"}), ("事实2", {"term": "B"})]
        indices = store.add_facts(items)
        assert indices == [0, 1]
        assert store.fact_count == 2

    def test_get_fact(self):
        store = MemoryStore()
        store.add_fact("内容", metadata={"key": "value"})
        fact = store.get_fact(0)
        assert fact["content"] == "内容"
        assert fact["metadata"]["key"] == "value"

    def test_get_fact_out_of_range(self):
        store = MemoryStore()
        assert store.get_fact(0) is None
        assert store.get_fact(-1) is None

    def test_get_fact_by_id(self):
        store = MemoryStore()
        store.add_fact("test", metadata={"fact_id": "custom_id"})
        fact = store.get_fact_by_id("custom_id")
        assert fact is not None
        assert fact["content"] == "test"

    def test_get_fact_by_id_not_found(self):
        store = MemoryStore()
        assert store.get_fact_by_id("nonexistent") is None

    def test_get_all_facts_dataframe(self):
        store = MemoryStore()
        store.add_fact("A", metadata={"term": "t1"})
        store.add_fact("B", metadata={"term": "t2"})
        df = store.get_all_facts()
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 2


class TestMemoryStoreSearch:
    def test_search_returns_results(self):
        store = MemoryStore()
        store.add_fact("动量策略根据近期涨幅选股", metadata={"term": "动量策略"})
        store.add_fact("均值回归基于价格偏离买入", metadata={"term": "均值回归"})
        store.add_fact("夏普比率衡量风险调整收益", metadata={"term": "夏普比率"})

        results = store.search("动量因子", k=2)
        assert len(results) >= 1
        assert "similarity" in results[0]

    def test_search_empty_store(self):
        store = MemoryStore()
        results = store.search("anything")
        assert results == []

    def test_search_category_filter(self):
        store = MemoryStore()
        store.add_fact("策略A", metadata={"category": "趋势跟踪"})
        store.add_fact("策略B", metadata={"category": "均值回归"})
        results = store.search("策略", categories=["趋势跟踪"])
        assert len(results) == 1
        assert results[0]["metadata"]["category"] == "趋势跟踪"

    def test_search_term_filter(self):
        store = MemoryStore()
        store.add_fact("关于夏普比率", metadata={"term": "夏普比率"})
        store.add_fact("关于最大回撤", metadata={"term": "最大回撤"})
        results = store.search("指标", terms=["夏普"])
        assert len(results) == 1

    def test_search_min_similarity(self):
        store = MemoryStore()
        store.add_fact("动量策略是一种基于趋势跟踪的选股方法")
        store.add_fact("动量因子分析包括收益率动量和盈余动量")
        results = store.search("动量", min_similarity=0.0)
        assert len(results) >= 1

    def test_search_as_strings(self):
        store = MemoryStore()
        store.add_fact(
            "夏普比率衡量单位风险的超额收益",
            metadata={"source_file": "test.md", "term": "夏普比率"},
        )
        strings = store.search_as_strings("夏普比率")
        assert len(strings) >= 1
        assert "夏普比率" in strings[0]
        assert "【来源:" in strings[0]


class TestMemoryStoreRelations:
    def test_add_relation(self):
        store = MemoryStore()
        store.add_relation("A", "B", weight=0.7)
        related = store.get_related("A")
        assert len(related) >= 1

    def test_get_related(self):
        store = MemoryStore()
        store.add_relation("策略A", "因子B")
        store.add_relation("因子B", "指标C")
        related = store.get_related("策略A", depth=2)
        assert "因子B" in related


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
            facts_path = Path(tmp_path).with_suffix(".facts.pkl")
            assert facts_path.exists()

            store2 = MemoryStore()
            ok = store2.load(tmp_path)
            assert ok
            assert store2.fact_count == 1
            fact = store2.get_fact(0)
            assert fact is not None
            assert fact["content"] == "持久化测试内容"
            assert fact["metadata"]["key"] == "value"
        finally:
            Path(tmp_path).unlink(missing_ok=True)
            Path(tmp_path).with_suffix(".facts.pkl").unlink(missing_ok=True)
            Path(tmp_path).with_suffix(".relations.pkl").unlink(missing_ok=True)

    def test_load_nonexistent(self):
        store = MemoryStore()
        assert not store.load("/tmp/nonexistent_memory.npz")


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

    def test_split_markdown_no_headings(self):
        md = "没有标题的纯文本内容" * 50
        chunks = MemoryStore._split_markdown(
            md, "test.md", chunk_size=100, chunk_overlap=20
        )
        assert len(chunks) > 1

    def test_split_markdown_empty_section(self):
        md = """# 标题一
内容A

## 标题二

# 标题三
内容B"""
        chunks = MemoryStore._split_markdown(md, "test.md")
        # 空内容段（标题二）会被跳过
        assert len(chunks) == 2

    def test_split_markdown_long_section(self):
        md = "长内容" * 1000
        chunks = MemoryStore._split_markdown(
            md, "test.md", chunk_size=100, chunk_overlap=20
        )
        assert len(chunks) > 1

    def test_load_markdown_file(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8"
        ) as f:
            f.write("# 策略知识\n动量策略选股方法\n\n## 回测\n夏普比率评估\n")
            tmp = f.name

        try:
            store = MemoryStore()
            count = store.load_markdown(tmp)
            assert count > 0
            assert store.fact_count > 0
        finally:
            Path(tmp).unlink(missing_ok=True)

    def test_load_text_file(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write("文本文件内容" * 50)
            tmp = f.name

        try:
            store = MemoryStore()
            count = store.load_text(tmp, chunk_size=100, chunk_overlap=20)
            assert count > 0
        finally:
            Path(tmp).unlink(missing_ok=True)

    def test_load_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = Path(tmpdir) / "doc.md"
            txt_path = Path(tmpdir) / "notes.txt"
            md_path.write_text("# 标题\n内容A\n\n## 子标题\n内容B", encoding="utf-8")
            txt_path.write_text("文本内容" * 20, encoding="utf-8")

            store = MemoryStore()
            total = store.load_directory(tmpdir)
            assert total > 0
            assert store.fact_count > 0

    def test_search_as_strings_with_category(self):
        store = MemoryStore()
        store.add_fact(
            "动量策略基于趋势跟踪",
            metadata={
                "source_file": "strategy.md",
                "term": "动量策略",
                "category": "趋势跟踪",
            },
        )
        strings = store.search_as_strings("动量")
        assert len(strings) >= 1
        assert "类别:" in strings[0]

    def test_search_source_file_filter(self):
        store = MemoryStore()
        store.add_fact("内容A", metadata={"source_file": "doc1.md"})
        store.add_fact("内容B", metadata={"source_file": "doc2.md"})
        results = store.search("内容", source_files=["doc1.md"])
        assert len(results) == 1
        assert results[0]["metadata"]["source_file"] == "doc1.md"

    def test_search_min_similarity_skip(self):
        store = MemoryStore()
        store.add_fact("动量策略是一种趋势跟踪的选股方法")
        results = store.search("完全无关的查询", min_similarity=0.99)
        assert results == []

    def test_ensure_vectors_cached(self):
        store = MemoryStore()
        store.add_fact("测试内容")
        store.search("测试")  # triggers vectorization
        # Second call should use cached vectors
        results = store.search("测试")
        assert len(results) >= 1
