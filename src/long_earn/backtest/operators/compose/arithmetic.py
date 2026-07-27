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
    # lhs 接受联合类型，让 validator 给出友好错误信息（而非 Pydantic 默认
    # 英文 "Input should be a valid string"）。LLM 高频错误：lhs=1 / lhs=0.0
    lhs: str | int | float
    rhs: RhsType
    op: ArithOp = "/"
    alias: str = "compose"

    @field_validator("lhs")
    @classmethod
    def _reject_scalar_lhs(cls, v: str | int | float) -> str:
        """拒绝数字标量作为 lhs（LLM 高频错误：``lhs: 1`` 或 ``lhs: 0.0``）。

        ``lhs`` 必须是已定义的列名（字符串），标量运算请放在 ``rhs``。
        列名含数字（如 ``"close_1d"``、``"ret_20"``）不受影响 —— 这些
        字符串无法被 ``float()`` 整体解析。
        """
        if isinstance(v, (int, float)):
            raise ValueError(
                f"lhs 必须是列名，不能是数字标量 {v!r}（标量请放在 rhs）"
            )
        # 字符串形式的纯数字（如 "15.87"）也拒绝
        try:
            float(v)
        except ValueError:
            return v  # 不是数字 → 合法列名
        raise ValueError(
            f"lhs 必须是列名，不能是数字 {v!r}（标量请放在 rhs）"
        )

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
        # validator 保证 lhs 必为列名（str），此处断言 narrow 类型
        assert isinstance(params.lhs, str)
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
