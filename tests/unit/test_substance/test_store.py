"""SubstanceStore 测试 — 检索契约 + 持久化往返 + 文档加载 + 关系图。"""

from pathlib import Path

from long_earn.substance.model import SubstanceForm
from long_earn.substance.persistence import delete_substance, load_all
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
    """PostgreSQL 保存→加载往返一致性（含 relation）。

    PG 全量迁移后 save/load 落 PostgreSQL（path 参数兼容保留）。
    共享库隔离：用 sid 差集验证本次写入的物质可读回，用后清理。
    """
    before = {s.sid for s in load_all()}

    store = SubstanceStore()
    store.add_knowledge("持久化测试", metadata={"key": "value"})
    store.add_relation("A", "B", weight=0.5)
    store.save()  # path 已废弃，落 PG

    store2 = SubstanceStore()
    assert store2.load()  # 从 PG 全量加载
    added = {s.sid for s in load_all()} - before
    assert len(added) >= 2
    # 验证写入的物质类型与内容完整读回
    by_sid = {s.sid: s for s in load_all()}
    for sid in added:
        s = by_sid[sid]
        if s.form is SubstanceForm.RELATION:
            assert s.source_id == "A" and s.target_id == "B"
        else:
            assert s.content == "持久化测试"
    # 清理本次写入，避免污染共享库
    for sid in added:
        delete_substance(sid)


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
