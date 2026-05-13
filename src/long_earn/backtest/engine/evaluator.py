"""安全表达式求值器

基于 AST 遍历的安全表达式求值，替代 eval()。
仅支持白名单操作：算术、比较、逻辑运算、内置函数（abs/min/max/sum/mean/std 等）。
"""

import ast
import logging
import operator
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# 安全运算符白名单
_SAFE_OPERATORS: dict[type[ast.AST], Any] = {
    ast.Add: np.add,
    ast.Sub: np.subtract,
    ast.Mult: np.multiply,
    ast.Div: np.true_divide,
    ast.FloorDiv: np.floor_divide,
    ast.Mod: np.fmod,
    ast.Pow: np.power,
    ast.USub: np.negative,
    ast.UAdd: lambda x: x,
    ast.Not: np.logical_not,
    ast.Invert: np.bitwise_not,
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.And: np.logical_and,
    ast.Or: np.logical_or,
}

# 安全内置函数白名单
_SAFE_FUNCTIONS: dict[str, Any] = {
    "abs": np.abs,
    "min": np.minimum,
    "max": np.maximum,
    "sum": np.sum,
    "mean": np.mean,
    "std": np.std,
    "sqrt": np.sqrt,
    "log": np.log,
    "exp": np.exp,
    "clip": np.clip,
    "where": np.where,
    "sign": np.sign,
    "round": np.round,
    "ceil": np.ceil,
    "floor": np.floor,
}


class SafeExpressionError(ValueError):
    """表达式求值安全异常"""

    pass


