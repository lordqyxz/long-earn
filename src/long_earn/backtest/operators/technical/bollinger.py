"""布林带算子，返回带 upper/middle/lower 三列的 DataFrame。"""

from typing import ClassVar

import polars as pl

from long_earn.backtest.operators._util import temporal_series
from long_earn.backtest.operators.base import Operator, OperatorParams, operator


class BollingerParams(OperatorParams):
    field: str = "close"
    window: int = 20
    k: float = 2.0


@operator
class BollingerBands(Operator):
    """布林带：``middle = SMA(window)``；``std = rolling_std(window)``；
    ``upper = middle + k*std``；``lower = middle - k*std``。

    因果性：SMA / rolling_std 均仅回溯历史窗口，不窥未来。
    """

    name: ClassVar[str] = "bollinger"
    category: ClassVar[str] = "technical"
    inputs: ClassVar[list[str]] = []
    params_cls: ClassVar[type[OperatorParams]] = BollingerParams
    min_history: ClassVar[int] = 0

    def apply(self, panel: pl.DataFrame, params: OperatorParams) -> pl.DataFrame:
        assert isinstance(params, BollingerParams)
        if params.window < 1:
            raise ValueError(f"bollinger.window 必须 >= 1，得到 {params.window}")
        col = pl.col(params.field)
        middle_expr = col.rolling_mean(params.window).over("symbol")
        std_expr = col.rolling_std(params.window).over("symbol")
        middle = temporal_series(panel, middle_expr.alias("middle"))
        std = temporal_series(panel, std_expr.alias("std"))
        upper = middle + params.k * std
        lower = middle - params.k * std
        return panel.with_columns(
            upper.alias("upper"),
            middle.alias("middle"),
            lower.alias("lower"),
        )
