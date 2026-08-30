from typing import ClassVar

import polars as pl

from long_earn.backtest.operators.base import Operator, OperatorParams, operator


class RoeQualityParams(OperatorParams):
    field: str = "close"
    window: int = 20
    min_samples: int = 5


@operator
class RoeQuality(Operator):
    """价格动量质量代理（**非基本面 ROE**）。

    名称保留 ``roe_quality`` 以兼容既有策略 YAML；实际仅用 ``params.field``
    价格序列的滚动收益均值/波动比（类 Sharpe），不读取财务 ``roe`` 列。
    """

    name: ClassVar[str] = "roe_quality"
    category: ClassVar[str] = "factor"
    inputs: ClassVar[list[str]] = ["close"]
    # 实际依赖字段由 params.field 决定（参数驱动），field_params 据此标注
    field_params: ClassVar[list[str]] = ["field"]
    params_cls: ClassVar[type[OperatorParams]] = RoeQualityParams
    min_history: ClassVar[int] = 0

    def apply(self, panel: pl.DataFrame, params: OperatorParams) -> pl.Series:
        assert isinstance(params, RoeQualityParams)
        p = params
        # Preserve original row order
        panel = panel.with_columns(pl.Series(range(panel.height)).alias("__row"))
        # Sort for time series calculations
        if "timestamp" in panel.columns and "symbol" in panel.columns:
            panel = panel.sort(["symbol", "timestamp"])
        elif "symbol" in panel.columns:
            panel = panel.sort("symbol")
        # Daily returns per symbol (uses only past close values)
        ret_expr = pl.col(p.field).pct_change().over("symbol").alias("ret")
        panel = panel.with_columns(ret_expr)
        # Rolling mean and std of returns (trailing window)
        mean_expr = (
            pl.col("ret")
            .rolling_mean(window_size=p.window, min_samples=p.min_samples)
            .over("symbol")
            .alias("ret_mean")
        )
        std_expr = (
            pl.col("ret")
            .rolling_std(window_size=p.window, min_samples=p.min_samples)
            .over("symbol")
            .alias("ret_std")
        )
        panel = panel.with_columns([mean_expr, std_expr])
        # Quality score: higher mean return and lower volatility -> higher score
        score_expr = (
            pl.when((pl.col("ret_std") == 0) | (pl.col("ret_std").is_null()))
            .then(0.0)
            .otherwise(pl.col("ret_mean") / pl.col("ret_std"))
            .alias("__factor")
        )
        panel = panel.with_columns(score_expr)
        # Restore original row order
        panel = panel.sort("__row")
        return panel["__factor"]
