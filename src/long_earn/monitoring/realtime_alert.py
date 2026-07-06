"""价格阈值告警监控器（ADR-011 阶段 1）。

订阅 :class:`RealtimeDataProvider`，在回调中检查价格是否突破阈值，
触发用户注册的告警回调。

告警为旁路消费者，不参与回测主流程，失败不影响策略计算。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from long_earn.backtest.data.realtime import RealtimeDataProvider


@dataclass
class PriceAlert:
    """单条价格告警规则。"""

    symbol: str
    threshold: float
    direction: str = "above"  # "above" 或 "below"
    triggered: bool = False


class PriceAlertMonitor:
    """价格阈值告警监控器。

    订阅 :class:`RealtimeDataProvider`，每次 tick 到达时检查所有告警规则，
    价格突破阈值时触发回调。

    Usage::

        monitor = PriceAlertMonitor(provider)
        monitor.add_alert("600519.SH", 1800.0, direction="above")
        monitor.on_trigger = lambda alert: print(f"告警: {alert.symbol} 突破 {alert.threshold}")
        monitor.start()
        # ... 运行期间触发 ...
        monitor.stop()
    """

    def __init__(self, provider: RealtimeDataProvider) -> None:
        self._provider = provider
        self._alerts: list[PriceAlert] = []
        self._subscription_id: str = ""
        self.on_trigger: Callable[[PriceAlert], None] | None = None

    def add_alert(
        self,
        symbol: str,
        threshold: float,
        direction: str = "above",
    ) -> None:
        """添加一条价格告警规则。

        Args:
            symbol: 股票代码（xtquant 格式）
            threshold: 价格阈值
            direction: "above"（突破上限）或 "below"（跌破下限）
        """
        if direction not in ("above", "below"):
            raise ValueError(f"direction 必须是 'above' 或 'below'， got: {direction}")
        self._alerts.append(
            PriceAlert(symbol=symbol, threshold=threshold, direction=direction)
        )
        logger.info(
            f"[alert] 添加告警: {symbol} {'>' if direction == 'above' else '<'} {threshold}"
        )

    def _handle_tick(self, tick: dict[str, object]) -> None:
        """tick 回调：检查所有告警规则。"""
        symbol = str(tick.get("symbol", ""))
        price = float(tick.get("price", 0.0) or 0.0)
        if price <= 0 or not symbol:
            return
        for alert in self._alerts:
            if alert.triggered or alert.symbol != symbol:
                continue
            triggered = (
                price >= alert.threshold
                if alert.direction == "above"
                else price <= alert.threshold
            )
            if triggered:
                alert.triggered = True
                logger.info(
                    f"[alert] 触发: {alert.symbol} 价格 {price} "
                    f"{'≥' if alert.direction == 'above' else '≤'} {alert.threshold}"
                )
                if self.on_trigger is not None:
                    try:
                        self.on_trigger(alert)
                    except Exception as e:
                        logger.warning(f"[alert] 回调异常: {e}")

    def start(self) -> str:
        """订阅并启动监控。

        Returns:
            订阅 ID；不支持订阅时返回空字符串（调用方改用轮询）。
        """
        symbols = list({a.symbol for a in self._alerts})
        if not symbols:
            logger.warning("[alert] 无告警规则，不启动监控")
            return ""
        self._subscription_id = self._provider.subscribe_quote(
            symbols, self._handle_tick
        )
        if self._subscription_id:
            logger.info(f"[alert] 监控已启动，订阅 {len(symbols)} 只股票")
        else:
            logger.info("[alert] 数据源不支持订阅，请使用轮询模式")
        return self._subscription_id

    def stop(self) -> None:
        """取消订阅，停止监控。"""
        if self._subscription_id:
            self._provider.unsubscribe(self._subscription_id)
            self._subscription_id = ""
            logger.info("[alert] 监控已停止")

    @property
    def alerts(self) -> list[PriceAlert]:
        """当前所有告警规则（含已触发）。"""
        return list(self._alerts)

    def clear_triggered(self) -> None:
        """清除已触发的告警（允许重新触发）。"""
        self._alerts = [a for a in self._alerts if not a.triggered]
