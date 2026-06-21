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
    """

    context: "RuntimeContext"
    memory: Any
    logger: Any
    _knowledge_cache: dict[str, list[str]]

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
