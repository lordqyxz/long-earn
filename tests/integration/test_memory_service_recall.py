"""MemoryService recall 端到端集成测试

验证 RuntimeContext 中的 MemoryService 在生产实例下的完整链路：
- `remember` → `recall` 跨实例数据流
- 嵌入检索 fallback 路径（embed extra 未装时仍走纯 TF-IDF，零回归）
- 服务层与底层 MemoryStore 的契约对齐

与 test_memory_end_to_end.py（针对裸 MemoryStore）互补：本测试聚焦
"服务接口 + 依赖注入"层级，确保 MemoryServiceImpl 不破坏底层契约。
"""

from pathlib import Path
from typing import Any

import pytest

from long_earn.config import AppConfig
from long_earn.services.logger_service import LoggerServiceImpl
from long_earn.services.memory_service import MemoryServiceImpl


@pytest.fixture
def memory_service(tmp_path: Path) -> MemoryServiceImpl:
    """构造独立 MemoryServiceImpl，使用临时路径避免污染真实记忆"""
    config = AppConfig()
    config.memory_path = str(tmp_path / "test_memory.npz")
    config.init_dir = str(tmp_path / "nonexistent_init")  # 跳过 init 加载
    svc = MemoryServiceImpl(config, LoggerServiceImpl())
    svc.initialize()
    return svc


class TestMemoryServiceRecall:
    """MemoryService.recall 端到端集成"""

    def test_remember_then_recall_returns_stored_fact(
        self, memory_service: MemoryServiceImpl
    ):
        """remember 后立刻 recall 应能找回事实"""
        memory_service.remember("动量策略基于近期价格趋势", category="策略")
        memory_service.remember("Python 是动态类型语言", category="技术")

        results = memory_service.recall("动量", k=3)
        contents = [r["content"] for r in results]
        assert "动量策略基于近期价格趋势" in contents

    def test_recall_with_category_filter_excludes_other_categories(
        self, memory_service: MemoryServiceImpl
    ):
        """recall 透传 category 过滤到 store.search"""
        memory_service.remember("动量策略基于近期价格趋势", category="策略")
        memory_service.remember("Python 是动态类型语言", category="技术")

        results = memory_service.recall("动量", k=3, categories=["策略"])
        contents = [r["content"] for r in results]
        assert "动量策略基于近期价格趋势" in contents
        assert "Python 是动态类型语言" not in contents

    def test_recall_falls_back_to_tfidf_when_embed_extra_missing(
        self, memory_service: MemoryServiceImpl
    ):
        """嵌入检索器在默认环境（无 sentence-transformers）下应返回 None，recall 走 TF-IDF"""
        # CI 默认无 embed extra；本测试验证 fallback 不报错且仍返回正确结果
        memory_service.remember("低估值策略筛选 PE 偏低的股票", category="策略")
        retriever = memory_service._get_embedding_retriever()
        # 默认未安装 sentence-transformers → 返回 None；若安装则不报错即可
        assert retriever is None or retriever.is_available

        results = memory_service.recall("PE 估值", k=2)
        # 至少能检索到刚存入的事实（TF-IDF 或 hybrid 都能命中）
        assert len(results) >= 1
        assert any("低估值" in r["content"] for r in results)

    def test_recall_exception_returns_empty_not_raise(
        self, memory_service: MemoryServiceImpl
    ):
        """底层 store.search 抛异常时 recall 应捕获并返回空列表（容错契约）"""
        # 通过 monkey-patch 触发异常
        original_search = memory_service._store.search

        def _raise(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("simulated failure")

        memory_service._store.search = _raise  # type: ignore[method-assign]
        try:
            results = memory_service.recall("任意查询", k=3)
            assert results == []
        finally:
            memory_service._store.search = original_search  # type: ignore[method-assign]
