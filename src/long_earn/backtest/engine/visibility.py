"""可见性守护模块

负责严格控制回测过程中的数据可见性，从架构层面杜绝未来函数 (Look-ahead bias)。

性能优化（P0）：
- ``__init__`` 预排序 full_data by timestamp（一次 O(N log N)）
- 维护 ``_timestamps`` 列表 + ``_history_end_idx`` 指针
- ``set_time`` 用 bisect 推进指针（O(log T)）
- ``_get_history_slice`` 用 ``head(_history_end_idx)`` O(1) 切片
- ``read_current_slab`` 用预构建的 timestamp → row_range 索引 O(1) 切片
"""

import bisect
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
    """可见性守护者

    负责维护时间线并拦截所有违规的数据访问请求。

    性能优化：预排序 + bisect 指针推进，避免每 bar 全表 filter。
    复杂度从 O(T²·U) 降到 O(T·log T + T·U)。
    """

    def __init__(self, full_data: pl.DataFrame):
        """
        Args:
            full_data: 包含全部回测期间数据的 Polars DataFrame
                       期望结构: [timestamp, symbol, close, ...]
        """
        # 预排序 by timestamp（一次 O(N log N)），后续所有切片基于此顺序
        self._full_data = full_data.sort("timestamp")
        # 预提取 timestamps 列表供 bisect（O(N) 一次）
        self._timestamps: list[datetime] = (
            self._full_data.select("timestamp").to_series().to_list()
        )
        self.current_timestamp: datetime | None = None
        self._context = VisibilityContext(self)
        # 历史切片指针：指向 _full_data 中最后一个 <= current_timestamp 的行 +1
        self._history_end_idx: int = 0
        # 当前 slab 的行范围 [start, end)
        self._slab_start_idx: int = 0
        self._slab_end_idx: int = 0

    def set_time(self, timestamp: datetime) -> None:
        """推进时间轴

        用 bisect 在预排序的 timestamps 列表上查找，O(log T)。
        同时计算当前 slab 的行范围（同一 timestamp 的连续行区间）。
        """
        self.current_timestamp = timestamp
        # 历史切片：所有 timestamp <= current_timestamp 的行
        self._history_end_idx = bisect.bisect_right(self._timestamps, timestamp)
        # 当前 slab：所有 timestamp == current_timestamp 的行
        # 预排序后同一 timestamp 的行连续，用 bisect 定位区间
        self._slab_start_idx = bisect.bisect_left(self._timestamps, timestamp)
        self._slab_end_idx = self._history_end_idx

    def get_context(self) -> VisibilityContext:
        """获取对外暴露的只读上下文"""
        return self._context

    def read_scalar(self, symbol: str, field: str) -> float:
        """读取当前时刻的标量值"""
        if self.current_timestamp is None:
            raise FutureDataError("时间轴尚未初始化")

        # 从当前 slab 切片中过滤（slab 已是连续行切片，O(U)）
        slab = self.read_current_slab()
        val = (
            slab.filter(pl.col("symbol") == symbol)
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

        # 使用指针切片获取历史数据
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
        """获取截至当前时间戳的历史数据切片

        使用预排序 + 指针切片 O(1)，避免每 bar 全表 filter O(N)。
        """
        if self._history_end_idx == 0:
            return self._full_data.head(0)
        return self._full_data.head(self._history_end_idx)

    def read_current_slab(self) -> pl.DataFrame:
        """读取当前时刻的所有截面数据

        使用预排序后的连续行区间切片 O(1)，避免每 bar 全表 filter。
        """
        if self.current_timestamp is None:
            raise FutureDataError("时间轴尚未初始化")

        if self._slab_start_idx >= self._slab_end_idx:
            # 当前 timestamp 在数据中不存在
            return self._full_data.head(0)
        # polars slice 是半开区间 [start, end)
        return self._full_data.slice(self._slab_start_idx, self._slab_end_idx - self._slab_start_idx)
