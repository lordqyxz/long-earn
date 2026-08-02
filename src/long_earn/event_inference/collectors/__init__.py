"""采集器实现 — Kimi 联网搜索 + ciccwm 热榜/专题资讯。

Kimi 采集器包装现有 ``tools/kimi_web_search.py``；ciccwm 采集器包装
``CiccwmDataProvider`` 的热榜与专题资讯能力（ADR-006 独占接口）。
"""

from long_earn.event_inference.collectors.base import (
    CollectedItem,
    Collector,
    CollectorRegistry,
)
from long_earn.event_inference.collectors.ciccwm_collector import (
    CiccwmHotCollector,
    CiccwmTopicCollector,
)
from long_earn.event_inference.collectors.kimi_collector import KimiCollector

__all__ = [
    "CiccwmHotCollector",
    "CiccwmTopicCollector",
    "CollectedItem",
    "Collector",
    "CollectorRegistry",
    "KimiCollector",
    "create_default_collector_registry",
]


def create_default_collector_registry(
    market_intelligence: object | None = None,
) -> CollectorRegistry:
    """构造默认采集器注册表（ADR-018）。

    注册 Kimi（有 API Key 时可用）与 ciccwm 热榜/专题（有情报源时可用）。
    不可用的采集器仍可注册——``collect_all`` 会跳过 ``is_available=False``。
    """
    registry = CollectorRegistry()
    registry.register(KimiCollector())
    if market_intelligence is not None:
        registry.register(CiccwmHotCollector(market_intelligence))  # type: ignore[arg-type]
        registry.register(CiccwmTopicCollector(market_intelligence))  # type: ignore[arg-type]
    return registry

