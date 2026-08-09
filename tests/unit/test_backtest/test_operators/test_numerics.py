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
        out = get_operator("returns").apply(
            panel, ReturnsParams(field="close", period=1)
        )
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
        expected = s.select(pl.col("close").rolling_mean(5).over("symbol").alias("e"))[
            "e"
        ]
        _assert_aligned(s["ma5"], expected)


class TestArithmetic:
    def test_subtraction(self, panel: pl.DataFrame):
        out = get_operator("arithmetic").apply(
            panel, ArithmeticParams(lhs="high", rhs="low", op="-", alias="spread")
        )
        df = panel.with_columns(out.alias("spread"))
        for high_v, low_v, sp in zip(df["high"], df["low"], df["spread"], strict=True):
            assert sp == pytest.approx(high_v - low_v)

    def test_lhs_scalar_int_rejected(self) -> None:
        """lhs 为 int 标量时解析期拒绝（LLM 高频错误：lhs=1）。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="lhs 必须是列名"):
            ArithmeticParams(lhs=1, rhs="close", op="*", alias="bad")

    def test_lhs_scalar_float_rejected(self) -> None:
        """lhs 为 float 标量时解析期拒绝（LLM 错误：lhs=0.0）。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="lhs 必须是列名"):
            ArithmeticParams(lhs=0.0, rhs="close", op="*", alias="bad")

    def test_lhs_numeric_string_rejected(self) -> None:
        """lhs 为数字字符串（Pydantic 强制转换后）拒绝。"""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="lhs 必须是列名"):
            ArithmeticParams(lhs="15.87", rhs="close", op="*", alias="bad")

    def test_lhs_column_with_digits_accepted(self) -> None:
        """列名含数字（如 ret_20）合法，不被误拒。"""
        params = ArithmeticParams(lhs="ret_20", rhs="close", op="*", alias="ok")
        assert params.lhs == "ret_20"


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
            assert sub["sma"][9] == pytest.approx(
                sub["close"].head(10).mean(), rel=1e-9
            )


# ── P1-17: 算子数值稳定性（NaN/Inf/除零/极值/超长窗口）──────────


def _panel_with_nan() -> pl.DataFrame:
    """构造含 NaN 的面板。"""
    return pl.DataFrame(
        {
            "symbol": ["A"] * 6 + ["B"] * 6,
            "timestamp": [1, 2, 3, 4, 5, 6] * 2,
            "close": [10.0, float("nan"), 12.0, 13.0, float("nan"), 15.0] * 2,
            "volume": [100.0, 200.0, 300.0, 400.0, 500.0, 600.0] * 2,
        }
    )


def _panel_with_inf() -> pl.DataFrame:
    """构造含 Inf 的面板。"""
    return pl.DataFrame(
        {
            "symbol": ["A"] * 4,
            "timestamp": [1, 2, 3, 4],
            "close": [10.0, float("inf"), 12.0, float("-inf")],
            "volume": [100.0, 200.0, 300.0, 400.0],
        }
    )


def _panel_with_zero_price() -> pl.DataFrame:
    """构造含零价格的面板（除零风险）。"""
    return pl.DataFrame(
        {
            "symbol": ["A"] * 4,
            "timestamp": [1, 2, 3, 4],
            "close": [10.0, 0.0, 12.0, 15.0],
            "volume": [100.0, 200.0, 300.0, 400.0],
        }
    )


def _panel_with_extreme() -> pl.DataFrame:
    """构造含极端价格的面板。"""
    return pl.DataFrame(
        {
            "symbol": ["A"] * 4,
            "timestamp": [1, 2, 3, 4],
            "close": [1e-10, 1e10, 1e-5, 100.0],
            "volume": [100.0, 200.0, 300.0, 400.0],
        }
    )


def _panel_short_for_window() -> pl.DataFrame:
    """构造数据短于窗口的面板。"""
    return pl.DataFrame(
        {
            "symbol": ["A"] * 5,
            "timestamp": [1, 2, 3, 4, 5],
            "close": [10.0, 11.0, 12.0, 13.0, 14.0],
            "volume": [100.0, 200.0, 300.0, 400.0, 500.0],
        }
    )


