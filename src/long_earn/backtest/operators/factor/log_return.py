"""对数收益率因子（operator_dev 自主研发写盘产物）。"""

from typing import ClassVar

import polars as pl

from long_earn.backtest.operators._util import temporal_series
from long_earn.backtest.operators.base import Operator, OperatorParams, operator


class LogReturnParams(OperatorParams):
    field: str = "close"
    period: int = 1


@operator
class LogReturn(Operator):
    name: ClassVar[str] = "log_return"
    category: ClassVar[str] = "factor"
    inputs: ClassVar[list[str]] = []
    # 实际依赖字段由 params.field 决定（参数驱动），field_params 据此标注
    field_params: ClassVar[list[str]] = ["field"]
    params_cls: ClassVar[type[OperatorParams]] = LogReturnParams
    min_history: ClassVar[int] = 1

    def apply(self, panel, params):
        expr = (
            (pl.col(params.field) / pl.col(params.field).shift(params.period))
            .log()
            .over("symbol")
            .alias("log_return")
        )
        return temporal_series(panel, expr)
