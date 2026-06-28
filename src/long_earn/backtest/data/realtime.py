"""实时行情数据提供者（ADR-011 阶段 1）。

第三组数据接口，面向实时行情场景（快照 + 订阅），与面向历史面板的
:class:`DataProvider` 和面向市场情报的 :class:`MarketIntelligenceProvider`
并列。降级链：miniqmt（推送 + 快照）→ ciccwm（HTTP 轮询快照）。

与历史面板的本质差异：
  - 数据形态：单点 dict（tick 级）vs 批量 DataFrame（日级）
  - 调用模式：订阅-推送 + 同步快照 vs 同步拉取
  - 消费方：告警/监控/实时分析 vs 回测引擎
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Protocol

from loguru import logger

from long_earn.backtest.data import ciccwm_client as client
from long_earn.backtest.data.miniqmt_provider import MiniQmtClient
from long_earn.backtest.data.symbol import xt_to_ciccwm

if TYPE_CHECKING:
    pass


class RealtimeDataProvider(Protocol):
    """实时行情数据提供者接口（第三组接口，ADR-011）。"""

    @property
    def is_available(self) -> bool:
        """数据源是否可用。"""
        ...

    def get_latest_quote(self, symbol: str) -> dict[str, Any]:
        """获取最新行情快照（同步）。

        Args:
            symbol: 股票代码（xtquant 格式，如 600519.SH）

        Returns:
            行情字典，至少含 price/volume/time/open/high/low/preClose/source；
            失败返回空 dict。
        """
        ...

    def subscribe_quote(
        self,
        symbols: list[str],
        callback: Callable[[dict[str, Any]], None],
    ) -> str:
        """订阅实时行情推送（异步）。

        Args:
            symbols: 股票代码列表
            callback: 回调函数，每次 tick 到达时调用

        Returns:
            订阅 ID；不支持订阅时返回空字符串（调用方改用轮询 get_latest_quote）。
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

    _SUBSCRIBE_TIMEOUT = 30

    def __init__(self) -> None:
        self._client = MiniQmtClient.get()
        self._subscriptions: dict[str, dict[str, Any]] = {}

    @property
    def is_available(self) -> bool:
        return self._client.is_available

    def get_latest_quote(self, symbol: str) -> dict[str, Any]:
        """通过 get_full_tick 获取最新行情快照。"""
        if not self.is_available:
            return {}
        try:
            result = self._client.get_full_tick([symbol])
            if isinstance(result, dict) and symbol in result:
                tick = result[symbol]
                if isinstance(tick, dict):
                    return {
                        "price": float(tick.get("lastPrice", 0.0) or 0.0),
                        "volume": int(tick.get("volume", 0) or 0),
                        "time": str(tick.get("time", "")),
                        "open": float(tick.get("open", 0.0) or 0.0),
                        "high": float(tick.get("high", 0.0) or 0.0),
                        "low": float(tick.get("low", 0.0) or 0.0),
                        "preClose": float(tick.get("lastClose", 0.0) or 0.0),
                        "source": "miniqmt",
                    }
            return {}
        except Exception as e:
            logger.warning(f"miniqmt get_latest_quote 失败: {e}")
            return {}

    def subscribe_quote(
        self,
        symbols: list[str],
        callback: Callable[[dict[str, Any]], None],
    ) -> str:
        """通过 xtdata.subscribe_quote 订阅推送。"""
        if not self.is_available:
            return ""
        try:
            xtdata = self._client._ensure_xtdata()
            if xtdata is None:
                return ""
            sub_id = f"miniqmt_{abs(hash(tuple(symbols)))}"
            xtdata.subscribe_quote(symbols, callback=callback)
            self._subscriptions[sub_id] = {"symbols": symbols, "active": True}
            logger.info(f"miniqmt 订阅 {len(symbols)} 只股票: {sub_id}")
            return sub_id
        except Exception as e:
            logger.warning(f"miniqmt subscribe_quote 失败: {e}")
            return ""

    def unsubscribe(self, subscription_id: str) -> None:
        """通过 xtdata.unsubscribe_quote 取消订阅。"""
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

    无订阅能力（HTTP 无长连接），``subscribe_quote`` 返回空 ID。
    通过 ``get_latest_quote`` 单次 HTTP 拉取实现"近实时"查询。
    CI 友好（无本地依赖，仅需网络 + 凭证）。
    """

    def __init__(self) -> None:
        self._available: bool | None = None

    @property
    def is_available(self) -> bool:
        """检测 ciccwm 是否可用（凭证文件存在且 API Key 非空）。"""
        if self._available is not None:
            return self._available
        available = client.is_credential_available()
        if not available:
            logger.warning("ciccwm realtime 不可用：凭证文件缺失或 API Key 为空")
        self._available = available
        return self._available

    def get_latest_quote(self, symbol: str) -> dict[str, Any]:
        """通过 ciccwm fetch_info 获取最新行情快照。"""
        if not self.is_available:
            return {}
        try:
            code, market = xt_to_ciccwm(symbol)
        except ValueError as e:
            logger.warning(f"无法解析代码 {symbol}: {e}")
            return {}
        try:
            info = client.fetch_info(code, market)
        except client.CICCWMCredentialError:
            raise
        except Exception as e:
            logger.warning(f"ciccwm get_latest_quote 失败: {e}")
            return {}
        if not isinstance(info, dict) or not info.get("code"):
            return {}
        return {
            "price": float(info.get("price", info.get("lastPrice", 0.0)) or 0.0),
            "volume": int(info.get("volume", 0) or 0),
            "time": str(info.get("time", info.get("date", ""))),
            "open": float(info.get("open", 0.0) or 0.0),
            "high": float(info.get("high", 0.0) or 0.0),
            "low": float(info.get("low", 0.0) or 0.0),
            "preClose": float(
                info.get("preClose", info.get("lastClose", 0.0)) or 0.0
            ),
            "source": "ciccwm",
        }

    def subscribe_quote(
        self,
        _symbols: list[str],
        _callback: Callable[[dict[str, Any]], None],
    ) -> str:
        """ciccwm 不支持订阅（HTTP 轮询模式），返回空 ID。"""
        logger.info("ciccwm 不支持订阅（HTTP 轮询模式），请使用 get_latest_quote")
        return ""

    def unsubscribe(self, _subscription_id: str) -> None:
        """无订阅，空操作。"""


# ── Composite 实现 ───────────────────────────────────────────────────


class CompositeRealtimeProvider:
    """组合实时行情提供者：miniqmt → ciccwm 自动降级。

    数据获取策略：
    1. miniqmt 可用 → 使用 miniqmt（实时订阅 + get_full_tick 快照）
    2. miniqmt 不可用 → 降级到 ciccwm HTTP 轮询（仅快照，无订阅）
    """

    def __init__(self) -> None:
        self._miniqmt: MiniQmtRealtimeProvider | None = None
        self._ciccwm: CiccwmRealtimeProvider | None = None

    @property
    def miniqmt(self) -> MiniQmtRealtimeProvider:
        """延迟加载 miniqmt 实时提供者。"""
        if self._miniqmt is None:
            self._miniqmt = MiniQmtRealtimeProvider()
        return self._miniqmt

    @property
    def ciccwm(self) -> CiccwmRealtimeProvider:
        """延迟加载 ciccwm 实时提供者。"""
        if self._ciccwm is None:
            self._ciccwm = CiccwmRealtimeProvider()
        return self._ciccwm

    @property
    def is_available(self) -> bool:
        """任一数据源可用即视为可用。"""
        return self.miniqmt.is_available or self.ciccwm.is_available

    def get_latest_quote(self, symbol: str) -> dict[str, Any]:
        """获取最新行情快照（自动降级）。"""
        # 1. miniqmt 优先
        if self.miniqmt.is_available:
            quote = self.miniqmt.get_latest_quote(symbol)
            if quote:
                return quote
        # 2. ciccwm 降级
        if self.ciccwm.is_available:
            logger.info("[realtime] miniqmt 不可用，降级到 ciccwm HTTP 轮询")
            quote = self.ciccwm.get_latest_quote(symbol)
            if quote:
                return quote
        logger.warning("所有实时行情源均不可用")
        return {}

    def subscribe_quote(
        self,
        symbols: list[str],
        callback: Callable[[dict[str, Any]], None],
    ) -> str:
        """订阅实时行情推送（miniqmt 优先，不支持时返回空 ID）。"""
        if self.miniqmt.is_available:
            sub_id = self.miniqmt.subscribe_quote(symbols, callback)
            if sub_id:
                return sub_id
        # ciccwm 不支持订阅，返回空 ID
        logger.info("[realtime] 无可用订阅源，请使用 get_latest_quote 轮询")
        return ""

    def unsubscribe(self, subscription_id: str) -> None:
        """取消订阅（委托给 miniqmt）。"""
        self.miniqmt.unsubscribe(subscription_id)


def create_realtime_provider() -> CompositeRealtimeProvider:
    """工厂函数：创建组合实时行情提供者。"""
    return CompositeRealtimeProvider()
