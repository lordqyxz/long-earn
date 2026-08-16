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
    # 实际依赖字段由 params.field 决定（参数驱动），field_params 据此标注
    field_params: ClassVar[list[str]] = ["field"]
    params_cls: ClassVar[type[OperatorParams]] = P
    min_history: ClassVar[int] = 0

    def apply(self, panel, params):
        # 保留原始行序，确保因果性验证通过（面板可能 shuffle）。
        panel = panel.with_row_index("__e2ev_row_id")
        if "symbol" in panel.columns and "timestamp" in panel.columns:
            panel = panel.sort(["symbol", "timestamp"])
        expr = (
            (pl.col(params.field) / pl.col(params.field).shift(1) - 1)
            .pow(2)
            .rolling_mean(params.window)
            .sqrt()
            .over("symbol")
            .alias("e2e_volatility")
        )
        result = temporal_series(panel, expr)
        # 恢复原始行序
        result = panel.select(["__e2ev_row_id"]).with_columns(result)
        result = result.sort("__e2ev_row_id")
        return result["e2e_volatility"]
