"""二元算术组合算子：``lhs <op> rhs``，op ∈ + - * /。"""

from typing import ClassVar, Literal

import polars as pl

from long_earn.backtest.operators.base import Operator, OperatorParams, operator

ArithOp = Literal["+", "-", "*", "/"]

_OPS: dict[str, str] = {"+": "add", "-": "sub", "*": "mul", "/": "truediv"}


class ArithmeticParams(OperatorParams):
    lhs: str
    rhs: str
    op: ArithOp = "/"
    alias: str = "compose"


@operator
class Arithmetic(Operator):
    """``arithmetic(lhs, rhs, op)`` —— ``lhs <op> rhs`` 当前行组合。

    因果性：仅用当前行两列，无时序依赖，天然因果。除法除零由 polars 产出
    ``inf``/``null``，不在算子层吞异常（让上游诊断可见）。
    """

    name: ClassVar[str] = "arithmetic"
    category: ClassVar[str] = "compose"
    inputs: ClassVar[list[str]] = []
    params_cls: ClassVar[type[OperatorParams]] = ArithmeticParams
    min_history: ClassVar[int] = 0

    def apply(self, panel: pl.DataFrame, params: OperatorParams) -> pl.Series:
        assert isinstance(params, ArithmeticParams)
        if params.op not in _OPS:
            raise ValueError(f"arithmetic.op={params.op!r} 非法，允许: {sorted(_OPS)}")
        if params.op == "/":
            expr = (pl.col(params.lhs) / pl.col(params.rhs)).alias(params.alias)
        elif params.op == "+":
            expr = (pl.col(params.lhs) + pl.col(params.rhs)).alias(params.alias)
        elif params.op == "-":
            expr = (pl.col(params.lhs) - pl.col(params.rhs)).alias(params.alias)
        else:  # "*"
            expr = (pl.col(params.lhs) * pl.col(params.rhs)).alias(params.alias)
        return panel.select(expr).to_series()
