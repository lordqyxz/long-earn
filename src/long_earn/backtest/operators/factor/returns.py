"""收益率算子：``close / shift(close, period) - 1``，仅回溯历史。"""

from typing import ClassVar

import polars as pl

from long_earn.backtest.operators._util import temporal_series
from long_earn.backtest.operators.base import Operator, OperatorParams, operator


class ReturnsParams(OperatorParams):
    field: str = "close"
    period: int = 1


@operator
class Returns(Operator):
    """``returns(field, period)`` —— ``field[t] / field[t-period] - 1``。

    因果性：等价于 ``field / shift(field, period) - 1``，仅依赖 ``period`` 期前
    的值。``period <= 0`` 禁止。
    """

    name: ClassVar[str] = "returns"
    category: ClassVar[str] = "factor"
    inputs: ClassVar[list[str]] = []
    params_cls: ClassVar[type[OperatorParams]] = ReturnsParams
    min_history: ClassVar[int] = 1

    def apply(self, panel: pl.DataFrame, params: OperatorParams) -> pl.Series:
        assert isinstance(params, ReturnsParams)
        if params.period <= 0:
            raise ValueError(
                f"returns.period 必须 > 0（仅允许回溯历史），得到 {params.period}"
            )
        expr = (
            (pl.col(params.field) / pl.col(params.field).shift(params.period) - 1)
            .over("symbol")
            .alias("returns")
        )
        return temporal_series(panel, expr)
