"""ciccwm 热榜/专题资讯采集器。

包装 :class:`CiccwmDataProvider` 的热榜（``get_hot_rank``）与专题资讯
（``get_topic_news``）独占能力（ADR-006）。ciccwm 不可用时（凭证缺失）
``is_available`` 返回 False，自动跳过。

两类采集器均接受 :class:`MarketIntelligenceProvider` 实例（可注入测试 Fake），
避免对 ciccwm 具体实现的硬依赖。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from long_earn.event_inference.collectors.base import CollectedItem

if TYPE_CHECKING:
    from long_earn.backtest.data.provider import MarketIntelligenceProvider

# 资讯正文字段候选名（ciccwm 不同接口字段名不统一）
_CONTENT_FIELD_CANDIDATES: tuple[str, ...] = (
    "content",
    "summary",
    "digest",
    "description",
    "brief",
    "abstract",
)
_TITLE_FIELD_CANDIDATES: tuple[str, ...] = ("title", "name", "subject")
_URL_FIELD_CANDIDATES: tuple[str, ...] = (
    "redirect_url",
    "url",
    "out_detail_url",
    "detail_url",
)


def _first_available(
    record: dict[str, Any], candidates: tuple[str, ...], default: str = ""
) -> str:
    """从记录中按候选字段名顺序取第一个非空值。"""
    for key in candidates:
        value = record.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return default


def _dataframe_to_items(
    df: Any,
    source: str,
    max_items: int,
) -> list[CollectedItem]:
    """将 ciccwm 返回的 DataFrame 转为 CollectedItem 列表。"""
    if df is None or getattr(df, "empty", True):
        return []

    items: list[CollectedItem] = []
    # iterrows 返回 (index, Series)；逐行提取字段
    for _, row in df.iterrows():
        record = {k: row[k] for k in df.columns}
        title = _first_available(record, _TITLE_FIELD_CANDIDATES)
        content = _first_available(record, _CONTENT_FIELD_CANDIDATES, default=title)
        url = _first_available(record, _URL_FIELD_CANDIDATES)
        if not content:
            continue
        items.append(
            CollectedItem(
                title=title or "ciccwm 资讯",
                content=content,
                url=url,
                source=source,
            )
        )
        if len(items) >= max_items:
            break
    return items


class CiccwmHotCollector:
    """ciccwm 今日热榜采集器。"""

    name = "ciccwm_hot"

    def __init__(self, provider: MarketIntelligenceProvider) -> None:
        self._provider = provider

    @property
    def is_available(self) -> bool:
        """ciccwm 凭证是否就绪。"""
        try:
            return self._provider.is_available
        except Exception:
            return False

    def collect(self, query: str, max_items: int = 10) -> list[CollectedItem]:  # noqa: ARG002
        """拉取今日热榜。

        热榜是全局榜单，与 ``query`` 无关（query 参数保留以符合 Collector 协议）。
        """
        if not self.is_available:
            return []
        try:
            df = self._provider.get_hot_rank(page_size=min(max_items, 20))
        except Exception as e:
            logger.warning(f"[ciccwm_hot] 热榜采集失败: {type(e).__name__}: {e}")
            return []
        return _dataframe_to_items(df, self.name, max_items)


class CiccwmTopicCollector:
    """ciccwm 专题资讯采集器。"""

    name = "ciccwm_topic"

    def __init__(self, provider: MarketIntelligenceProvider) -> None:
        self._provider = provider

    @property
    def is_available(self) -> bool:
        """ciccwm 凭证是否就绪。"""
        try:
            return self._provider.is_available
        except Exception:
            return False

    def collect(self, query: str, max_items: int = 10) -> list[CollectedItem]:  # noqa: ARG002
        """拉取专题资讯列表。

        专题资讯为全局流，与 ``query`` 无关（query 参数保留以符合 Collector 协议）。
        """
        if not self.is_available:
            return []
        try:
            df = self._provider.get_topic_news(page_size=min(max_items, 30))
        except Exception as e:
            logger.warning(f"[ciccwm_topic] 专题资讯采集失败: {type(e).__name__}: {e}")
            return []
        return _dataframe_to_items(df, self.name, max_items)
