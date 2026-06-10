"""策略接口定义

提供一个面向 Agent 友好的状态化策略基类，支持基于 Polars Slab 的截面计算。
"""

from abc import ABC, abstractmethod
from typing import Any

import polars as pl

from long_earn.backtest.domain.entities import SignalEvent
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
