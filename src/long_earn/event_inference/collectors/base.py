"""采集器基类 — 多源新闻/资讯采集的统一接口。

采集器负责从外部数据源（Kimi 联网搜索 / ciccwm 热榜 / 腾讯新闻等）拉取
原始素材，产出 :class:`CollectedItem` 列表。后续 extract 节点用 LLM 将
原始素材抽取为结构化 Substance 事件。

采集器实现可注入（Protocol 鸭子类型），生产用 LLM/HTTP 实现，测试用
确定性 Fake，与 operator_dev 的 OperatorImplementer 同模式。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from loguru import logger


@dataclass(frozen=True)
class CollectedItem:
    """采集到的原始素材项。

    Attributes:
        title: 标题
        content: 正文内容（可能为长文本）
        url: 来源链接（可空）
        source: 数据源标识（如 "kimi" / "ciccwm_hot" / "ciccwm_topic"）
        published_at: 发布时间（ISO 字符串，可空，采集器无法判断时留空）
    """

    title: str
    content: str
    url: str = ""
    source: str = ""
    published_at: str = ""


class Collector(Protocol):
    """采集器接口 — 从单一数据源拉取与查询相关的原始素材。"""

    @property
    def name(self) -> str:
        """采集器唯一标识（如 "kimi" / "ciccwm_hot"）。"""
        ...

    @property
    def is_available(self) -> bool:
        """数据源是否可用（凭证/网络/依赖就绪）。

        不可用的采集器在 collect 节点被跳过，不阻塞流程。
        """
        ...

    def collect(self, query: str, max_items: int = 10) -> list[CollectedItem]:
        """按查询拉取原始素材。

        Args:
            query: 检索关键词（如 "贵州茅台 最新动态"）
            max_items: 最多返回条数

        Returns:
            原始素材列表；数据源不可用或无结果时返回空列表
        """
        ...


class CollectorRegistry:
    """采集器注册表 — 管理多个采集器，按名称查找。

    用法::

        registry = CollectorRegistry()
        registry.register(KimiCollector())
        registry.register(CiccwmHotCollector())

        items: list[CollectedItem] = []
        for collector in registry.available():
            items.extend(collector.collect("茅台"))
    """

    def __init__(self) -> None:
        self._collectors: dict[str, Collector] = {}

    def register(self, collector: Collector) -> None:
        """注册采集器；同名覆盖（后注册者生效）。"""
        self._collectors[collector.name] = collector

    def get(self, name: str) -> Collector | None:
        """按名称获取采集器。"""
        return self._collectors.get(name)

    def all(self) -> list[Collector]:
        """全部已注册采集器。"""
        return list(self._collectors.values())

    def available(self) -> list[Collector]:
        """仅返回 is_available 为 True 的采集器。"""
        return [c for c in self._collectors.values() if c.is_available]

    def collect_all(
        self, query: str, max_items_per_source: int = 10
    ) -> list[CollectedItem]:
        """对所有可用采集器并发拉取并合并结果。

        单个采集器异常不影响其他源（记录警告后继续），失败源的素材不进入结果。
        """
        items: list[CollectedItem] = []
        for collector in self.available():
            try:
                items.extend(collector.collect(query, max_items_per_source))
            except Exception as e:
                # 采集失败不阻塞流程，仅记录；调用方通过日志感知
                logger.warning(
                    f"[collectors] {collector.name} 采集失败: {type(e).__name__}: {e}"
                )
        return items

    @property
    def count(self) -> int:
        """已注册采集器数量。"""
        return len(self._collectors)

    @property
    def available_count(self) -> int:
        """可用采集器数量。"""
        return len(self.available())
