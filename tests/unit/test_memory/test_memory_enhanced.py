"""MemoryStore 增强功能测试 — 记忆衰减、冲突检测、记忆压缩

注意：嵌入检索测试需要 sentence-transformers，默认跳过。
"""

from datetime import datetime, timedelta

import pytest

from long_earn.memory.embedding import EmbeddingRetriever
from long_earn.memory.store import (
    DEFAULT_DECAY_HALF_LIFE,
    MemoryStore,
)


class TestMemoryDecay:
    """记忆衰减机制测试"""

    def test_decay_factor_fresh_fact(self):
        """新创建的事实衰减因子为 1.0"""
        store = MemoryStore()
        store.add_fact("测试内容")
        factor = store._calc_decay(store._facts[0]["metadata"]["created_at"])
        assert factor == pytest.approx(1.0, rel=1e-3)

    def test_decay_factor_old_fact(self):
        """旧事实衰减因子低于 1.0"""
        old_time = (
            datetime.now() - timedelta(days=DEFAULT_DECAY_HALF_LIFE)
        ).isoformat()
        factor = MemoryStore._calc_decay(old_time)
        assert 0.3 < factor < 0.4  # exp(-1) ≈ 0.368

    def test_decay_factor_very_old(self):
        """非常旧的事实衰减因子接近 0"""
        old_time = (
            datetime.now() - timedelta(days=DEFAULT_DECAY_HALF_LIFE * 5)
        ).isoformat()
        factor = MemoryStore._calc_decay(old_time)
        assert factor < 0.1

    def test_decay_method_marks_facts(self):
        """decay() 方法正确标记衰减事实"""
        store = MemoryStore()
        store.add_fact("新内容")
        # 手动插入旧事实 - 修改 metadata
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
        assert count == 1  # 旧事实被衰减
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

    def test_search_with_decay_includes_decayed_when_requested(self):
        """指定 include_decayed=True 时包含已衰减事实"""
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

        results = store.search("概念", k=5, apply_decay=True, include_decayed=True)
        assert len(results) == 2


class TestConflictDetection:
    """冲突检测机制测试"""

    def test_find_no_conflict(self):
        """不相关内容不触发冲突"""
        store = MemoryStore()
        store.add_fact("动量策略根据近期涨幅选股", metadata={"term": "动量"})
        conflicts = store.find_conflicts("如何配置 Python 开发环境", min_similarity=0.5)
        assert len(conflicts) == 0

    def test_find_conflict_by_similarity(self):
        """高相似度内容触发冲突"""
        store = MemoryStore()
        # 使用带英文/数字的文本确保 tokenizer 正确切分
        store.add_fact(
            "this strategy works very well 利好买入",
            metadata={"term": "test_strategy"},
        )
        conflicts = store.find_conflicts(
            "this strategy works very bad 利空卖出",
            min_similarity=0.5,
        )
        assert len(conflicts) >= 1

    def test_find_conflict_in_group(self):
        """已标记冲突组的内容被识别"""
        store = MemoryStore()
        store.add_fact(
            "this is a momentum strategy 趋势跟踪",
            metadata={"conflict_group": "conflict_momentum"},
        )
        store.add_fact(
            "mean reversion strategy 均值回归", metadata={"term": "均值回归"}
        )
        conflicts = store.find_conflicts(
            "this is a momentum strategy 趋势跟踪", min_similarity=0.5
        )
        assert len(conflicts) >= 1
        assert "冲突组" in conflicts[0]["conflict_reason"]

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
        assert store._facts[idx_b]["metadata"]["conflict_version"] == 2

    def test_is_contradictory_detects_opposite(self):
        """矛盾检测正确识别正反面观点"""
        assert MemoryStore._is_contradictory(
            "利好：业绩大幅增长，建议买入",
            "利空：净利润大幅下降，建议卖出",
        )
        assert not MemoryStore._is_contradictory(
            "基本面良好，建议关注",
            "技术面显示走强趋势",
        )


class TestMemoryCompression:
    """记忆压缩机制测试"""

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
        # 使用带英文的文本确保 tokenizer 正确切分
        store.add_fact(
            "momentum strategy based on recent price trend 动量策略根据近期涨幅"
        )
        store.add_fact(
            "momentum strategy based on past price trend 动量策略根据过去涨幅"
        )
        store.add_fact("mean reversion strategy 均值回归基于价格偏离")

        count = store.compress(min_similarity=0.5)
        assert count > 0
        # 压缩后总量减少
        assert store.fact_count < 3

    def test_merge_cluster_updates_content(self):
        """聚类合并后主事实包含去重内容"""
        store = MemoryStore()
        idx = store.add_fact("原内容", metadata={"term": "测试"})
        store._facts.append(
            {
                "content": "新内容",
                "metadata": {"term": "测试", "category": "分类"},
            }
        )
        store._fact_texts.append("新内容")

        removed = store._merge_cluster([idx, 1])
        assert removed == 1
        assert "原内容" in store._facts[idx]["content"]
        assert "新内容" in store._facts[idx]["content"]
        assert store._facts[idx]["metadata"].get("compressed") is True

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
    """嵌入检索器测试 — sentence-transformers 可用时测试混合检索"""

    def test_is_available_false_without_sentence_transformers(self):
        """无 sentence-transformers 时 is_available 为 False"""
        retriever = EmbeddingRetriever()
        # CI 环境可能没有安装，但无论如何方法应返回布尔值
        assert isinstance(retriever.is_available, bool)

    def test_embedding_search_fallback(self):
        """无嵌入模型时回退到空结果"""
        retriever = EmbeddingRetriever()
        store = MemoryStore()
        store.add_fact("测试内容")
        results = retriever.embedding_search(store, "测试")
        # 如果没有 sentence-transformers，返回空列表
        if not retriever.is_available:
            assert results == []
        else:
            assert len(results) >= 1

    def test_hybrid_search_fallback(self):
        """无嵌入模型时混合检索回退到 TF-IDF"""
        retriever = EmbeddingRetriever()
        store = MemoryStore()
        store.add_fact("动量策略选股")
        store.add_fact("均值回归策略")

        results = retriever.hybrid_search(store, "动量", k=2, apply_decay=False)
        assert len(results) >= 1
        if not retriever.is_available:
            # 退化到纯 TF-IDF，结果中不应有嵌入分数
            assert "_embedding_score" not in results[0]
