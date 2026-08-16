"""Kimi 联网搜索采集器。

包装现有 ``services/kimi_web_search.py``（Moonshot API + ``$web_search`` 内置函数）。
凭证：环境变量 ``MOONSHOT_API_KEY`` 或 ``KIMI_API_KEY``。
"""

from __future__ import annotations

import os
from typing import Any

from loguru import logger

from long_earn.event_inference.collectors.base import CollectedItem


class KimiCollector:
    """Kimi 联网搜索采集器。

    依赖 ``services/kimi_web_search.kimi_web_search``。凭证缺失时 ``is_available``
    返回 False，collect 节点自动跳过。
    """

    name = "kimi"

    def __init__(self) -> None:
        self._available: bool | None = None

    @property
    def is_available(self) -> bool:
        """检查 Moonshot/Kimi API Key 是否配置。"""
        if self._available is not None:
            return self._available

        self._available = bool(
            os.environ.get("MOONSHOT_API_KEY") or os.environ.get("KIMI_API_KEY")
        )
        return self._available

    def collect(self, query: str, max_items: int = 10) -> list[CollectedItem]:
        """调用 Kimi ``$web_search`` 拉取联网搜索结果。

        Args:
            query: 搜索关键词
            max_items: 最多返回条数（Kimi 单次返回数由模型决定，此处做截断）

        Returns:
            原始素材列表；不可用或失败时返回空列表
        """
        if not self.is_available:
            return []

        # 延迟导入：kimi_web_search 依赖 openai SDK，避免无凭证环境加载失败
        from long_earn.services.kimi_web_search import kimi_web_search  # noqa: PLC0415

        try:
            raw_results: list[dict[str, Any]] = kimi_web_search(query)
        except Exception as e:
            logger.warning(f"[kimi] 联网搜索失败: {type(e).__name__}: {e}")
            return []

        items: list[CollectedItem] = []
        for raw in raw_results[:max_items]:
            content = str(raw.get("content", "")).strip()
            if not content:
                continue
            items.append(
                CollectedItem(
                    title=str(raw.get("title", "Kimi 搜索结果")),
                    content=content,
                    url=str(raw.get("url", "")),
                    source=self.name,
                )
            )
        return items