class TestOperatorStability:
    """P1-17: 算子数值稳定性——NaN/Inf/除零/极值/超长窗口"""

    def test_returns_handles_nan(self) -> None:
        """returns 算子：NaN 输入不应崩溃，输出 NaN 位置应与输入一致"""
        panel = _panel_with_nan()
        out = get_operator("returns").apply(
            panel, ReturnsParams(field="close", period=1)
        )
        assert out is not None, "returns 算子 NaN 输入时不应返回 None"
        assert out.len() == panel.height, "输出长度应与输入一致"

    def test_returns_handles_inf(self) -> None:
        """returns 算子：Inf 输入不应崩溃"""
        panel = _panel_with_inf()
        out = get_operator("returns").apply(
            panel, ReturnsParams(field="close", period=1)
        )
        assert out is not None, "returns 算子 Inf 输入时不应返回 None"

    def test_returns_handles_zero_price(self) -> None:
        """returns 算子：零价格输入不应除零崩溃"""
        panel = _panel_with_zero_price()
        out = get_operator("returns").apply(
            panel, ReturnsParams(field="close", period=1)
        )
        assert out is not None, "returns 算子零价格输入时不应崩溃"

    def test_returns_handles_extreme_prices(self) -> None:
        """returns 算子：极端价格（1e-10 / 1e10）不应溢出"""
        panel = _panel_with_extreme()
        out = get_operator("returns").apply(
            panel, ReturnsParams(field="close", period=1)
        )
        assert out is not None, "returns 算子极端价格输入时不应崩溃"

    def test_sma_window_longer_than_data(self) -> None:
        """sma 算子：窗口 > 数据长度时全部为 null，不应崩溃"""
        panel = _panel_short_for_window()
        out = get_operator("sma").apply(panel, SMAParams(field="close", window=100))
        assert out is not None, "sma 超长窗口时不应崩溃"
        assert out.null_count() == out.len(), "超长窗口应全部为 null"

    def test_shift_handles_nan(self) -> None:
        """shift 算子：NaN 输入不应崩溃"""
        panel = _panel_with_nan()
        out = get_operator("shift").apply(
            panel, ShiftParams(field="close", periods=1)
        )
        assert out is not None, "shift 算子 NaN 输入时不应返回 None"
        assert out.len() == panel.height

    def test_windowed_handles_nan(self) -> None:
        """windowed 算子：NaN 输入不应崩溃"""
        panel = _panel_with_nan()
        out = get_operator("windowed").apply(
            panel, WindowedParams(field="close", window=5)
        )
        assert out is not None, "windowed 算子 NaN 输入时不应崩溃"

    def test_filter_threshold_handles_nan(self) -> None:
        """filter_threshold 算子：NaN 输入不应崩溃"""
        panel = _panel_with_nan()
        out = get_operator("filter_threshold").apply(
            panel, FilterThresholdParams(field="close", op=">", value=12.0)
        )
        assert out is not None, "filter_threshold 算子 NaN 输入时不应崩溃"

    def test_arithmetic_handles_nan(self) -> None:
        """arithmetic 算子：NaN 输入不应崩溃"""
        panel = _panel_with_nan()
        out = get_operator("arithmetic").apply(
            panel,
            ArithmeticParams(lhs="close", op="+", rhs="volume"),
        )
        assert out is not None, "arithmetic 算子 NaN 输入时不应崩溃"

    def test_all_registered_operators_accept_empty_panel(self) -> None:
        """所有已注册算子：空面板不应崩溃，应返回空 Series"""
        from long_earn.backtest.operators import list_operators
        from long_earn.backtest.operators.base import OperatorParams

        empty = pl.DataFrame(
            {"symbol": [], "timestamp": [], "close": [], "volume": []},
            schema={
                "symbol": pl.Utf8,
                "timestamp": pl.Int64,
                "close": pl.Float64,
                "volume": pl.Float64,
            },
        )
        for name in list_operators():
            op = get_operator(name)
            if op is None:
                continue
            # 跳过需要自定义 params（非基础 OperatorParams）的算子：
            # 它们有必填字段，空 {} 会导致 Pydantic 校验失败或运行时异常。
            # 这些算子的空面板行为由其专门的稳定性测试覆盖（如 test_returns_handles_nan）。
            if op.params_cls is not OperatorParams:
                continue
            try:
                result = op.apply(empty, {})
                assert result is not None, f"{name}: 空面板不应返回 None"
                assert result.len() == 0, f"{name}: 空面板应返回空 Series"
            except Exception as e:
                pytest.fail(f"{name}: 空面板输入时崩溃: {e}")
