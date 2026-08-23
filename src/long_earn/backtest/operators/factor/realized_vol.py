"""已实现波动率因子（operator_dev 自主研发写盘产物）。"""

from typing import ClassVar

import polars as pl

from long_earn.backtest.operators._util import temporal_series
from long_earn.backtest.operators.base import Operator, OperatorParams, operator


class RealizedVolParams(OperatorParams):
    field: str = "close"
    window: int = 10


@operator
class RealizedVol(Operator):
    name: ClassVar[str] = "realized_vol"
    category: ClassVar[str] = "factor"
    inputs: ClassVar[list[str]] = []
    # 实际依赖字段由 params.field 决定（参数驱动），field_params 据此标注
    field_params: ClassVar[list[str]] = ["field"]
    params_cls: ClassVar[type[OperatorParams]] = RealizedVolParams
    min_history: ClassVar[int] = 0

    def apply(self, panel: pl.DataFrame, params: OperatorParams) -> pl.Series:
        assert isinstance(params, RealizedVolParams)
        expr = (
            (pl.col(params.field) / pl.col(params.field).shift(1) - 1)
            .pow(2)
            .rolling_mean(params.window)
            .sqrt()
            .over("symbol")
            .alias("realized_vol")
        )
        return temporal_series(panel, expr)
