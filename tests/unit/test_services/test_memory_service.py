"""MemoryServiceImpl 接口契约测试

聚焦 recall() 的检索策略分支：
- embed extra 不可用时（默认环境）回退纯 TF-IDF
- embed extra 可用时走 EmbeddingRetriever.hybrid_search（用 mock 验证调用）
- 检索异常被捕获并返回空列表（不向上抛）

遵循 CLAUDE.md 测试原则：只测接口层契约，不重复声明 store/embedding 内部逻辑。
"""

from unittest.mock import MagicMock, patch

from long_earn.services.memory_service import MemoryServiceImpl


def _make_service() -> MemoryServiceImpl:
    """构造测试用 MemoryServiceImpl（不触发真实记忆初始化）。"""
    config = MagicMock()
    config.memory_path = "/tmp/nonexistent_memory_service_test.npz"
    config.init_dir = "./init"
    logger = MagicMock()
    return MemoryServiceImpl(config, logger)


class TestRecallFallback:
    """recall() 在嵌入检索不可用时回退 TF-IDF"""

    def test_recall_returns_tfidf_results_when_embedding_unavailable(self):
        """embed extra 缺失时 recall 等价于 store.search"""
        svc = _make_service()
        svc._store.add_fact("动量策略选股", metadata={"term": "动量"})
        svc._store.add_fact("价值投资", metadata={"term": "价值"})

        # 嵌入检索器不可用（默认环境未装 sentence-transformers）
        assert svc._get_embedding_retriever() is None

        results = svc.recall("动量选股", k=2)
        assert len(results) >= 1
        assert "动量" in results[0]["content"]

    def test_recall_exception_returns_empty(self):
        """store.search 抛异常时 recall 捕获并返回空列表（不向上抛）"""
        svc = _make_service()
        with patch.object(
            svc._store, "search", side_effect=RuntimeError("boom")
        ):
            results = svc.recall("任意查询", k=3)
        assert results == []


class TestRecallHybrid:
    """recall() 在嵌入检索可用时走 hybrid_search"""

    def test_recall_uses_hybrid_search_when_embedding_available(self):
        """embed extra 可用时 recall 调用 EmbeddingRetriever.hybrid_search"""
        svc = _make_service()
        svc._store.add_fact("动量策略选股", metadata={"term": "动量"})

        fake_retriever = MagicMock()
        fake_retriever.is_available = True
        fake_retriever.hybrid_search.return_value = [
            {"content": "hybrid-result", "metadata": {}, "similarity": 0.9}
        ]
        svc._embedding = fake_retriever

        results = svc.recall("动量", k=1)
        assert results == [
            {"content": "hybrid-result", "metadata": {}, "similarity": 0.9}
        ]
        fake_retriever.hybrid_search.assert_called_once()
        call_kwargs = fake_retriever.hybrid_search.call_args
        assert call_kwargs.kwargs["k"] == 1
        # 默认融合权重 0.5
        assert call_kwargs.kwargs["alpha"] == 0.5

    def test_recall_alpha_override_via_filters(self):
        """filters 中的 alpha 覆盖默认融合权重，且不会重复透传进 search_kwargs"""
        svc = _make_service()
        svc._store.add_fact("动量策略", metadata={"term": "动量"})
        fake_retriever = MagicMock()
        fake_retriever.is_available = True
        fake_retriever.hybrid_search.return_value = []
        svc._embedding = fake_retriever

        # alpha 经 filters 传入，应被弹出作为 hybrid_search 的命名参数
        # （若未弹出会重复进 **search_kwargs 触发 TypeError，调用成功即证明已弹出）
        svc.recall("动量", k=1, alpha=0.8)
        call_kwargs = fake_retriever.hybrid_search.call_args
        assert call_kwargs.kwargs["alpha"] == 0.8

    def test_recall_hybrid_exception_returns_empty(self):
        """hybrid_search 抛异常时 recall 捕获并返回空列表"""
        svc = _make_service()
        svc._store.add_fact("动量策略", metadata={"term": "动量"})
        fake_retriever = MagicMock()
        fake_retriever.is_available = True
        fake_retriever.hybrid_search.side_effect = RuntimeError("embed boom")
        svc._embedding = fake_retriever

        assert svc.recall("动量", k=1) == []
