"""pandas → polars 数据面板适配。

提供两种消费方式：

1. **直接调用纯函数** :func:`to_polars_panel` —— provider 在自身的
   ``get_merged_panel_as_polars`` 实现中调用，无需额外对象。
2. **适配器对象** :class:`PandasToPolarsProvider` —— 包装一个只实现
   pandas 接口的 provider，向后兼容不实现 polars 方法的旧 provider。

历史背景：此模块原定义在 ``services/backtest_service.py``，违反
Clean Architecture（服务层不应承担数据层职责）。现移至数据层，消除
``backtest.engine`` → ``services`` 的反向依赖。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
import polars as pl

if TYPE_CHECKING:
    from long_earn.backtest.data.provider import DataProvider


def to_polars_panel(df: pd.DataFrame) -> pl.DataFrame:
    """把 :meth:`DataProvider.get_merged_panel` 的 pandas 输出转为 polars。

    Args:
        df: pandas DataFrame，index 为 (date, symbol)，含行情+财务列

    Returns:
        polars DataFrame，含 timestamp / symbol / close 等列；空输入返回空 DataFrame

    Raises:
        ValueError: 数据缺少必要列（timestamp / symbol / close）
    """
    if df is None or df.empty:
        return pl.DataFrame()

    df = df.reset_index()
    if "date" in df.columns:
        df = df.rename(columns={"date": "timestamp"})

    required_cols = {"timestamp", "symbol", "close"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"数据缺少必要列: {missing}")

    return pl.from_pandas(df)


class PandasToPolarsProvider:
    """将 pandas 接口的 DataProvider 适配为 polars 输出（向后兼容）。

    当 provider 自身已实现 ``get_merged_panel_as_polars`` 时，无需此适配器；
    仅用于包装只实现 pandas 接口的旧 provider。
    """

    def __init__(self, pandas_provider: DataProvider) -> None:
        self._provider = pandas_provider

    def get_merged_panel_as_polars(
        self, symbols: list[str], start_date: str, end_date: str
    ) -> pl.DataFrame:
        """获取合并面板并转为 polars DataFrame。

        Args:
            symbols: 股票代码列表（已含 .SH/.SZ 后缀，调用方负责格式化）
            start_date: 起始日期（YYYY-MM-DD）
            end_date: 结束日期（YYYY-MM-DD）

        Returns:
            polars DataFrame，含 timestamp / symbol / close 等列；空数据返回空 DataFrame
        """
        df = self._provider.get_merged_panel(
            symbols,
            start_date,
            end_date,
            price_fields=["open", "high", "low", "close", "volume"],
            financial_fields=[
                "net_profit_yoy",
                "roe",
                "revenue_yoy",
                "gross_margin",
            ],
        )
        return to_polars_panel(df)

    @staticmethod
    def format_symbols(symbols: list[str]) -> list[str]:
        """确保符号格式正确（000001 → 000001.SZ，600000 → 600000.SH）。

        委托 :func:`long_earn.backtest.data.symbol.normalize_xt`，
        集中符号转换逻辑避免重复实现。
        """
        from long_earn.backtest.data.symbol import normalize_xt  # noqa: PLC0415

        return [normalize_xt(s) for s in symbols]
