"""算子数值正确性测试（关键算子）。

校验算子输出与按 symbol 分组的手算期望一致，且输出行序与 panel 对齐（即使
panel 打乱输入）。重点验证时序对齐与窗口边界，不重复 causality 已覆盖的内容。
"""

from __future__ import annotations

import polars as pl
import pytest

from long_earn.backtest.operators import get_operator
from long_earn.backtest.operators.compose.arithmetic import ArithmeticParams
from long_earn.backtest.operators.factor.returns import ReturnsParams
from long_earn.backtest.operators.factor.shift import ShiftParams
from long_earn.backtest.operators.factor.windowed import WindowedParams
from long_earn.backtest.operators.filter.threshold import FilterThresholdParams
from long_earn.backtest.operators.technical.sma_ema import SMAParams


def _assert_aligned(got: pl.Series, expected: pl.Series) -> None:
    """逐元素比较两列（got 算子输出，expected 按 symbol 分组手算），null 视作相等。"""
    assert got.len() == expected.len()
    for g, e in zip(got, expected, strict=True):
        if e is None:
            assert g is None
        else:
            assert g == pytest.approx(e, rel=1e-9, abs=1e-12)


def _sorted(df: pl.DataFrame) -> pl.DataFrame:
    return df.sort(["symbol", "timestamp"])


class TestShift:
    def test_shift_first_row_null_per_symbol(self, panel: pl.DataFrame):
        """每个 symbol 首行无历史 → null；输出对齐 panel 原始行序。"""
        out = get_operator("shift").apply(panel, ShiftParams(field="close", periods=1))
        assert out.len() == panel.height
        firsts = panel.with_columns(out.alias("prev")).sort(["symbol", "timestamp"])
        first_rows = firsts.group_by("symbol").first()
        assert first_rows["prev"].null_count() == first_rows.height

    def test_shift_periods_zero_rejected(self, panel: pl.DataFrame):
        with pytest.raises(ValueError, match="> 0"):
            get_operator("shift").apply(panel, ShiftParams(field="close", periods=0))


class TestReturns:
    def test_returns_formula(self, panel: pl.DataFrame):
        out = get_operator("returns").apply(panel, ReturnsParams(field="close", period=1))
        s = _sorted(panel.with_columns(out.alias("ret")))
        expected = s.select(
            (pl.col("close") / pl.col("close").shift(1).over("symbol") - 1).alias("e")
        )["e"]
        _assert_aligned(s["ret"], expected)


class TestWindowed:
    def test_windowed_mean_matches_grouped_rolling(self, panel: pl.DataFrame):
        out = get_operator("windowed").apply(
            panel, WindowedParams(field="close", window=5, agg="mean")
        )
        s = _sorted(panel.with_columns(out.alias("ma5")))
        expected = s.select(
            pl.col("close").rolling_mean(5).over("symbol").alias("e")
        )["e"]
        _assert_aligned(s["ma5"], expected)


class TestArithmetic:
    def test_subtraction(self, panel: pl.DataFrame):
        out = get_operator("arithmetic").apply(
            panel, ArithmeticParams(lhs="high", rhs="low", op="-", alias="spread")
        )
        df = panel.with_columns(out.alias("spread"))
        for high_v, low_v, sp in zip(df["high"], df["low"], df["spread"], strict=True):
            assert sp == pytest.approx(high_v - low_v)


class TestFilterThreshold:
    def test_returns_bool_mask(self, panel: pl.DataFrame):
        out = get_operator("filter_threshold").apply(
            panel, FilterThresholdParams(field="close", op=">", value=15.0)
        )
        assert out.dtype == pl.Boolean
        assert (out == (panel["close"] > 15.0)).all()


class TestSMA:
    def test_window_boundary_and_value(self, panel: pl.DataFrame):
        """窗口未满为 null；满窗首值等于前 N 个 close 的均值。"""
        out = get_operator("sma").apply(panel, SMAParams(field="close", window=10))
        s = _sorted(panel.with_columns(out.alias("sma")))
        for sub in s.partition_by("symbol", as_dict=True).values():
            assert sub["sma"].head(9).null_count() == 9
            assert sub["sma"][9] == pytest.approx(sub["close"].head(10).mean(), rel=1e-9)
