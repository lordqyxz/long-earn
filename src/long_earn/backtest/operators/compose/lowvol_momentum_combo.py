from typing import ClassVar

import polars as pl

from long_earn.backtest.operators.base import Operator, OperatorParams, operator


class LowvolMomentumComboParams(OperatorParams):
    field: str = "close"
    low_vol_lookback: int = 20
    momentum_lookback: int = 20
    low_vol_weight: float = 0.7
    momentum_weight: float = 0.3
    min_obs: int = 5


@operator
class LowvolMomentumCombo(Operator):
    name: ClassVar[str] = "lowvol_momentum_combo"
    category: ClassVar[str] = "compose"
    inputs: ClassVar[list[str]] = ["close"]
    params_cls: ClassVar[type[OperatorParams]] = LowvolMomentumComboParams
    min_history: ClassVar[int] = 0

    def apply(self, panel: pl.DataFrame, params: OperatorParams) -> pl.Series:
        p = params
        df = panel.with_row_index("__lvmc_row_id")
        work = df.select(["__lvmc_row_id", "timestamp", "symbol", p.field]).sort(
            ["symbol", "timestamp"]
        )

        work = work.with_columns(
            pl.col(p.field).pct_change().over("symbol").alias("__ret")
        )
        work = work.with_columns(
            pl.col("__ret")
            .rolling_std(window_size=p.low_vol_lookback, min_periods=p.min_obs)
            .over("symbol")
            .alias("__vol")
        )
        work = work.with_columns(
            (
                pl.col(p.field)
                / pl.col(p.field).shift(p.momentum_lookback).over("symbol")
                - 1.0
            ).alias("__mom")
        )

        work = work.with_columns(
            pl.col("__vol")
            .rank(method="average", descending=True)
            .over("timestamp")
            .alias("__low_vol_rank")
        )
        work = work.with_columns(
            pl.col("__mom")
            .rank(method="average", descending=False)
            .over("timestamp")
            .alias("__mom_rank")
        )

        work = work.with_columns(
            pl.col("__vol").count().over("timestamp").alias("__vol_count")
        )
        work = work.with_columns(
            pl.col("__mom").count().over("timestamp").alias("__mom_count")
        )

        work = work.with_columns(
            (pl.col("__low_vol_rank") / pl.col("__vol_count")).alias("__low_vol_score")
        )
        work = work.with_columns(
            (pl.col("__mom_rank") / pl.col("__mom_count")).alias("__mom_score")
        )

        work = work.with_columns(
            (
                p.low_vol_weight * pl.col("__low_vol_score")
                + p.momentum_weight * pl.col("__mom_score")
            ).alias("__score")
        )

        score = work.sort("__lvmc_row_id").select("__score")["__score"].alias("score")
        return score
