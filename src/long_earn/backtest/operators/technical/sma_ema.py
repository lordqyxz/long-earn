"""SMA / EMA 技术指标算子。"""

from typing import ClassVar

import polars as pl

from long_earn.backtest.operators._util import temporal_series
from long_earn.backtest.operators.base import Operator, OperatorParams, operator


class SMAParams(OperatorParams):
    field: str = "close"
    window: int = 20


@operator
class SMA(Operator):
    """简单移动平均。因果：``rolling_mean(window)`` 仅回溯历史。"""

    name: ClassVar[str] = "sma"
    category: ClassVar[str] = "technical"
    inputs: ClassVar[list[str]] = []
    params_cls: ClassVar[type[OperatorParams]] = SMAParams
    min_history: ClassVar[int] = 0

    def apply(self, panel: pl.DataFrame, params: OperatorParams) -> pl.Series:
        assert isinstance(params, SMAParams)
        if params.window < 1:
            raise ValueError(f"sma.window 必须 >= 1，得到 {params.window}")
        expr = (
            pl.col(params.field).rolling_mean(params.window).over("symbol").alias("sma")
        )
        return temporal_series(panel, expr)


class EMAParams(OperatorParams):
    field: str = "close"
    span: int = 12


@operator
class EMA(Operator):
    """指数移动平均。因果：``ewm_mean(span)`` 是递推式，只用历史值加权。"""

    name: ClassVar[str] = "ema"
    category: ClassVar[str] = "technical"
    inputs: ClassVar[list[str]] = []
    params_cls: ClassVar[type[OperatorParams]] = EMAParams
    min_history: ClassVar[int] = 0

    def apply(self, panel: pl.DataFrame, params: OperatorParams) -> pl.Series:
        assert isinstance(params, EMAParams)
        if params.span < 1:
            raise ValueError(f"ema.span 必须 >= 1，得到 {params.span}")
        expr = (
            pl.col(params.field).ewm_mean(span=params.span).over("symbol").alias("ema")
        )
        return temporal_series(panel, expr)
