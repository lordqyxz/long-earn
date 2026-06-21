"""阈值过滤算子：``field <op> value``。"""

from typing import ClassVar, Literal

import polars as pl

from long_earn.backtest.operators.base import Operator, OperatorParams, operator

CompareOp = Literal[">", ">=", "<", "<=", "==", "!="]


class FilterThresholdParams(OperatorParams):
    field: str
    op: CompareOp = ">"
    value: float = 0.0


_OPS: dict[str, str] = {
    ">": "gt",
    ">=": "ge",
    "<": "lt",
    "<=": "le",
    "==": "eq",
    "!=": "ne",
}


@operator
class FilterThreshold(Operator):
    """``filter_threshold(field, op, value)`` —— 当前行 ``field`` 与常量比较。

    因果性：仅用当前行字段值，无时序依赖，天然因果。
    """

    name: ClassVar[str] = "filter_threshold"
    category: ClassVar[str] = "filter"
    inputs: ClassVar[list[str]] = []
    params_cls: ClassVar[type[OperatorParams]] = FilterThresholdParams
    min_history: ClassVar[int] = 0

    def apply(self, panel: pl.DataFrame, params: OperatorParams) -> pl.Series:
        assert isinstance(params, FilterThresholdParams)
        if params.op not in _OPS:
            raise ValueError(
                f"filter_threshold.op={params.op!r} 非法，允许: {sorted(_OPS)}"
            )
        method = _OPS[params.op]
        expr = getattr(pl.col(params.field), method)(params.value).alias("mask")
        return panel.select(expr).to_series()
