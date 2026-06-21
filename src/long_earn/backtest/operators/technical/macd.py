"""MACD 指标算子，返回带 macd/signal/histogram 三列的 DataFrame。"""

from typing import ClassVar

import polars as pl

from long_earn.backtest.operators._util import temporal_series
from long_earn.backtest.operators.base import Operator, OperatorParams, operator


class MACDParams(OperatorParams):
    field: str = "close"
    fast: int = 12
    slow: int = 26
    signal: int = 9


@operator
class MACD(Operator):
    """MACD：``macd = EMA(fast) - EMA(slow)``；``signal = EMA(macd, signal)``；
    ``histogram = macd - signal``。

    因果性：EMA 是递推式因果滤波，三者组合仅用历史。``fast/slow/signal >= 1``
    且 ``fast < slow``。
    """

    name: ClassVar[str] = "macd"
    category: ClassVar[str] = "technical"
    inputs: ClassVar[list[str]] = []
    params_cls: ClassVar[type[OperatorParams]] = MACDParams
    min_history: ClassVar[int] = 0

    def apply(self, panel: pl.DataFrame, params: OperatorParams) -> pl.DataFrame:
        assert isinstance(params, MACDParams)
        if params.fast < 1 or params.slow < 1 or params.signal < 1:
            raise ValueError("macd.fast/slow/signal 必须 >= 1")
        if params.fast >= params.slow:
            raise ValueError(f"macd.fast({params.fast}) 必须 < slow({params.slow})")
        col = pl.col(params.field)
        macd_line = (
            col.ewm_mean(span=params.fast) - col.ewm_mean(span=params.slow)
        ).over("symbol")
        # signal 需要在 macd_line 之上再做 ewm，分两步
        macd_series = temporal_series(panel, macd_line.alias("macd"))
        with_macd = panel.with_columns(macd_series.alias("macd"))
        signal_series = temporal_series(
            with_macd,
            pl.col("macd").ewm_mean(span=params.signal).over("symbol").alias("signal"),
        )
        hist = macd_series - signal_series
        return panel.with_columns(
            macd_series.alias("macd"),
            signal_series.alias("signal"),
            hist.alias("histogram"),
        )
