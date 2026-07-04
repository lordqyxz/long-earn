"""共享混入类 — 提供跨 Agent 的知识检索能力"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext


class KnowledgeContextMixin:
    """知识上下文混入 — 统一的知识检索和缓存逻辑

    使用方式:
        class MyAgent(KnowledgeContextMixin):
            def __init__(self, context: RuntimeContext):
                self.context = context
                self.memory = context.memory
                self.logger = context.logger
                self._knowledge_cache: dict[str, list[str]] = {}
                self._event_cache: dict[str, list[str]] = {}
    """

    context: "RuntimeContext"
    memory: Any
    logger: Any
    _knowledge_cache: dict[str, list[str]]
    _event_cache: dict[str, list[str]]

    def _search_knowledge(self, query: str, **kwargs) -> list[str]:
        """搜索知识库"""
        try:
            return self.memory.search(query, k=3, **kwargs)
        except Exception:
            if self.logger:
                self.logger.warning(f"搜索知识库失败: {query}")
            return []

    def _get_knowledge_context(
        self,
        query: str,
        node_type: str | None = None,
        **search_kwargs,
    ) -> str:
        """获取知识库上下文（带缓存）

        Args:
            query: 搜索查询
            node_type: 节点类型，用于缓存键和搜索参数
            **search_kwargs: 传递给 knowledge_service.search() 的额外参数
        """
        cache_key = f"{node_type}:{query}" if node_type else query

        if cache_key in self._knowledge_cache:
            return "\n".join(self._knowledge_cache[cache_key])

        results = self._search_knowledge(query, **search_kwargs)
        if results:
            self._knowledge_cache[cache_key] = results
            return "\n".join(results)
        return ""

    # ── 事件上下文（ADR-007 Phase 3）──────────────────────────

    def _get_event_context(
        self,
        query: str,
        k: int = 5,
        use_cache: bool = True,
    ) -> str:
        """获取相关市场事件上下文（WorldInfo 激活引擎）。

        与 ``_get_knowledge_context`` 的区别：走关键词触发 + conflict_group 互斥，
        专门召回 EVENT/RELATION 形态物质，把"相关市场事件"注入 prompt。

        Args:
            query: 触发文本（股票名/代码、策略主题）
            k: 返回物质数上限
            use_cache: 是否使用缓存（同一 query 二次调用命中缓存）
        """
        cache_key = f"event:{query}"
        if use_cache and cache_key in self._event_cache:
            return "\n".join(self._event_cache[cache_key]) if self._event_cache[cache_key] else ""

        if not hasattr(self.memory, "activate_events"):
            return ""

        try:
            events = self.memory.activate_events(query, k=k)
        except Exception:
            if self.logger:
                self.logger.warning(f"激活事件上下文失败: {query}")
            events = []

        if use_cache:
            self._event_cache[cache_key] = events

        return "\n".join(events) if events else ""
