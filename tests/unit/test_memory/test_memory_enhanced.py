"""MemoryStore 增强功能接口测试 — 记忆衰减、冲突检测、记忆压缩

聚焦公共接口契约，不测试内部实现细节。
"""

from datetime import datetime, timedelta

from long_earn.memory.embedding import EmbeddingRetriever
from long_earn.memory.store import MemoryStore


class TestMemoryDecay:
    """记忆衰减机制接口测试"""

    def test_decay_method_marks_facts(self):
        """decay() 方法正确标记衰减事实"""
        store = MemoryStore()
        store.add_fact("新内容")
        # 手动插入旧事实
        store._facts.append(
            {
                "content": "旧内容",
                "metadata": {
                    "created_at": (datetime.now() - timedelta(days=365)).isoformat(),
                },
            }
        )
        store._fact_texts.append("旧内容")

        count = store.decay(half_life_days=30)
        assert count == 1
        assert store._facts[0]["metadata"]["decayed"] is False
        assert store._facts[1]["metadata"]["decayed"] is True

    def test_search_with_decay_excludes_decayed(self):
        """带衰减的搜索排除已衰减事实"""
        store = MemoryStore()
        store.add_fact("热门概念 A")
        store._facts.append(
            {
                "content": "过时概念 B",
                "metadata": {
                    "created_at": (datetime.now() - timedelta(days=365)).isoformat(),
                    "decayed": True,
                },
            }
        )
        store._fact_texts.append("过时概念 B")

        results = store.search("概念", k=5, apply_decay=True, include_decayed=False)
        contents = [r["content"] for r in results]
        assert "热门概念 A" in contents
        assert "过时概念 B" not in contents


class TestConflictDetection:
    """冲突检测机制接口测试"""

    def test_find_no_conflict(self):
        """不相关内容不触发冲突"""
        store = MemoryStore()
        store.add_fact("动量策略根据近期涨幅选股", metadata={"term": "动量"})
        conflicts = store.find_conflicts("如何配置 Python 开发环境", min_similarity=0.5)
        assert len(conflicts) == 0

    def test_find_conflict_by_similarity(self):
        """高相似度内容触发冲突"""
        store = MemoryStore()
        store.add_fact(
            "this strategy works very well 利好买入",
            metadata={"term": "test_strategy"},
        )
        conflicts = store.find_conflicts(
            "this strategy works very bad 利空卖出",
            min_similarity=0.5,
        )
        assert len(conflicts) >= 1

    def test_resolve_conflict_creates_group(self):
        """resolve_conflict 正确建立冲突组"""
        store = MemoryStore()
        idx_a = store.add_fact("策略A: 买入持有", metadata={"term": "策略A"})
        idx_b = store.resolve_conflict(idx_a, "策略A: 波段操作")

        assert store._facts[idx_a]["metadata"]["conflict_group"]
        assert store._facts[idx_b]["metadata"]["conflict_group"]
        assert (
            store._facts[idx_a]["metadata"]["conflict_group"]
            == store._facts[idx_b]["metadata"]["conflict_group"]
        )


class TestMemoryCompression:
    """记忆压缩机制接口测试"""

    def test_compress_no_similar(self):
        """没有相似事实时不压缩"""
        store = MemoryStore()
        store.add_fact("动量策略选股")
        store.add_fact("Python 编程教程")
        store.add_fact("数据分析方法")

        count = store.compress(min_similarity=0.9)
        assert count == 0
        assert store.fact_count == 3

    def test_compress_similar_facts(self):
        """相似事实被正确合并"""
        store = MemoryStore()
        store.add_fact(
            "momentum strategy based on recent price trend 动量策略根据近期涨幅"
        )
        store.add_fact(
            "momentum strategy based on past price trend 动量策略根据过去涨幅"
        )
        store.add_fact("mean reversion strategy 均值回归基于价格偏离")

        count = store.compress(min_similarity=0.5)
        assert count > 0
        assert store.fact_count < 3

    def test_summarize_topic_exists(self):
        """主题总结包含相关内容"""
        store = MemoryStore()
        store.add_fact(
            "momentum strategy 动量策略选股根据近期股价强势",
            metadata={"term": "动量策略"},
        )
        store.add_fact(
            "momentum trend 动量趋势适合行情",
            metadata={"term": "动量策略"},
        )

        summary = store.summarize_topic("momentum trend 动量")
        assert "momentum" in summary or "动量" in summary

    def test_summarize_topic_not_found(self):
        """不存在的主题返回提示信息"""
        store = MemoryStore()
        summary = store.summarize_topic("不存在的主题")
        assert "未找到" in summary


class TestEmbeddingRetriever:
    """嵌入检索器接口测试"""

    def test_is_available_returns_bool(self):
        """is_available 应返回布尔值"""
        retriever = EmbeddingRetriever()
        assert isinstance(retriever.is_available, bool)

    def test_hybrid_search_fallback(self):
        """无嵌入模型时混合检索回退到 TF-IDF"""
        retriever = EmbeddingRetriever()
        store = MemoryStore()
        store.add_fact("动量策略选股")
        store.add_fact("均值回归策略")

        results = retriever.hybrid_search(store, "动量", k=2, apply_decay=False)
        assert len(results) >= 1

    def test_ollama_backend_not_available_returns_false(self):
        """ollama 后端不可用时 is_available 返回 False"""
        retriever = EmbeddingRetriever(
            model_name="bge-m3",
            base_url="http://localhost:99999",
            backend="ollama",
        )
        assert not retriever.is_available

    def test_ollama_embedding_search_fallback(self):
        """ollama 不可用时 embedding_search 返回空列表"""
        retriever = EmbeddingRetriever(
            model_name="bge-m3",
            base_url="http://localhost:99999",
            backend="ollama",
        )
        store = MemoryStore()
        store.add_fact("test content")
        results = retriever.embedding_search(store, "test", k=3)
        assert results == []

    def test_ollama_hybrid_search_fallback(self):
        """ollama 不可用时 hybrid_search 回退到 TF-IDF"""
        retriever = EmbeddingRetriever(
            model_name="bge-m3",
            base_url="http://localhost:99999",
            backend="ollama",
        )
        store = MemoryStore()
        store.add_fact("momentum strategy based on price trend")
        store.add_fact("mean reversion strategy")
        results = retriever.hybrid_search(store, "momentum trend", k=2, apply_decay=False)
        assert len(results) >= 1

    def test_ollama_rerank_disabled_when_not_available(self):
        """ollama 不可用时 rerank 不执行"""
        retriever = EmbeddingRetriever(
            model_name="bge-m3",
            base_url="http://localhost:99999",
            backend="ollama",
            enable_reranker=True,
        )
        store = MemoryStore()
        store.add_fact("test content")
        # embedding_search 返回空，rerank 不会执行
        results = retriever.embedding_search(store, "test", k=3)
        assert results == []

    def test_ollama_rerank_skipped_when_no_candidates(self):
        """空候选列表时 rerank 直接返回"""
        retriever = EmbeddingRetriever(
            model_name="bge-m3",
            base_url="http://localhost:11434",
            backend="ollama",
        )
        results = retriever._rerank("test query", [])
        assert results == []
