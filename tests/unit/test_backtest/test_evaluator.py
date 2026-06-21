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
    """核心功能与安全测试"""

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

    def test_numpy_where(self):
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        result = evaluator.evaluate("where(close > 10, close, 10)")
        assert isinstance(result, pd.Series)

    # ── 安全边界测试 ──────────────────────────────────────────

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

    def test_unsupported_ast_node_raises(self):
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        with pytest.raises(SafeExpressionError, match="不支持的 AST 节点"):
            evaluator.evaluate("[x for x in close]")

    def test_unsafe_comparison_is(self):
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        with pytest.raises(SafeExpressionError, match="禁止的比较运算符"):
            evaluator.evaluate("close is None")
