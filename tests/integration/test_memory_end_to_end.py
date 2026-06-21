"""记忆系统端到端集成测试

验证 add_fact → search → recall 完整链路。
"""

import pytest

from long_earn.memory.store import MemoryStore


@pytest.fixture
def store():
    """创建空的 MemoryStore 实例"""
    return MemoryStore()


class TestMemoryEndToEnd:
    """记忆系统端到端链路测试"""

    def test_add_and_search(self, store):
        """添加事实后应能通过搜索检索到"""
        store.add_fact("动量策略根据近期涨幅选股", metadata={"category": "策略"})
        store.add_fact("均值回归基于价格偏离", metadata={"category": "策略"})
        store.add_fact("Python 编程教程", metadata={"category": "技术"})

        results = store.search("动量选股", k=3)
        contents = [r["content"] for r in results]
        assert "动量策略根据近期涨幅选股" in contents

    def test_add_and_search_with_category_filter(self, store):
        """按类别过滤搜索应返回对应类别结果"""
        store.add_fact("动量策略根据近期涨幅选股", metadata={"category": "策略"})
        store.add_fact("Python 编程教程", metadata={"category": "技术"})

        results = store.search("动量", k=3, categories=["策略"])
        contents = [r["content"] for r in results]
        assert "动量策略根据近期涨幅选股" in contents
        assert "Python 编程教程" not in contents

    def test_add_and_search_with_term_filter(self, store):
        """按词条过滤搜索应返回对应词条结果"""
        store.add_fact("动量策略适合牛市", metadata={"term": "动量"})
        store.add_fact("均值回归适合震荡市", metadata={"term": "均值回归"})

        results = store.search("策略", k=3, terms=["动量"])
        contents = [r["content"] for r in results]
        assert "动量策略适合牛市" in contents
        assert "均值回归适合震荡市" not in contents

    def test_persistence_roundtrip(self, store, tmp_path):
        """持久化后重新加载应保留数据"""
        store.add_fact("测试持久化", metadata={"test": True})
        # 添加第二条事实以触发向量构建
        store.add_fact("持久化测试第二行", metadata={"test": True})
        # 先搜索触发向量构建
        store.search("测试", k=1)
        save_path = tmp_path / "memory.npz"

        store.save(str(save_path))
        assert save_path.exists()

        new_store = MemoryStore()
        loaded = new_store.load(str(save_path))
        assert loaded is True

        # 加载后需要重新 fit 向量器
        if new_store._fact_texts:
            new_store._doc_matrix = new_store._vectorizer.fit_transform(new_store._fact_texts)
            new_store._dirty = False

        results = new_store.search("持久化", k=1)
        assert len(results) >= 1
        assert results[0]["content"] == "测试持久化"
