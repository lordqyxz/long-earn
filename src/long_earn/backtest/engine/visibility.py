"""可见性守护模块

负责严格控制回测过程中的数据可见性，从架构层面杜绝未来函数 (Look-ahead bias)。
"""

import logging
from datetime import datetime

import polars as pl

from long_earn.backtest.domain.exceptions import BacktestDomainError

logger = logging.getLogger(__name__)


class FutureDataError(BacktestDomainError):
    """尝试访问未来数据的异常"""

    pass


class VisibilityContext:
    """策略可见的数据上下文 (只读)"""

    def __init__(self, guard: "VisibilityGuard"):
        self._guard = guard

    @property
    def current_timestamp(self) -> datetime:
        ts = self._guard.current_timestamp
        if ts is None:
            return datetime.min
        return ts

    def get_price(self, symbol: str, field: str = "close") -> float:
        """获取当前时刻单只股票的价格"""
        return self._guard.read_scalar(symbol, field)

    def get_history(self, symbol: str, field: str, window: int) -> pl.Series:
        """
        获取单只股票的历史数据序列

        Args:
            symbol: 股票代码
            field: 字段名 (e.g. 'close')
            window: 回溯窗口大小
        """
        return self._guard.read_history(symbol, field, window)

    def get_current_slab(self) -> pl.DataFrame:
        """获取当前时刻所有股票的截面数据 (Slab)"""
        return self._guard.read_current_slab()


class VisibilityGuard:
    """
    可见性守护者

    负责维护时间线并拦截所有违规的数据访问请求。
    """

    def __init__(self, full_data: pl.DataFrame):
        """
        Args:
            full_data: 包含全部回测期间数据的 Polars DataFrame
                       期望结构: [timestamp, symbol, close, ...]
        """
        self._full_data = full_data
        self.current_timestamp: datetime | None = None
        self._context = VisibilityContext(self)

    def set_time(self, timestamp: datetime) -> None:
        """推进时间轴"""
        self.current_timestamp = timestamp

    def get_context(self) -> VisibilityContext:
        """获取对外暴露的只读上下文"""
        return self._context

    def read_scalar(self, symbol: str, field: str) -> float:
        """读取当前时刻的标量值"""
        if self.current_timestamp is None:
            raise FutureDataError("时间轴尚未初始化")

        # 过滤当前时间点和对应股票
        val = (
            self._full_data.filter(
                (pl.col("timestamp") == self.current_timestamp)
                & (pl.col("symbol") == symbol)
            )
            .select(field)
            .to_series()
        )

        if val.is_empty():
            return float("nan")
        result = val[0]
        return float(result) if result is not None else float("nan")

    def read_history(self, symbol: str, field: str, window: int) -> pl.Series:
        """读取历史数据序列"""
        if self.current_timestamp is None:
            raise FutureDataError("时间轴尚未初始化")

        # 核心逻辑：仅筛选 <= current_timestamp 的数据
        history = (
            self._full_data.filter(
                (pl.col("timestamp") <= self.current_timestamp)
                & (pl.col("symbol") == symbol)
            )
            .sort("timestamp", descending=False)
            .tail(window)
            .select(field)
            .to_series()
        )
        return history

    def read_current_slab(self) -> pl.DataFrame:
        """读取当前时刻的所有截面数据"""
        if self.current_timestamp is None:
            raise FutureDataError("时间轴尚未初始化")

        return self._full_data.filter(pl.col("timestamp") == self.current_timestamp)
