"""位移算子：把字段沿时间轴向后回溯 N 期（只读历史）。"""

from typing import ClassVar

import polars as pl

from long_earn.backtest.operators._util import temporal_series
from long_earn.backtest.operators.base import Operator, OperatorParams, operator


class ShiftParams(OperatorParams):
    field: str
    periods: int = 1


@operator
class Shift(Operator):
    """``shift(field, periods)`` —— 取 ``periods`` 期前的 ``field`` 值。

    因果性：``periods > 0`` 仅回溯历史；``periods <= 0`` 等价窥探未来，
    参数校验直接禁止，从源头杜绝未来函数。
    """

    name: ClassVar[str] = "shift"
    category: ClassVar[str] = "factor"
    # 实际依赖字段由 params.field 决定（参数驱动），静态 inputs 留空
    inputs: ClassVar[list[str]] = []
    params_cls: ClassVar[type[OperatorParams]] = ShiftParams
    min_history: ClassVar[int] = 1

    def apply(self, panel: pl.DataFrame, params: OperatorParams) -> pl.Series:
        assert isinstance(params, ShiftParams)
        if params.periods <= 0:
            raise ValueError(
                f"shift.periods 必须 > 0（仅允许回溯历史），得到 {params.periods}"
            )
        expr = (
            pl.col(params.field)
            .shift(params.periods)
            .over("symbol")
            .alias(params.field)
        )
        return temporal_series(panel, expr)
