"""VisibilityGuard 防未来函数接口测试

测试数据可见性控制的公共接口，不测试代理方法。
"""

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
    """read_history 接口测试"""

    def test_read_history_includes_current_time(self):
        """read_history 应包含当前时间点的数据"""
        data = _make_test_data()
        guard = VisibilityGuard(data)
        ts = datetime(2024, 1, 3)
        guard.set_time(ts)

        history = guard.read_history("S1", "close", window=10)

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

        expected_len = data.filter(
            (pl.col("timestamp") <= ts) & (pl.col("symbol") == "S1")
        ).height
        assert len(history) == expected_len

    def test_read_history_respects_window(self):
        """read_history 应遵守窗口大小限制"""
        data = _make_test_data()
        guard = VisibilityGuard(data)
        ts = datetime(2024, 1, 5)
        guard.set_time(ts)

        history = guard.read_history("S1", "close", window=3)

        assert len(history) == 3


class TestReadCurrentSlab:
    """read_current_slab 接口测试"""

    def test_read_current_slab_returns_only_current_slice(self):
        """read_current_slab 仅返回当前时间点的截面数据"""
        data = _make_test_data()
        guard = VisibilityGuard(data)
        ts = datetime(2024, 1, 3)
        guard.set_time(ts)

        slab = guard.read_current_slab()

        assert slab["timestamp"].unique().to_list() == [ts]
        assert len(slab) == 3


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
    """read_scalar 标量读取接口测试"""

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

        import math
        assert math.isnan(val)
