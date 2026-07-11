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
            # 固定 seed 的确定性成交量，避免 flaky（P2-1）
            "volume": np.linspace(1e5, 1e6, 10),
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

    # ── 属性访问安全边界（P1-7）──────────────────────────────────

    def test_safe_attribute_access_allowed(self):
        """白名单属性（values/shape/index）应允许访问。"""
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        # values 在白名单，应返回 numpy 数组
        result = evaluator.evaluate("close.values")
        assert isinstance(result, (pd.Series, object)) and hasattr(result, "shape"), (
            "close.values 应返回数组-like 对象"
        )

    def test_unsafe_attribute_blocked(self):
        """非白名单属性应抛 SafeExpressionError（防止 close.os.system 链式攻击）。"""
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        with pytest.raises(SafeExpressionError, match="禁止访问属性"):
            evaluator.evaluate("close.os")

    def test_attribute_method_call_blocked(self):
        """非白名单属性上的方法调用应被拒。"""
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        with pytest.raises(SafeExpressionError, match="禁止通过属性调用方法"):
            evaluator.evaluate("close.foo()")

    # ── 一元运算符安全边界（P1-7）──────────────────────────────

    def test_unary_not_allowed(self):
        """一元 not（ast.Not）在白名单，应正常求值。"""
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        result = evaluator.evaluate("not (close > 10)")
        assert isinstance(result, pd.Series)

    def test_unary_usub_allowed(self):
        """一元负号（ast.USub）应正常求值。"""
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        result = evaluator.evaluate("-close")
        expected = -df["close"]
        pd.testing.assert_series_equal(result, expected, check_names=False)

    # ── 安全函数白名单覆盖（P1-7）──────────────────────────────

    def test_safe_functions_available(self):
        """白名单函数 sign/round/ceil/floor/abs 应可用。"""
        df = _make_df()
        evaluator = SafeExpressionEvaluator(df)
        # sign 函数
        result = evaluator.evaluate("sign(close - 15)")
        assert isinstance(result, pd.Series)
        # abs 函数
        result2 = evaluator.evaluate("abs(close - 15)")
        assert isinstance(result2, pd.Series)
