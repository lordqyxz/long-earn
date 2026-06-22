"""记忆系统综合场景测试 — 关键词/段落/过滤/衰减/压缩/冲突

测试策略：构造真实量化交易场景，验证存储-提取-管理全链路。
不 mock 内部实现，聚焦接口行为。
"""

from datetime import datetime, timedelta

import pytest

from long_earn.memory.embedding import EmbeddingRetriever
from long_earn.memory.store import MemoryStore


class TestEnglishKeywordRetrieval:
    """英文关键词精确检索"""

    def test_sharpe_ratio_query(self):
        store = MemoryStore()
        store.add_fact("Sharpe ratio measures risk-adjusted return per unit of risk",
                       metadata={"term": "sharpe_ratio", "category": "risk_metrics"})
        results = store.search("Sharpe ratio risk adjusted return", k=3)
        assert len(results) >= 1
        assert results[0]["similarity"] > 0.3

    def test_momentum_strategy_query(self):
        store = MemoryStore()
        store.add_fact("Momentum strategy follows recent price trend for trading signals",
                       metadata={"term": "momentum", "category": "strategy"})
        store.add_fact("Value investing focuses on undervalued stocks",
                       metadata={"term": "value", "category": "strategy"})
        results = store.search("momentum trend following", k=3)
        assert results[0]["metadata"]["term"] == "momentum"

    def test_multi_word_query_ranks_correctly(self):
        """多词查询应正确排序：匹配越多词的文档排最前"""
        store = MemoryStore()
        store.add_fact("Alpha measures excess return above market benchmark",
                       metadata={"term": "alpha", "category": "performance"})
        store.add_fact("Annualized return converts total return to yearly average",
                       metadata={"term": "annual_return", "category": "performance"})
        results = store.search("excess return benchmark", k=3)
        assert results[0]["metadata"]["term"] == "alpha"


class TestMetadataFiltering:
    """元数据过滤检索"""

    def test_category_filter(self):
        store = MemoryStore()
        store.add_fact("Momentum strategy", metadata={"term": "momentum", "category": "strategy"})
        store.add_fact("Risk management", metadata={"term": "risk", "category": "risk"})
        results = store.search("trading", k=3, categories=["risk"])
        assert len(results) == 1
        assert results[0]["metadata"]["category"] == "risk"

    def test_term_filter(self):
        store = MemoryStore()
        store.add_fact("Momentum strategy", metadata={"term": "momentum", "category": "strategy"})
        store.add_fact("Risk management", metadata={"term": "risk", "category": "risk"})
        results = store.search("trading", k=3, terms=["momentum"])
        assert len(results) == 1

    def test_source_file_filter(self):
        store = MemoryStore()
        store.add_fact("Alpha strategy", metadata={"term": "alpha", "source_file": "en_strategy.md"})
        store.add_fact("Risk rules", metadata={"term": "risk", "source_file": "en_risk.md"})
        results = store.search("strategy", k=3, source_files=["en_strategy.md"])
        assert len(results) == 1

    def test_multi_category_filter(self):
        """多个 category 取并集"""
        store = MemoryStore()
        store.add_fact("Alpha strategy", metadata={"term": "alpha", "category": "strategy"})
        store.add_fact("Risk management", metadata={"term": "risk", "category": "risk"})
        store.add_fact("Portfolio theory", metadata={"term": "portfolio", "category": "theory"})
        results = store.search("management", k=3, categories=["strategy", "risk"])
        assert len(results) >= 1
        categories = {r["metadata"]["category"] for r in results}
        assert "risk" in categories


