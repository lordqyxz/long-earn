"""RSI 相对强弱指标算子（简单滚动平均版，因果）。"""

from typing import ClassVar

import polars as pl

from long_earn.backtest.operators._util import temporal_series
from long_earn.backtest.operators.base import Operator, OperatorParams, operator


class RSIParams(OperatorParams):
    field: str = "close"
    window: int = 14


@operator
class RSI(Operator):
    """RSI（简单滚动平均版）。

    计算：``diff = field - shift(field,1)``；``gain=max(diff,0)``、
    ``loss=max(-diff,0)``；``RS = rolling_mean(gain,window)/rolling_mean(loss,window)``；
    ``RSI = 100 - 100/(1+RS)``。

    因果性：diff 用 ``shift(1)``（仅上一期），gain/loss/RS 全用滚动历史窗口，
    不窥未来。``window < 1`` 禁止。
    """

    name: ClassVar[str] = "rsi"
    category: ClassVar[str] = "technical"
    inputs: ClassVar[list[str]] = []
    params_cls: ClassVar[type[OperatorParams]] = RSIParams
    min_history: ClassVar[int] = 0

    def apply(self, panel: pl.DataFrame, params: OperatorParams) -> pl.Series:
        assert isinstance(params, RSIParams)
        if params.window < 1:
            raise ValueError(f"rsi.window 必须 >= 1，得到 {params.window}")
        # 注意：必须先按 (symbol,timestamp) 排序再算 diff，否则 shift 跨 symbol 串味
        expr = (
            (
                100
                - 100
                / (
                    1
                    + (
                        (pl.col(params.field) - pl.col(params.field).shift(1))
                        .clip(lower_bound=0)
                        .rolling_mean(params.window)
                        / (
                            (pl.col(params.field).shift(1) - pl.col(params.field))
                            .clip(lower_bound=0)
                            .rolling_mean(params.window)
                        )
                    )
                )
            )
            .over("symbol")
            .alias("rsi")
        )
        return temporal_series(panel, expr)
