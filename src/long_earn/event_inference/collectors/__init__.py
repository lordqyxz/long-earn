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
]
