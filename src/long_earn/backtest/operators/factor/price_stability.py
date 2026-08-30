from typing import ClassVar

import polars as pl

from long_earn.backtest.operators.base import Operator, OperatorParams, operator


class PriceStabilityParams(OperatorParams):
    field: str = "close"
    window: int = 60
    min_samples: int = 30
    eps: float = 1e-10


@operator
class PriceStability(Operator):
    """价格稳定性：滚动均值/波动 + 相对均值偏离。

    仅使用 ``params.field`` 价格序列，**不是**基本面毛利率因子。
    旧名 ``gross_margin_stability`` 已废弃，见 ``OPERATOR_RENAMES``。
    """

    name: ClassVar[str] = "price_stability"
    category: ClassVar[str] = "factor"
    inputs: ClassVar[list[str]] = ["close"]
    # 实际依赖字段由 params.field 决定（参数驱动），field_params 据此标注
    field_params: ClassVar[list[str]] = ["field"]
    params_cls: ClassVar[type[OperatorParams]] = PriceStabilityParams
    min_history: ClassVar[int] = 0

    def apply(self, panel: pl.DataFrame, params: OperatorParams) -> pl.Series:
        assert isinstance(params, PriceStabilityParams)
        field = params.field
        window = params.window
        min_samples = params.min_samples
        eps = params.eps

        # 保留原始行序，确保因果性验证通过（面板可能 shuffle）。
        panel = panel.with_row_index("__ps_row_id")
        if "symbol" in panel.columns and "timestamp" in panel.columns:
            panel = panel.sort(["symbol", "timestamp"])

        # Helper expressions that only use past data.
        if "symbol" in panel.columns:
            mean_expr = (
                pl.col(field)
                .rolling_mean(window_size=window, min_samples=min_samples)
                .over("symbol")
            )
            std_expr = (
                pl.col(field)
                .rolling_std(window_size=window, min_samples=min_samples)
                .over("symbol")
            )
        else:
            mean_expr = pl.col(field).rolling_mean(
                window_size=window, min_samples=min_samples
            )
            std_expr = pl.col(field).rolling_std(
                window_size=window, min_samples=min_samples
            )

        # High mean and low volatility => positive score. Also add a small
        # momentum term (current vs rolling mean) as a slope proxy.
        out = panel.with_columns(
            mean_expr.alias("_ps_mean"),
            std_expr.alias("_ps_std"),
        ).with_columns(
            (
                -pl.col("_ps_std") / (pl.col("_ps_mean").abs() + eps)
                + (pl.col(field) - pl.col("_ps_mean")) / (pl.col("_ps_std") + eps)
            ).alias("_ps_score")
        )
        out = out.sort("__ps_row_id")
        return out["_ps_score"]
