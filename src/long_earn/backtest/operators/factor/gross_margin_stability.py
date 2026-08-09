from typing import ClassVar

import polars as pl

from long_earn.backtest.operators.base import Operator, OperatorParams, operator


class GrossMarginStabilityParams(OperatorParams):
    field: str = "close"
    window: int = 60
    min_periods: int = 30
    eps: float = 1e-10


@operator
class GrossMarginStability(Operator):
    name: ClassVar[str] = "gross_margin_stability"
    category: ClassVar[str] = "factor"
    inputs: ClassVar[list[str]] = ["close"]
    params_cls: ClassVar[type[OperatorParams]] = GrossMarginStabilityParams
    min_history: ClassVar[int] = 0

    def apply(self, panel: pl.DataFrame, params: OperatorParams) -> pl.Series:
        field = params.field
        window = params.window
        min_periods = params.min_periods
        eps = params.eps

        # 保留原始行序，确保因果性验证通过（面板可能 shuffle）。
        panel = panel.with_row_index("__gms_row_id")
        if "symbol" in panel.columns and "timestamp" in panel.columns:
            panel = panel.sort(["symbol", "timestamp"])

        # Helper expressions that only use past data.
        if "symbol" in panel.columns:
            mean_expr = (
                pl.col(field)
                .rolling_mean(window_size=window, min_periods=min_periods)
                .over("symbol")
            )
            std_expr = (
                pl.col(field)
                .rolling_std(window_size=window, min_periods=min_periods)
                .over("symbol")
            )
        else:
            mean_expr = pl.col(field).rolling_mean(
                window_size=window, min_periods=min_periods
            )
            std_expr = pl.col(field).rolling_std(
                window_size=window, min_periods=min_periods
            )

        # Compute rolling mean and std, then combine level and stability.
        # High mean and low volatility => positive score. Also add a small momentum term
        # (current value relative to rolling mean) as a proxy for positive slope.
        out = panel.with_columns(
            mean_expr.alias("_gms_mean"),
            std_expr.alias("_gms_std"),
        ).with_columns(
            (
                -pl.col("_gms_std") / (pl.col("_gms_mean").abs() + eps)
                + (pl.col(field) - pl.col("_gms_mean")) / (pl.col("_gms_std") + eps)
            ).alias("_gms_score")
        )
        # 恢复原始行序
        out = out.sort("__gms_row_id")
        return out["_gms_score"].fill_null(0.0)
