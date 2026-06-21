"""算子内部共享工具（不参与目录扫描）。

核心是 :func:`temporal_series`：在保证按 ``(symbol, timestamp)`` 时间序计算
的前提下，把结果 Series **对齐回 panel 原始行序**。这样无论输入 panel 的行
排列如何，时序算子（shift / rolling）都正确回溯历史，且输出可直接赋值回
panel 的对应行。

因果性说明：所有时序变换（``shift(positive)``、``rolling_*``）都是仅回溯
过去的算子，不读取未来行——这是算子目录 ``causal=True`` 契约的数值基础。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import polars as pl


def temporal_series(
    panel: pl.DataFrame,
    expr: pl.Expr,
) -> pl.Series:
    """在 (symbol, timestamp) 时间序下计算 ``expr``（含 ``over("symbol")``），
    结果对齐回 panel 原始行序返回。

    Args:
        panel: 含 ``timestamp`` / ``symbol`` 列的面板。
        expr: 形如 ``pl.col("close").shift(5).over("symbol")`` 的表达式。

    Returns:
        与 panel 等长、按原始行序对齐的 Series。
    """

    import polars as pl  # noqa: PLC0415

    if panel.height == 0:
        return pl.Series(name=expr.meta.output_name() or "_", values=[])

    indexed = panel.with_row_index("_op_row_idx")
    sorted_df = indexed.sort(["symbol", "timestamp"])
    computed = sorted_df.with_columns(expr)
    back = computed.sort("_op_row_idx")
    name = expr.meta.output_name()
    series = back[name]
    # 去掉可能的索引列污染，返回纯 Series
    return series.rename(name) if name else series


def cross_section(panel: pl.DataFrame, expr: pl.Expr) -> pl.Series:
    """横截面计算（每个 timestamp 内独立排序/排名），对齐回原始行序。

    用于 rank 算子：在同一时刻的 symbol 截面内排名，不跨时刻、不窥未来。
    """

    import polars as pl  # noqa: PLC0415

    if panel.height == 0:
        return pl.Series(name=expr.meta.output_name() or "_", values=[])

    indexed = panel.with_row_index("_op_row_idx")
    # 按 timestamp 分组即可，组内保持 symbol 顺序；over("timestamp") 不重排输出
    computed = indexed.with_columns(expr)
    back = computed.sort("_op_row_idx")
    name = expr.meta.output_name()
    return back[name]
