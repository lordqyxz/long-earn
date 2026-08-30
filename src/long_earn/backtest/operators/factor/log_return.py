"""对数收益率因子（operator_dev 自主研发写盘产物）。"""

from typing import ClassVar

import polars as pl
from pydantic import Field

from long_earn.backtest.operators._util import temporal_series
from long_earn.backtest.operators.base import Operator, OperatorParams, operator


class LogReturnParams(OperatorParams):
    field: str = "close"
    # gt=0 在解析期拦截负值：shift(负 period) 会引用未来 bar（前视偏差），
    # 且注册因果证明的边界参数只覆盖正值区间，无法在证明阶段发现
    period: int = Field(default=1, gt=0)


@operator
class LogReturn(Operator):
    name: ClassVar[str] = "log_return"
    category: ClassVar[str] = "factor"
    inputs: ClassVar[list[str]] = []
    # 实际依赖字段由 params.field 决定（参数驱动），field_params 据此标注
    field_params: ClassVar[list[str]] = ["field"]
    params_cls: ClassVar[type[OperatorParams]] = LogReturnParams
    min_history: ClassVar[int] = 1

    def apply(self, panel: pl.DataFrame, params: OperatorParams) -> pl.Series:
        assert isinstance(params, LogReturnParams)
        expr = (
            (pl.col(params.field) / pl.col(params.field).shift(params.period))
            .log()
            .over("symbol")
            .alias("log_return")
        )
        return temporal_series(panel, expr)
