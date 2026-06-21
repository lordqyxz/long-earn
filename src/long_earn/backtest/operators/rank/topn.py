"""横截面排名算子：在同一 timestamp 内对 field 排名，取 top N。"""

from typing import ClassVar

import polars as pl

from long_earn.backtest.operators.base import Operator, OperatorParams, operator


class RankTopParams(OperatorParams):
    field: str
    top: int = 10
    ascending: bool = False


@operator
class RankTop(Operator):
    """``rank_top(field, top, ascending)`` —— 每个时刻截面内按 field 排序取前 N。

    因果性：排名仅用同一 timestamp 的截面数据，不跨时刻、不窥未来。
    返回带 ``rank`` 列的 DataFrame（已对齐 panel 行序），rank=1 为最优；
    未入选行 rank 为 null。
    """

    name: ClassVar[str] = "rank_top"
    category: ClassVar[str] = "rank"
    inputs: ClassVar[list[str]] = []
    params_cls: ClassVar[type[OperatorParams]] = RankTopParams
    min_history: ClassVar[int] = 0

    def apply(self, panel: pl.DataFrame, params: OperatorParams) -> pl.DataFrame:
        assert isinstance(params, RankTopParams)
        if params.top < 1:
            raise ValueError(f"rank_top.top 必须 >= 1，得到 {params.top}")
        if panel.height == 0:
            return panel.with_columns(pl.lit(None).alias("rank"))

        # over("timestamp") 保证只在同一时刻截面内排名，不跨时刻
        ranked_expr = (
            pl.col(params.field)
            .rank(method="ordinal", descending=not params.ascending)
            .over("timestamp")
            .alias("rank")
        )
        with_rank = panel.with_columns(ranked_expr)
        # 仅保留 top N（rank <= top），其余 rank 置 null
        mask = pl.col("rank") <= params.top
        return with_rank.with_columns(
            pl.when(mask).then(pl.col("rank")).otherwise(None).alias("rank")
        )
