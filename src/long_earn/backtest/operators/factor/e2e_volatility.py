from typing import ClassVar

import polars as pl

from long_earn.backtest.operators._util import temporal_series
from long_earn.backtest.operators.base import Operator, OperatorParams, operator


class P(OperatorParams):
    field: str = "close"
    window: int = 10


@operator
class e2e_volatility(Operator):  # noqa: N801 算子名须与目录注册名一致（小写下划线）
    name: ClassVar[str] = "e2e_volatility"
    category: ClassVar[str] = "factor"
    inputs: ClassVar[list[str]] = []
    params_cls: ClassVar[type[OperatorParams]] = P
    min_history: ClassVar[int] = 0

    def apply(self, panel, params):
        expr = (
            (pl.col(params.field) / pl.col(params.field).shift(1) - 1)
            .pow(2)
            .rolling_mean(params.window)
            .sqrt()
            .over("symbol")
            .alias("e2e_volatility")
        )
        return temporal_series(panel, expr)
