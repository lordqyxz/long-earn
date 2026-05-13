"""安全表达式求值器测试"""

import numpy as np
import pandas as pd
import pytest

from long_earn.backtest.engine.evaluator import (
    SafeExpressionError,
    SafeExpressionEvaluator,
)


def _make_df() -> pd.DataFrame:
    """构造测试面板"""
    dates = pd.date_range("2024-01-01", "2024-01-05", freq="B")
    symbols = ["000001", "000002"]
    idx = pd.MultiIndex.from_product([dates, symbols], names=["date", "symbol"])
    data = pd.DataFrame(
        {
            "close": [10.0, 20.0, 10.5, 21.0, 11.0, 19.5, 10.8, 20.5, 11.2, 22.0],
            "volume": np.random.rand(10) * 1e6,
            "roe": [0.15, 0.10, 0.15, 0.10, 0.15, 0.10, 0.15, 0.10, 0.15, 0.10],
        },
        index=idx,
    )
    return data


class TestSafeExpressionEvaluator:
    """安全表达式求值器单元测试"""

    def test_simple_arithmetic(self):
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        result = evaluator.evaluate("close * 2")
        expected = df["close"] * 2
        pd.testing.assert_series_equal(result, expected, check_names=False)

    def test_comparison(self):
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        result = evaluator.evaluate("close > 10.5")
        expected = df["close"] > 10.5
        pd.testing.assert_series_equal(result, expected, check_names=False)

    def test_compound_condition(self):
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        result = evaluator.evaluate("close > 10 and roe > 0.12")
        expected = (df["close"] > 10) & (df["roe"] > 0.12)
        pd.testing.assert_series_equal(result, expected, check_names=False)

    def test_shift_function(self):
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        result = evaluator.evaluate("shift(close, 1)")
        expected = df["close"].groupby(level="symbol").shift(1)
        pd.testing.assert_series_equal(result, expected, check_names=False)

    def test_builtin_abs(self):
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        result = evaluator.evaluate("abs(-close)")
        expected = abs(-df["close"])
        pd.testing.assert_series_equal(result, expected, check_names=False)

    def test_builtin_np(self):
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        result = evaluator.evaluate("np.log(close)")
        expected = np.log(df["close"])
        pd.testing.assert_series_equal(result, expected, check_names=False)

    def test_if_expression(self):
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        result = evaluator.evaluate("close if close > 10 else 10")
        assert isinstance(result, pd.Series)

    def test_undefined_variable_raises(self):
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        with pytest.raises(SafeExpressionError, match="未定义的变量"):
            evaluator.evaluate("unknown_field > 10")

    def test_syntax_error_raises(self):
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        with pytest.raises(SafeExpressionError, match=r"表达式语法错误|表达式执行失败"):
            evaluator.evaluate("close >")

    def test_constant_result(self):
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        result = evaluator.evaluate("42")
        expected = pd.Series(42, index=df.index)
        pd.testing.assert_series_equal(result, expected, check_names=False)

    # ── 函数调用 ──────────────────────────────────────────────

    def test_rank_function(self):
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        result = evaluator.evaluate("rank(close)")
        assert result is not None
        assert isinstance(result, pd.Series)

    def test_clip_function(self):
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        result = evaluator.evaluate("clip(close, 10, 20)")
        assert result is not None

    def test_nested_function_call(self):
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        result = evaluator.evaluate("abs(shift(close, 1))")
        assert isinstance(result, pd.Series)

    # ── 布尔运算 ──────────────────────────────────────────────

    def test_bool_or(self):
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        result = evaluator.evaluate("close > 20 or close < 11")
        expected = (df["close"] > 20) | (df["close"] < 11)
        pd.testing.assert_series_equal(result, expected, check_names=False)

    # ── 属性访问和下标 ────────────────────────────────────────

    def test_series_attribute_access(self):
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        result = evaluator.evaluate("close.mean()")
        assert result is not None

    def test_subscript_access(self):
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        result = evaluator.evaluate("close[:2]")
        assert isinstance(result, pd.Series)

    # ── 复合比较 ──────────────────────────────────────────────

    def test_chained_comparison(self):
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        result = evaluator.evaluate("10 < close < 20")
        expected = (df["close"] > 10) & (df["close"] < 20)
        pd.testing.assert_series_equal(result, expected, check_names=False)

    def test_numpy_where(self):
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        result = evaluator.evaluate("np.where(close > 10, close, 10)")
        assert isinstance(result, pd.Series)

    # ── 错误情况 ──────────────────────────────────────────────

    def test_unsafe_binary_op_raises(self):
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        with pytest.raises(SafeExpressionError, match="禁止的二元运算符"):
            evaluator.evaluate("close & roe")

    def test_disallowed_function_raises(self):
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        with pytest.raises(SafeExpressionError, match="禁止的函数调用"):
            evaluator.evaluate("eval('1+1')")

    def test_unknown_attribute_raises(self):
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        with pytest.raises(SafeExpressionError, match="属性不存在"):
            evaluator.evaluate("np.nonexistent_attr")

    def test_unsupported_ast_node_raises(self):
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        with pytest.raises(SafeExpressionError, match="不支持的 AST 节点"):
            evaluator.evaluate("[x for x in close]")

    def test_generic_error_fallback(self):
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        evaluator._namespace["broken_func"] = lambda: 1 / 0
        with pytest.raises(SafeExpressionError, match="表达式执行失败"):
            evaluator.evaluate("broken_func()")

    # ── 比较运算符 ────────────────────────────────────────────

    def test_unsafe_comparison_is(self):
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        with pytest.raises(SafeExpressionError, match="禁止的比较运算符"):
            evaluator.evaluate("close is None")

    # ── 函数调用 ──────────────────────────────────────────────

    def test_call_nonexistent_method(self):
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)

        class _Dummy:
            pass

        evaluator._namespace["dummy"] = _Dummy()
        with pytest.raises(SafeExpressionError, match="对象无此属性"):
            evaluator.evaluate("dummy.nonexistent()")

    # ── 下标访问 ──────────────────────────────────────────────

    def test_subscript_with_slice(self):
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        result = evaluator.evaluate("close[1:3]")
        assert isinstance(result, pd.Series)
