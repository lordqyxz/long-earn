"""二元算术组合算子：``lhs <op> rhs``，op ∈ + - * /。"""

from typing import ClassVar, Literal

import polars as pl
from pydantic import field_validator

from long_earn.backtest.operators.base import Operator, OperatorParams, operator

ArithOp = Literal["+", "-", "*", "/"]

_OPS: dict[str, str] = {"+": "add", "-": "sub", "*": "mul", "/": "truediv"}

# rhs 可以是列名（str）或标量（int/float）。LLM 生成策略时常需要标量乘法
# （如年化乘子 15.87、归一化系数 100 等），仅支持列名会让大量合理策略失败。
RhsType = str | int | float


class ArithmeticParams(OperatorParams):
    lhs: str
    rhs: RhsType
    op: ArithOp = "/"
    alias: str = "compose"

    @field_validator("rhs")
    @classmethod
    def _coerce_rhs(cls, v: str | int | float) -> str | int | float:
        """字符串形式的数值（LLM 偶尔生成 ``rhs: "15.87"``）转 float。

        纯字段名（如 ``"close"``）保持原样。
        """
        if isinstance(v, str):
            try:
                return float(v)
            except ValueError:
                return v
        return v


@operator
class Arithmetic(Operator):
    """``arithmetic(lhs, rhs, op)`` —— ``lhs <op> rhs`` 当前行组合。

    ``rhs`` 可以是列名（与 ``lhs`` 同 panel 的另一列）或标量（int/float）。
    标量场景：``arithmetic(lhs="vol_daily", rhs=15.87, op="*")`` → 年化波动率。

    因果性：仅用当前行数据，无时序依赖，天然因果。除法除零由 polars 产出
    ``inf``/``null``，不在算子层吞异常（让上游诊断可见）。
    """

    name: ClassVar[str] = "arithmetic"
    category: ClassVar[str] = "compose"
    inputs: ClassVar[list[str]] = []
    # lhs/rhs 可能是列名或标量；field_params 标注承载列名的 params 键
    field_params: ClassVar[list[str]] = ["lhs", "rhs"]
    params_cls: ClassVar[type[OperatorParams]] = ArithmeticParams
    min_history: ClassVar[int] = 0

    def apply(self, panel: pl.DataFrame, params: OperatorParams) -> pl.Series:
        assert isinstance(params, ArithmeticParams)
        if params.op not in _OPS:
            raise ValueError(f"arithmetic.op={params.op!r} 非法，允许: {sorted(_OPS)}")
        lhs_expr = pl.col(params.lhs)
        # rhs 是标量 → 直接用 Python 值；是字符串 → 当列名
        if isinstance(params.rhs, (int, float)):
            rhs_expr: pl.Expr = pl.lit(params.rhs)
        else:
            rhs_expr = pl.col(params.rhs)
        if params.op == "/":
            expr = (lhs_expr / rhs_expr).alias(params.alias)
        elif params.op == "+":
            expr = (lhs_expr + rhs_expr).alias(params.alias)
        elif params.op == "-":
            expr = (lhs_expr - rhs_expr).alias(params.alias)
        else:  # "*"
            expr = (lhs_expr * rhs_expr).alias(params.alias)
        return panel.select(expr).to_series()
