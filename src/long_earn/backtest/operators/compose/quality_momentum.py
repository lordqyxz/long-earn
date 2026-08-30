from typing import ClassVar

import polars as pl

from long_earn.backtest.operators.base import Operator, OperatorParams, operator


class QualityMomentumParams(OperatorParams):
    field: str = "close"
    momentum_window: int = 20
    quality_window: int = 60


@operator
class QualityMomentum(Operator):
    name: ClassVar[str] = "quality_momentum"
    category: ClassVar[str] = "compose"
    inputs: ClassVar[list[str]] = []
    # 实际依赖字段由 params.field 决定（参数驱动），field_params 据此标注
    field_params: ClassVar[list[str]] = ["field"]
    params_cls: ClassVar[type[OperatorParams]] = QualityMomentumParams
    min_history: ClassVar[int] = 0

    def apply(self, panel: pl.DataFrame, params: OperatorParams) -> pl.Series:
        assert isinstance(params, QualityMomentumParams)
        p = params
        # 保留原始行序，确保因果性验证通过（面板可能 shuffle）。
        panel = panel.with_row_index("__qm_row_id")
        if "symbol" in panel.columns and "timestamp" in panel.columns:
            panel = panel.sort(["symbol", "timestamp"])

        fld = pl.col(p.field)
        has_symbol = "symbol" in panel.columns

        # Momentum: past N-period return (per symbol)
        mom_expr = (fld / fld.shift(p.momentum_window) - 1.0).alias("__mom")
        if has_symbol:
            mom_expr = mom_expr.over("symbol")

        # Quality proxy: stability of fundamentals if available, else price volatility
        if "roe" in panel.columns and "gross_margin" in panel.columns:
            roe_vol = pl.col("roe").rolling_std(p.quality_window)
            gm_vol = pl.col("gross_margin").rolling_std(p.quality_window)
            if has_symbol:
                roe_vol = roe_vol.over("symbol")
                gm_vol = gm_vol.over("symbol")
            qual_expr = (
                (1.0 / (1.0 + roe_vol) + 1.0 / (1.0 + gm_vol)) / 2.0
            ).alias("__qual")
        else:
            ret_expr = fld.pct_change().alias("__ret")
            if has_symbol:
                ret_expr = ret_expr.over("symbol")
            panel = panel.with_columns(ret_expr)
            vol_expr = pl.col("__ret").rolling_std(p.quality_window)
            if has_symbol:
                vol_expr = vol_expr.over("symbol")
            qual_expr = (1.0 / (1.0 + vol_expr)).alias("__qual")

        panel = panel.with_columns([mom_expr, qual_expr])
        score = (pl.col("__mom") * pl.col("__qual")).alias(p.field)
        panel = panel.with_columns(score)

        # 恢复原始行序
        result = panel.sort("__qm_row_id")
        return result[p.field]