class TestParagraphRetrieval:
    """长文本段落检索"""

    def test_paragraph_stored_and_retrieved(self):
        store = MemoryStore()
        content = (
            "Walk-forward analysis divides historical data into N windows. "
            "Each window has an in-sample training set and out-of-sample test set. "
            "Parameters are optimized on in-sample and validated on out-of-sample."
        )
        store.add_fact(content, metadata={"term": "walk_forward", "category": "backtest"})
        results = store.search("walk forward analysis out of sample", k=3)
        assert len(results) >= 1
        assert results[0]["similarity"] > 0.2

    def test_metadata_filter_on_paragraph(self):
        store = MemoryStore()
        store.add_fact(
            "Position sizing should be based on account risk tolerance.",
            metadata={"term": "position_sizing", "category": "risk", "source_file": "risk_notes.md"},
        )
        store.add_fact(
            "Backtesting is the process of testing strategy on historical data.",
            metadata={"term": "backtesting", "category": "methodology", "source_file": "method.md"},
        )
        # 按 source_file 过滤应只返回 1 条
        results = store.search("position", k=3, source_files=["risk_notes.md"])
        assert len(results) == 1
        assert results[0]["metadata"]["term"] == "position_sizing"


class TestMemoryDecay:
    """记忆衰减场景"""

    def test_old_facts_decayed_below_new_facts(self):
        """旧事实经衰减后相似度应低于新事实"""
        store = MemoryStore()
        store.add_fact("Momentum strategy based on price trend",
                       metadata={"term": "momentum", "created_at": "2020-01-01T00:00:00"})
        store.add_fact("Momentum strategy based on price trend",
                       metadata={"term": "momentum", "created_at": "2026-06-01T00:00:00"})
        results_decay = store.search("momentum strategy", k=2, apply_decay=True)
        results_no_decay = store.search("momentum strategy", k=2, apply_decay=False)
        # 不衰减时两条相似度相同
        assert results_no_decay[0]["similarity"] == results_no_decay[1]["similarity"]
        # 衰减后新事实 > 旧事实
        assert results_decay[0]["similarity"] > results_decay[1]["similarity"]

    def test_include_decayed_flag(self):
        """include_decayed=True 应包含已衰减的事实"""
        store = MemoryStore()
        store._facts.append({
            "content": "Old fact",
            "metadata": {"created_at": "2019-01-01T00:00:00", "decayed": True},
        })
        store._fact_texts.append("Old fact")
        store.add_fact("New fact", metadata={"created_at": "2026-06-01T00:00:00"})
        results = store.search("fact", k=3, include_decayed=True, apply_decay=False)
        assert len(results) == 2
        results_excluded = store.search("fact", k=3, include_decayed=False, apply_decay=False)
        assert len(results_excluded) == 1


class TestConflictDetection:
    """冲突检测场景"""

    def test_no_conflict_for_unrelated(self):
        store = MemoryStore()
        store.add_fact("Momentum strategy buys recent winners",
                       metadata={"term": "momentum"})
        conflicts = store.find_conflicts("Python programming tutorial", min_similarity=0.5)
        assert len(conflicts) == 0

    def test_conflict_detected_by_contradiction(self):
        store = MemoryStore()
        store.add_fact("This strategy works very well 利好买入",
                       metadata={"term": "test_strategy"})
        conflicts = store.find_conflicts(
            "This strategy works very bad 利空卖出",
            min_similarity=0.5,
        )
        assert len(conflicts) >= 1

    def test_resolve_conflict_links_entries(self):
        store = MemoryStore()
        idx_a = store.add_fact("Strategy A: buy and hold", metadata={"term": "strategy_A"})
        idx_b = store.resolve_conflict(idx_a, "Strategy A: swing trade")
        assert store._facts[idx_a]["metadata"]["conflict_group"]
        assert store._facts[idx_b]["metadata"]["conflict_group"]
        assert (
            store._facts[idx_a]["metadata"]["conflict_group"]
            == store._facts[idx_b]["metadata"]["conflict_group"]
        )


