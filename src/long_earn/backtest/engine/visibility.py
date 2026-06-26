"""可见性守护模块

负责严格控制回测过程中的数据可见性，从架构层面杜绝未来函数 (Look-ahead bias)。
"""

from datetime import datetime

import polars as pl

from long_earn.backtest.domain.exceptions import BacktestDomainError


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

    def get_history_df(self) -> pl.DataFrame:
        """获取截至当前时刻的所有历史数据（多字段）

        返回的 DataFrame 仅包含 timestamp <= current_timestamp 的数据，
        从架构层面保证无未来函数风险。
        """
        return self._guard.read_history_df()

    def get_current_slab(self) -> pl.DataFrame:
        """获取当前时刻所有股票的截面数据 (Slab)"""
        return self._guard.read_current_slab()


class VisibilityGuard:
    """
    可见性守护者

    负责维护时间线并拦截所有违规的数据访问请求。
    缓存截至当前时间戳的历史数据切片，避免重复过滤全量数据。
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
        # 缓存：上次时间戳及其对应的历史数据切片
        self._cached_timestamp: datetime | None = None
        self._cached_history: pl.DataFrame | None = None

    def set_time(self, timestamp: datetime) -> None:
        """推进时间轴"""
        self.current_timestamp = timestamp
        # 时间推进时使缓存失效（新时间戳可能包含更多数据）
        if self._cached_timestamp != timestamp:
            self._cached_timestamp = None
            self._cached_history = None

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

        # 使用缓存的历史数据切片
        history = self._get_history_slice()

        # 核心逻辑：仅筛选 <= current_timestamp 的数据
        result = (
            history.filter(pl.col("symbol") == symbol)
            .sort("timestamp", descending=False)
            .tail(window)
            .select(field)
            .to_series()
        )
        return result

    def read_history_df(self) -> pl.DataFrame:
        """读取截至当前时刻的所有历史数据（多字段）"""
        if self.current_timestamp is None:
            raise FutureDataError("时间轴尚未初始化")

        return self._get_history_slice()

    def _get_history_slice(self) -> pl.DataFrame:
        """获取截至当前时间戳的历史数据切片（带缓存）"""
        if (
            self._cached_history is not None
            and self._cached_timestamp == self.current_timestamp
        ):
            return self._cached_history

        self._cached_history = self._full_data.filter(
            pl.col("timestamp") <= self.current_timestamp
        )
        self._cached_timestamp = self.current_timestamp
        return self._cached_history

    def read_current_slab(self) -> pl.DataFrame:
        """读取当前时刻的所有截面数据"""
        if self.current_timestamp is None:
            raise FutureDataError("时间轴尚未初始化")

        return self._full_data.filter(pl.col("timestamp") == self.current_timestamp)