class SafeExpressionEvaluator:
    """基于 AST 的安全表达式求值器"""

    def __init__(self, df: pd.DataFrame):
        """初始化求值器

        Args:
            df: 包含因子和行情数据的面板 DataFrame
        """
        self.df = df
        self._namespace: dict[str, Any] = {}

        # 注册列名作为变量
        for col in df.columns:
            self._namespace[col] = df[col]

        # 注册安全函数
        self._namespace.update(_SAFE_FUNCTIONS)
        self._namespace.update(
            {
                "np": np,
                "pd": pd,
            }
        )

        # 注册领域辅助函数
        self._namespace["shift"] = self._shift
        self._namespace["rank"] = self._rank

    def _shift(self, series: pd.Series, periods: int = 1) -> pd.Series:
        return series.groupby(level="symbol").shift(periods)

    def _rank(self, series: pd.Series, ascending: bool = True) -> pd.Series:
        return series.groupby(level="date").rank(ascending=ascending)

    def evaluate(self, expr: str) -> pd.Series:
        """安全求值表达式

        Args:
            expr: 表达式字符串，如 'close / shift(close, 20) - 1'

        Returns:
            计算结果 Series

        Raises:
            SafeExpressionError: 表达式不安全或执行失败
        """
        try:
            tree = ast.parse(expr, mode="eval")
            result = self._eval_node(tree.body)
            if isinstance(result, pd.Series):
                return result.reindex(self.df.index)
            return pd.Series(result, index=self.df.index)
        except SyntaxError as e:
            raise SafeExpressionError(f"表达式语法错误: {expr}, 错误: {e}") from e
        except SafeExpressionError:
            raise
        except Exception as e:
            raise SafeExpressionError(f"表达式执行失败: {expr}, 错误: {e}") from e

    def _eval_node(self, node: ast.AST) -> Any:  # noqa: PLR0911, PLR0912
        """递归求值 AST 节点"""
        if isinstance(node, ast.Constant):
            return node.value

        elif isinstance(node, ast.Name):
            name = node.id
            if name in self._namespace:
                return self._namespace[name]
            raise SafeExpressionError(f"未定义的变量: {name}")

        elif isinstance(node, ast.BinOp):
            return self._eval_binary_op(node)

        elif isinstance(node, ast.UnaryOp):
            return self._eval_unary_op(node)

        elif isinstance(node, ast.BoolOp):
            return self._eval_bool_op(node)

        elif isinstance(node, ast.Compare):
            return self._eval_compare(node)

        elif isinstance(node, ast.Call):
            return self._eval_call(node)

        elif isinstance(node, ast.Attribute):
            return self._eval_attribute(node)

        elif isinstance(node, ast.Subscript):
            return self._eval_subscript(node)

        elif isinstance(node, ast.Slice):
            return slice(
                self._eval_node(node.lower) if node.lower else None,
                self._eval_node(node.upper) if node.upper else None,
                self._eval_node(node.step) if node.step else None,
            )

        elif isinstance(node, ast.Tuple):
            return tuple(self._eval_node(elt) for elt in node.elts)

        elif isinstance(node, ast.List):
            return [self._eval_node(elt) for elt in node.elts]

        elif isinstance(node, ast.IfExp):
            test = self._eval_node(node.test)
            return np.where(
                test, self._eval_node(node.body), self._eval_node(node.orelse)
            )

        else:
            raise SafeExpressionError(f"不支持的 AST 节点: {type(node).__name__}")

    def _eval_binary_op(self, node: ast.BinOp) -> Any:
        op_type = type(node.op)
        if op_type not in _SAFE_OPERATORS:
            raise SafeExpressionError(f"禁止的二元运算符: {op_type.__name__}")
        left = self._eval_node(node.left)
        right = self._eval_node(node.right)
        return _SAFE_OPERATORS[op_type](left, right)

    def _eval_unary_op(self, node: ast.UnaryOp) -> Any:
        op_type = type(node.op)
        if op_type not in _SAFE_OPERATORS:
            raise SafeExpressionError(f"禁止的一元运算符: {op_type.__name__}")
        operand = self._eval_node(node.operand)
        return _SAFE_OPERATORS[op_type](operand)

    def _eval_bool_op(self, node: ast.BoolOp) -> Any:
        values = [self._eval_node(v) for v in node.values]
        if isinstance(node.op, ast.And):
            result = values[0]
            for v in values[1:]:
                result = np.logical_and(result, v)
            return result
        elif isinstance(node.op, ast.Or):
            result = values[0]
            for v in values[1:]:
                result = np.logical_or(result, v)
            return result
        raise SafeExpressionError(f"禁止的布尔运算符: {type(node.op).__name__}")

    def _eval_compare(self, node: ast.Compare) -> Any:
        left = self._eval_node(node.left)
        result = None
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            op_type = type(op)
            if op_type not in _SAFE_OPERATORS:
                raise SafeExpressionError(f"禁止的比较运算符: {op_type.__name__}")
            right = self._eval_node(comparator)
            cmp_result = _SAFE_OPERATORS[op_type](left, right)
            if result is None:
                result = cmp_result
            else:
                result = np.logical_and(result, cmp_result)
            left = right
        return result if result is not None else left

    def _eval_call(self, node: ast.Call) -> Any:
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
            if func_name not in self._namespace:
                raise SafeExpressionError(f"禁止的函数调用: {func_name}()")
            func = self._namespace[func_name]
            args = [self._eval_node(arg) for arg in node.args]
            kwargs = {kw.arg: self._eval_node(kw.value) for kw in node.keywords}
            return func(*args, **kwargs)

        elif isinstance(node.func, ast.Attribute):
            obj = self._eval_node(node.func.value)
            attr = node.func.attr
            if not hasattr(obj, attr):
                raise SafeExpressionError(f"对象无此属性: {attr}")
            method = getattr(obj, attr)
            args = [self._eval_node(arg) for arg in node.args]
            kwargs = {kw.arg: self._eval_node(kw.value) for kw in node.keywords}
            return method(*args, **kwargs)

        raise SafeExpressionError("不支持的函数调用形式")

    def _eval_attribute(self, node: ast.Attribute) -> Any:
        obj = self._eval_node(node.value)
        attr = node.attr
        if not hasattr(obj, attr):
            raise SafeExpressionError(f"属性不存在: {attr}")
        return getattr(obj, attr)

    def _eval_subscript(self, node: ast.Subscript) -> Any:
        value = self._eval_node(node.value)
        if isinstance(node.slice, ast.Slice):
            sl = self._eval_node(node.slice)
            try:
                return value.iloc[sl]
            except Exception:
                pass
        sl = self._eval_node(node.slice)
        return value[sl]