class TestMemoryCompression:
    """记忆压缩场景"""

    def test_no_similar_facts_not_compressed(self):
        store = MemoryStore()
        store.add_fact("Momentum strategy selects stocks")
        store.add_fact("Python programming language")
        store.add_fact("Data analysis methods")
        count = store.compress(min_similarity=0.9)
        assert count == 0
        assert store.fact_count == 3

    def test_similar_facts_merged(self):
        """修复验证：相似事实被正确合并"""
        store = MemoryStore()
        store.add_fact("momentum strategy based on price trend following approach")
        store.add_fact("momentum strategy using price trend following method")
        store.add_fact("mean reversion strategy based on price deviation")
        count = store.compress(min_similarity=0.4)
        assert count > 0
        assert store.fact_count < 3

    def test_multi_cluster_compression_no_index_error(self):
        """修复验证：多聚类压缩不触发 IndexError"""
        store = MemoryStore()
        store.add_fact("cluster_A_item_1 momentum strategy")
        store.add_fact("cluster_A_item_2 momentum trend following")
        store.add_fact("cluster_B_item_1 value investing")
        store.add_fact("cluster_B_item_2 value bargain hunting")
        # 手动构造 doc_matrix 确保两个独立聚类
        import numpy as np
        store._doc_matrix = np.array([
            [1.0, 0.0, 0.5],
            [0.8, 0.0, 0.4],
            [0.0, 1.0, 0.0],
            [0.0, 0.8, 0.0],
        ], dtype=np.float32)
        store._dirty = False
        # 不应抛出 IndexError
        count = store.compress(min_similarity=0.5)
        assert count == 2
        assert store.fact_count == 2

    def test_compressed_fact_metadata(self):
        """压缩后的事实应有 compressed 标记和 merged_count"""
        store = MemoryStore()
        store.add_fact("momentum strategy based on price trend following approach")
        store.add_fact("momentum strategy using price trend following method")
        store.compress(min_similarity=0.4)
        for f in store._facts:
            if f["metadata"].get("compressed"):
                assert f["metadata"].get("merged_count", 0) >= 2
                return
        pytest.fail("No compressed fact found")


class TestEmbeddingRetriever:
    """嵌入检索器接口"""

    def test_is_available_returns_bool(self):
        retriever = EmbeddingRetriever()
        assert isinstance(retriever.is_available, bool)

    def test_hybrid_search_fallback_without_embedding(self):
        """无嵌入模型时 hybrid_search 回退到 TF-IDF"""
        retriever = EmbeddingRetriever()
        store = MemoryStore()
        store.add_fact("Momentum strategy selects stocks by price trend")
        store.add_fact("Mean reversion strategy")
        results = retriever.hybrid_search(store, "momentum trend", k=2, apply_decay=False)
        assert len(results) >= 1

    def test_invalidate_cache(self):
        retriever = EmbeddingRetriever()
        retriever._cache = "dummy"
        retriever._cache_version = 5
        retriever.invalidate_cache()
        assert retriever._cache is None
        assert retriever._cache_version == -1


class TestMemoryServiceRecall:
    """MemoryServiceImpl.recall 链路"""

    def test_recall_returns_results(self):
        from unittest.mock import MagicMock
        from long_earn.services.memory_service import MemoryServiceImpl

        config = MagicMock()
        config.memory_path = ""
        config.init_dir = ""
        svc = MemoryServiceImpl(config, MagicMock())
        svc._store.add_fact("Momentum strategy based on price trend",
                            metadata={"term": "momentum", "category": "strategy"})

        results = svc.recall("momentum trend", k=3)
        assert len(results) >= 1
        assert "similarity" in results[0]

    def test_recall_with_category_filter(self):
        from unittest.mock import MagicMock
        from long_earn.services.memory_service import MemoryServiceImpl

        config = MagicMock()
        config.memory_path = ""
        config.init_dir = ""
        svc = MemoryServiceImpl(config, MagicMock())
        svc._store.add_fact("Momentum strategy", metadata={"term": "momentum", "category": "strategy"})
        svc._store.add_fact("Risk management", metadata={"term": "risk", "category": "risk"})

        results = svc.recall("management", k=3, categories=["risk"])
        assert len(results) == 1

    def test_recall_without_init_dir_does_not_load_cwd(self):
        """修复验证：init_dir='' 不应加载当前目录文件"""
        from unittest.mock import MagicMock
        from long_earn.services.memory_service import MemoryServiceImpl

        config = MagicMock()
        config.memory_path = ""
        config.init_dir = ""
        svc = MemoryServiceImpl(config, MagicMock())
        svc.initialize()
        # 不应有从当前目录加载的事实
        assert svc._store.fact_count == 0
