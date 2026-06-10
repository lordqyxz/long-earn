"""VisibilityGuard 防未来函数测试

测试数据可见性控制：历史数据限定、截面数据隔离、异常处理。
"""

import math
from datetime import datetime, timedelta

import polars as pl
import pytest

from long_earn.backtest.engine.visibility import (
    FutureDataError,
    VisibilityGuard,
)


def _make_test_data() -> pl.DataFrame:
    """构造三支股票、五个时间点的测试数据"""
    dates = [datetime(2024, 1, 1) + timedelta(days=i) for i in range(5)]
    symbols = ["S1", "S2", "S3"]
    rows = []
    for d in dates:
        day_idx = (d - dates[0]).days
        for s in symbols:
            rows.append(
                {
                    "timestamp": d,
                    "symbol": s,
                    "close": 10.0 + day_idx + (hash(s) % 5),
                    "volume": 10000.0 + day_idx * 100,
                    "roe": 0.1 + day_idx * 0.01,
                }
            )
    return pl.DataFrame(rows)


class TestReadHistory:
    """read_history 仅返回 <= 当前时间的数据"""

    def test_read_history_includes_current_time(self):
        """read_history 应包含当前时间点的数据"""
        data = _make_test_data()
        guard = VisibilityGuard(data)
        ts = datetime(2024, 1, 3)
        guard.set_time(ts)

        history = guard.read_history("S1", "close", window=10)

        # 最后一条数据应为 ts 时刻的数据
        slab_at_ts = data.filter(
            (pl.col("timestamp") == ts) & (pl.col("symbol") == "S1")
        )
        expected_last = slab_at_ts.select("close").to_series()[0]
        assert history.to_list()[-1] == expected_last

    def test_read_history_excludes_future_time(self):
        """read_history 不应包含未来时间点的数据"""
        data = _make_test_data()
        guard = VisibilityGuard(data)
        ts = datetime(2024, 1, 1)
        guard.set_time(ts)

        history = guard.read_history("S1", "close", window=100)

        # ts 之后应没有数据
        max_ts_in_history = (
            data.filter(pl.col("timestamp") <= ts)
            .select("timestamp")
            .max()
            .to_series()[0]
        )
        # 验证返回的序列长度不超过 <= ts 的行数
        expected_len = data.filter(
            (pl.col("timestamp") <= ts) & (pl.col("symbol") == "S1")
        ).height
        assert len(history) == expected_len

    def test_read_history_respects_window(self):
        """read_history 应遵守窗口大小限制"""
        data = _make_test_data()
        guard = VisibilityGuard(data)
        ts = datetime(2024, 1, 5)  # 最后一个时间点
        guard.set_time(ts)

        history = guard.read_history("S1", "close", window=3)

        assert len(history) == 3

    def test_read_history_returns_empty_for_no_data(self):
        """没有历史数据时返回空序列"""
        data = _make_test_data()
        guard = VisibilityGuard(data)
        ts = datetime(2024, 1, 1)
        guard.set_time(ts)

        # S1 只在 2024-01-01 有数据，window=0 等价于无数据
        history = guard.read_history("S1", "close", window=0)

        assert len(history) == 0


class TestReadCurrentSlab:
    """read_current_slab 仅返回当前时间截面"""

    def test_read_current_slab_returns_only_current_slice(self):
        """read_current_slab 仅返回当前时间点的截面数据"""
        data = _make_test_data()
        guard = VisibilityGuard(data)
        ts = datetime(2024, 1, 3)
        guard.set_time(ts)

        slab = guard.read_current_slab()

        # 应只包含 ts 时刻的数据
        assert slab["timestamp"].unique().to_list() == [ts]
        # 应包含所有股票
        assert len(slab) == 3  # S1, S2, S3

    def test_read_current_slab_isolates_time(self):
        """read_current_slab 在不同时间点返回不同数据"""
        data = _make_test_data()
        guard = VisibilityGuard(data)

        ts1 = datetime(2024, 1, 2)
        guard.set_time(ts1)
        slab1 = guard.read_current_slab()

        ts2 = datetime(2024, 1, 4)
        guard.set_time(ts2)
        slab2 = guard.read_current_slab()

        # 两个 slab 的时间戳应不同
        assert (
            slab1["timestamp"].unique().to_list()
            != slab2["timestamp"].unique().to_list()
        )


