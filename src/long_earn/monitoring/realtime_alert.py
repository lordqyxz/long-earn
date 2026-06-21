"""实时行情预警节点（TODO #3.3 实时数据对接的 demo 模块）

提供基于 RealtimeDataProvider 的价格阈值预警能力，作为独立模块供后续扩展。
不强制接入主图（LangGraph 节点），保持解耦。

使用方式：
    provider = create_realtime_provider()
    alert = PriceAlert(symbol="600519.SH", threshold=1800.0, direction="above")
    result = alert.check(provider)
    # result = {"triggered": True/False, "price": 1820.5, "message": "..."}
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from long_earn.backtest.data.realtime import (
    RealtimeDataProvider,
    create_realtime_provider,
)

logger = logging.getLogger(__name__)


@dataclass
class PriceAlert:
    """价格阈值预警。

    Attributes:
        symbol: 股票代码（如 600519.SH）
        threshold: 触发阈值
        direction: 触发方向，"above"（价格 >= 阈值）或 "below"（价格 <= 阈值）
        name: 预警名称（可选，用于日志标识）
    """

    symbol: str
    threshold: float
    direction: str = "above"
    name: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.direction not in ("above", "below"):
            raise ValueError(f"direction 必须是 'above' 或 'below'，得到: {self.direction}")
        if self.threshold <= 0:
            raise ValueError(f"threshold 必须大于 0，得到: {self.threshold}")

    def check(self, provider: RealtimeDataProvider | None = None) -> dict[str, Any]:
        """检查当前价格是否触发预警。

        Args:
            provider: 实时行情提供者；None 时自动创建

        Returns:
            {"triggered": bool, "price": float, "symbol": str,
             "threshold": float, "direction": str, "message": str, "time": str}
        """
        if provider is None:
            provider = create_realtime_provider()

        quote = provider.get_latest_quote(self.symbol)
        price = float(quote.get("price", 0.0))

        if price <= 0:
            logger.warning(f"预警 {self.name or self.symbol} 无法获取有效价格")
            return {
                "triggered": False,
                "price": 0.0,
                "symbol": self.symbol,
                "threshold": self.threshold,
                "direction": self.direction,
                "message": f"无法获取 {self.symbol} 的实时价格",
                "time": datetime.now().isoformat(),
            }

        triggered = (
            price >= self.threshold
            if self.direction == "above"
            else price <= self.threshold
        )

        message = (
            f"{'触发' if triggered else '未触发'}: {self.symbol} 当前价 {price}"
            f" {'>=' if self.direction == 'above' else '<='} 阈值 {self.threshold}"
        )

        if triggered:
            logger.info(message)

        return {
            "triggered": triggered,
            "price": price,
            "symbol": self.symbol,
            "threshold": self.threshold,
            "direction": self.direction,
            "message": message,
            "time": datetime.now().isoformat(),
        }


def check_alerts(
    alerts: list[PriceAlert],
    provider: RealtimeDataProvider | None = None,
) -> list[dict[str, Any]]:
    """批量检查多个预警。

    Args:
        alerts: 预警列表
        provider: 共享的实时行情提供者（避免重复创建）

    Returns:
        每个预警的检查结果列表
    """
    if provider is None:
        provider = create_realtime_provider()
    return [alert.check(provider) for alert in alerts]
