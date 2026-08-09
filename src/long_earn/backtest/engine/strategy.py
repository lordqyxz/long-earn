"""策略接口定义

提供一个面向 Agent 友好的状态化策略基类，支持基于 Polars Slab 的截面计算。
"""

import uuid
from abc import ABC, abstractmethod
from typing import Any

import polars as pl

from long_earn.backtest.domain.entities import ExecType, OrderEvent, SignalEvent
from long_earn.backtest.engine.visibility import VisibilityContext


class BaseStrategy(ABC):
    """
    策略基类

    设计目标：
    1. 消除 LLM 对索引偏移的认知负担
    2. 提供强类型的上下文访问
    3. 允许持有内部状态 (Stateful)
    """

    def __init__(self, strategy_id: str, config: dict[str, Any] | None = None):
        self.strategy_id = strategy_id
        self.config = config or {}
        self._state: dict[str, Any] = {}

    def init(self) -> None:  # noqa: B027
        """
        策略初始化钩子。用于定义策略内部状态。
        例如：self._last_signal_time = None
        """
        pass

    @abstractmethod
    def on_bar(
        self, bars: pl.DataFrame, context: VisibilityContext
    ) -> SignalEvent | None:
        """
        核心决策钩子：每当时间轴推进一个 Bar 时触发。

        Args:
            bars: 当前时刻所有候选股的截面数据 (Slab)，Index=symbol。
                  支持 Polars 向量化操作。
            context: 可见性上下文，用于安全地读取历史数据或单股价格。

        Returns:
            SignalEvent: 包含目标权重或信号的事件。如果本时刻不操作，返回 None。
        """
        pass

    def get_state(self, key: str, default: Any = None) -> Any:
        """获取策略内部状态"""
        return self._state.get(key, default)

    def set_state(self, key: str, value: Any) -> None:
        """设置策略内部状态"""
        self._state[key] = value

    # ── P1-08: 高级订单支持 ─────────────────────────────────────

    def submit_order(  # noqa: PLR0913
        self,
        symbol: str,
        order_type: str,
        quantity: float,
        *,
        exec_type: str = ExecType.MARKET,
        price: float | None = None,
        stop_price: float | None = None,
        oco_group_id: str = "",
        timestamp: Any = None,
    ) -> OrderEvent:
        """创建高级订单（LIMIT/STOP/STOP_LIMIT/OCO）。

        绕过 Portfolio 权重系统，直接生成 OrderEvent。策略可在 on_bar 中
        调用此方法并将返回的 OrderEvent 放入 SignalEvent.metadata["direct_orders"]
        列表中，引擎会直接提交给 Broker 而不经过 Portfolio 权重转换。

        Args:
            symbol: 标的代码
            order_type: BUY 或 SELL
            quantity: 数量（股）
            exec_type: 执行类型（MKT/LMT/STP/STL），默认市价
            price: 限价/止损价（MARKET 时为 None）
            stop_price: 止损触发价（STOP/STOP_LIMIT 时使用）
            oco_group_id: OCO 互斥组 ID
            timestamp: 订单时间戳（默认 None，由引擎填充）

        Returns:
            OrderEvent: 可直接提交给 Broker 的订单
        """
        return OrderEvent(
            timestamp=timestamp,
            trace_id=str(uuid.uuid4()),
            event_id=f"ord_{symbol}_{uuid.uuid4().hex[:8]}",
            symbol=symbol,
            order_type=order_type,
            quantity=quantity,
            price=price,
            order_id=f"ord_{uuid.uuid4().hex[:8]}",
            exec_type=exec_type,
            stop_price=stop_price,
            oco_group_id=oco_group_id,
        )
