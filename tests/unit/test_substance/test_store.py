"""SubstanceStore 测试 — 检索契约 + 持久化往返 + 文档加载 + 关系图。"""

from pathlib import Path

from long_earn.substance.store import SubstanceStore


def test_search_with_metadata_filter():
    """搜索返回结果 + category 过滤生效。"""
    store = SubstanceStore()
    store.add_knowledge("动量策略根据近期涨幅选股", metadata={"term": "动量策略"})
    store.add_knowledge("均值回归基于价格偏离买入", metadata={"term": "均值回归"})
    store.add_knowledge("策略A", metadata={"category": "趋势跟踪"})
    store.add_knowledge("策略B", metadata={"category": "均值回归"})

    results = store.search("动量因子", k=2)
    assert len(results) >= 1
    assert "content" in results[0]
    assert "similarity" in results[0]

    filtered = store.search("策略", categories=["趋势跟踪"])
    assert len(filtered) == 1
    assert filtered[0]["metadata"]["category"] == "趋势跟踪"


def test_persistence_roundtrip(tmp_path: Path):
    """JSONL 保存→加载往返一致性（含 relation）。"""
    store = SubstanceStore()
    store.add_knowledge("持久化测试", metadata={"key": "value"})
    store.add_relation("A", "B", weight=0.5)

    path = tmp_path / "test.jsonl"
    store.save(path)

    store2 = SubstanceStore()
    assert store2.load(path)
    assert store2.count >= 2


def test_load_markdown_by_headings(tmp_path: Path):
    """Markdown 按标题切分存入。"""
    md = tmp_path / "doc.md"
    md.write_text(
        "# 一级\n内容A\n\n## 二级\n内容B\n\n# 另一个\n内容C", encoding="utf-8"
    )

    store = SubstanceStore()
    assert store.load_markdown(md) == 3


def test_relation_bfs():
    """关系添加 + BFS 关联查询。"""
    store = SubstanceStore()
    store.add_relation("entity_a", "entity_b", weight=0.8)
    assert "entity_b" in store.get_related("entity_a", depth=1)
