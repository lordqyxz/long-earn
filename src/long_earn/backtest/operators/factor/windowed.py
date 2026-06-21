"""滚动窗口因子算子：mean/std/min/max/median/sum，仅回溯历史窗口。"""

from typing import ClassVar, Literal

import polars as pl

from long_earn.backtest.operators._util import temporal_series
from long_earn.backtest.operators.base import Operator, OperatorParams, operator

Agg = Literal["mean", "std", "min", "max", "median", "sum"]


class WindowedParams(OperatorParams):
    field: str
    window: int
    agg: Agg = "mean"


_AGG_EXPR: dict[str, str] = {
    "mean": "rolling_mean",
    "std": "rolling_std",
    "min": "rolling_min",
    "max": "rolling_max",
    "median": "rolling_median",
    "sum": "rolling_sum",
}


@operator
class WindowedFactor(Operator):
    """``windowed(field, window, agg)`` —— 滚动窗口聚合。

    因果性：polars ``rolling_*`` 默认 ``window`` 个**历史**样本（含当前行），
    不窥未来；``window < 1`` 禁止。``min_history = window``。
    """

    name: ClassVar[str] = "windowed"
    category: ClassVar[str] = "factor"
    inputs: ClassVar[list[str]] = []
    params_cls: ClassVar[type[OperatorParams]] = WindowedParams
    # min_history 在 apply 前无法静态确定（依赖 window），用 0 占位；具体门槛
    # 由策略层按 params.window 校验。contract 要求非负即可。
    min_history: ClassVar[int] = 0

    def apply(self, panel: pl.DataFrame, params: OperatorParams) -> pl.Series:
        assert isinstance(params, WindowedParams)
        if params.window < 1:
            raise ValueError(f"windowed.window 必须 >= 1，得到 {params.window}")
        if params.agg not in _AGG_EXPR:
            raise ValueError(
                f"windowed.agg={params.agg!r} 非法，允许: {sorted(_AGG_EXPR)}"
            )
        method = _AGG_EXPR[params.agg]
        col = pl.col(params.field)
        rolled = getattr(col, method)(params.window)
        expr = rolled.over("symbol").alias(f"{params.agg}_{params.window}")
        return temporal_series(panel, expr)