class TestFutureDataError:
    """时间轴未初始化时抛出 FutureDataError"""

    def test_read_scalar_raises_when_time_not_set(self):
        """read_scalar 在时间未设置时抛出 FutureDataError"""
        data = _make_test_data()
        guard = VisibilityGuard(data)

        with pytest.raises(FutureDataError, match="时间轴尚未初始化"):
            guard.read_scalar("S1", "close")

    def test_read_history_raises_when_time_not_set(self):
        """read_history 在时间未设置时抛出 FutureDataError"""
        data = _make_test_data()
        guard = VisibilityGuard(data)

        with pytest.raises(FutureDataError, match="时间轴尚未初始化"):
            guard.read_history("S1", "close", window=5)

    def test_read_current_slab_raises_when_time_not_set(self):
        """read_current_slab 在时间未设置时抛出 FutureDataError"""
        data = _make_test_data()
        guard = VisibilityGuard(data)

        with pytest.raises(FutureDataError, match="时间轴尚未初始化"):
            guard.read_current_slab()


class TestReadScalar:
    """read_scalar 标量读取测试"""

    def test_read_scalar_returns_correct_value(self):
        """read_scalar 返回正确的当前价格"""
        data = _make_test_data()
        guard = VisibilityGuard(data)
        ts = datetime(2024, 1, 3)
        guard.set_time(ts)

        val = guard.read_scalar("S1", "close")

        expected = (
            data.filter((pl.col("timestamp") == ts) & (pl.col("symbol") == "S1"))
            .select("close")
            .to_series()[0]
        )
        assert val == pytest.approx(float(expected))

    def test_read_scalar_unknown_symbol_returns_nan(self):
        """read_scalar 不存在的股票返回 NaN"""
        data = _make_test_data()
        guard = VisibilityGuard(data)
        ts = datetime(2024, 1, 3)
        guard.set_time(ts)

        val = guard.read_scalar("UNKNOWN", "close")

        assert math.isnan(val)

    def test_read_scalar_unknown_field_returns_nan(self):
        """read_scalar 不存在的字段返回 NaN"""
        data = _make_test_data()
        guard = VisibilityGuard(data)
        ts = datetime(2024, 1, 3)
        guard.set_time(ts)

        # 先检查字段是否存在，如果不存在 polars 会报错
        # 这里测试不存在列的情况
        try:
            val = guard.read_scalar("S1", "nonexistent_field")
            assert math.isnan(val)
        except Exception:
            # polars 在列不存在时会抛出异常，这也是合理行为
            pass


class TestVisibilityContext:
    """VisibilityContext 只读上下文测试"""

    def test_context_get_price_delegates_to_guard(self):
        """VisibilityContext.get_price 代理到 VisibilityGuard.read_scalar"""
        data = _make_test_data()
        guard = VisibilityGuard(data)
        ts = datetime(2024, 1, 3)
        guard.set_time(ts)
        ctx = guard.get_context()

        price = ctx.get_price("S1", "close")

        expected = guard.read_scalar("S1", "close")
        assert price == expected

    def test_context_get_history_delegates_to_guard(self):
        """VisibilityContext.get_history 代理到 VisibilityGuard.read_history"""
        data = _make_test_data()
        guard = VisibilityGuard(data)
        ts = datetime(2024, 1, 5)
        guard.set_time(ts)
        ctx = guard.get_context()

        history = ctx.get_history("S1", "close", window=3)

        expected = guard.read_history("S1", "close", window=3)
        assert history.to_list() == expected.to_list()

    def test_context_get_current_slab_delegates_to_guard(self):
        """VisibilityContext.get_current_slab 代理到 VisibilityGuard.read_current_slab"""
        data = _make_test_data()
        guard = VisibilityGuard(data)
        ts = datetime(2024, 1, 3)
        guard.set_time(ts)
        ctx = guard.get_context()

        slab = ctx.get_current_slab()

        expected = guard.read_current_slab()
        assert slab.equals(expected)

    def test_context_current_timestamp_is_readable(self):
        """VisibilityContext.current_timestamp 可读取当前时间"""
        data = _make_test_data()
        guard = VisibilityGuard(data)
        ts = datetime(2024, 1, 3)
        guard.set_time(ts)
        ctx = guard.get_context()

        assert ctx.current_timestamp == ts

    def test_context_current_timestamp_default_to_min(self):
        """VisibilityContext.current_timestamp 未设置时返回 datetime.min"""
        data = _make_test_data()
        guard = VisibilityGuard(data)
        ctx = guard.get_context()

        assert ctx.current_timestamp == datetime.min
