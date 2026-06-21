"""实时行情数据提供者（TODO #3.3 实时数据对接）

提供统一的实时行情获取接口，支持多数据源自动降级：
  miniqmt (xtdata.subscribe_quote / get_full_tick) → ciccwm (HTTP 轮询)

架构设计：
  - RealtimeDataProvider Protocol：统一接口，上层服务只依赖此接口
  - MiniQmtRealtimeProvider：基于 xtdata 的实时行情（需 miniQMT 客户端在线）
  - CiccwmRealtimeProvider：基于 ciccwm HTTP API 的轮询降级（CI 友好）
  - CompositeRealtimeProvider：按优先级自动选择数据源
  - 工厂函数 create_realtime_provider()：根据环境自动创建最佳提供者

使用方式：
    provider = create_realtime_provider()
    quote = provider.get_latest_quote("600519.SH")
    sub_id = provider.subscribe_quote(["600519.SH"], callback=my_handler)
    provider.unsubscribe(sub_id)
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Protocol

from long_earn.backtest.data.ciccwm_provider import CiccwmDataProvider
from long_earn.backtest.data.miniqmt_provider import MiniQmtClient

logger = logging.getLogger(__name__)

# ── 常量 ──────────────────────────────────────────────────────────────
_DEFAULT_POLL_INTERVAL = 5.0  # ciccwm 轮询间隔（秒）
_SUBSCRIBE_TIMEOUT = 30  # subscribe 超时（秒）


class RealtimeDataProvider(Protocol):
    """实时行情数据提供者统一接口。"""

    @property
    def is_available(self) -> bool:
        """数据源是否可用。"""
        ...

    def get_latest_quote(self, symbol: str) -> dict[str, Any]:
        """获取指定股票的最新行情快照。

        Args:
            symbol: 股票代码（xtquant 格式，如 600519.SH）

        Returns:
            行情字典，至少含 price / volume / time 字段；失败返回空 dict
        """
        ...

    def subscribe_quote(
        self,
        symbols: list[str],
        callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        """订阅实时行情推送。

        Args:
            symbols: 股票代码列表
            callback: 可选回调函数，每次 tick 到达时调用

        Returns:
            订阅 ID（用于取消订阅）
        """
        ...

    def unsubscribe(self, subscription_id: str) -> None:
        """取消订阅。"""
        ...


# ── MiniQmt 实现 ──────────────────────────────────────────────────────


class MiniQmtRealtimeProvider:
    """基于 xtdata 的实时行情提供者。

    依赖 miniQMT 客户端在线；不可用时 ``is_available`` 返回 False，
    所有方法返回空值（不抛异常）。
    """

    def __init__(self) -> None:
        self._client = MiniQmtClient.get()
        self._subscriptions: dict[str, Any] = {}

    @property
    def is_available(self) -> bool:
        return self._client.is_available

    def get_latest_quote(self, symbol: str) -> dict[str, Any]:
        if not self.is_available:
            return {}
        try:
            result = self._client.get_full_tick([symbol])
            if isinstance(result, dict) and symbol in result:
                tick = result[symbol]
                if isinstance(tick, dict):
                    return {
                        "price": tick.get("lastPrice", 0.0),
                        "volume": tick.get("volume", 0),
                        "time": tick.get("time", ""),
                        "open": tick.get("open", 0.0),
                        "high": tick.get("high", 0.0),
                        "low": tick.get("low", 0.0),
                        "preClose": tick.get("lastClose", 0.0),
                        "source": "miniqmt",
                    }
            return {}
        except Exception as e:
            logger.warning(f"miniqmt get_latest_quote 失败: {e}")
            return {}

    def subscribe_quote(
        self,
        symbols: list[str],
        callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        if not self.is_available:
            return ""
        try:
            xtdata = self._client._ensure_xtdata()
            if xtdata is None:
                return ""
            sub_id = f"miniqmt_{hash(tuple(symbols))}"
            xtdata.subscribe_quote(
                symbols, callback=callback or (lambda _d: None)
            )
            self._subscriptions[sub_id] = {"symbols": symbols, "active": True}
            logger.info(f"miniqmt 订阅 {len(symbols)} 只股票: {sub_id}")
            return sub_id
        except Exception as e:
            logger.warning(f"miniqmt subscribe_quote 失败: {e}")
            return ""

    def unsubscribe(self, subscription_id: str) -> None:
        if not self.is_available or subscription_id not in self._subscriptions:
            return
        try:
            xtdata = self._client._ensure_xtdata()
            if xtdata is not None:
                xtdata.unsubscribe_quote(
                    self._subscriptions[subscription_id]["symbols"]
                )
            self._subscriptions[subscription_id]["active"] = False
            logger.info(f"miniqmt 取消订阅: {subscription_id}")
        except Exception as e:
            logger.warning(f"miniqmt unsubscribe 失败: {e}")


# ── Ciccwm 轮询实现 ──────────────────────────────────────────────────


class CiccwmRealtimeProvider:
    """基于 ciccwm HTTP API 的轮询实时行情提供者。

    无订阅能力（HTTP 无长连接），``subscribe_quote`` 返回空 ID；
    通过 ``get_latest_quote`` 单次 HTTP 拉取实现"近实时"查询。
    CI 友好（无本地依赖，仅需网络 + 凭证）。
    """

    def __init__(self) -> None:
        self._provider: Any = None

    @property
    def is_available(self) -> bool:
        try:
            if self._provider is None:
                self._provider = CiccwmDataProvider()
            return self._provider.is_available
        except Exception:
            return False

    def get_latest_quote(self, symbol: str) -> dict[str, Any]:
        if not self.is_available or self._provider is None:
            return {}
        try:
            info = self._provider.get_info(symbol)
            if isinstance(info, dict) and info.get("code"):
                return {
                    "price": float(info.get("price", info.get("lastPrice", 0.0))),
                    "volume": int(info.get("volume", 0)),
                    "time": str(info.get("time", info.get("date", ""))),
                    "open": float(info.get("open", 0.0)),
                    "high": float(info.get("high", 0.0)),
                    "low": float(info.get("low", 0.0)),
                    "preClose": float(info.get("preClose", info.get("lastClose", 0.0))),
                    "source": "ciccwm",
                }
            return {}
        except Exception as e:
            logger.warning(f"ciccwm get_latest_quote 失败: {e}")
            return {}

    def subscribe_quote(
        self,
        _symbols: list[str],
        _callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        logger.info("ciccwm 不支持订阅（HTTP 轮询模式），请使用 get_latest_quote")
        return ""

    def unsubscribe(self, _subscription_id: str) -> None:
        pass


# ── Composite 实现 ───────────────────────────────────────────────────


class CompositeRealtimeProvider:
    """组合实时行情提供者：miniqmt → ciccwm 自动降级。

    数据获取策略：
    1. miniqmt 可用 → 使用 miniqmt（实时订阅 + get_full_tick）
    2. miniqmt 不可用 → 降级到 ciccwm HTTP 轮询
    """

    def __init__(self) -> None:
        self._miniqmt: MiniQmtRealtimeProvider | None = None
        self._ciccwm: CiccwmRealtimeProvider | None = None

    @property
    def _mq(self) -> MiniQmtRealtimeProvider:
        if self._miniqmt is None:
            self._miniqmt = MiniQmtRealtimeProvider()
        return self._miniqmt

    @property
    def _cc(self) -> CiccwmRealtimeProvider:
        if self._ciccwm is None:
            self._ciccwm = CiccwmRealtimeProvider()
        return self._ciccwm

    @property
    def is_available(self) -> bool:
        return self._mq.is_available or self._cc.is_available

    def get_latest_quote(self, symbol: str) -> dict[str, Any]:
        if self._mq.is_available:
            result = self._mq.get_latest_quote(symbol)
            if result:
                return result
        if self._cc.is_available:
            return self._cc.get_latest_quote(symbol)
        return {}

    def subscribe_quote(
        self,
        symbols: list[str],
        callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> str:
        if self._mq.is_available:
            return self._mq.subscribe_quote(symbols, callback)
        logger.warning("miniqmt 不可用，订阅不可用（ciccwm 不支持订阅）")
        return ""

    def unsubscribe(self, subscription_id: str) -> None:
        if self._mq.is_available:
            self._mq.unsubscribe(subscription_id)


# ── 工厂函数 ─────────────────────────────────────────────────────────


def create_realtime_provider() -> CompositeRealtimeProvider:
    """创建最佳实时行情提供者（自动降级）。"""
    return CompositeRealtimeProvider()
