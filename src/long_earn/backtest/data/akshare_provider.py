"""akshare 降级数据提供者。

当 miniqmt 不可用且 DuckDB 缓存无数据时，降级到 akshare 获取数据。
akshare 通过 HTTP 请求获取公开市场数据，无需本地客户端。

数据获取后会自动写入 DuckDB 缓存，后续查询直接走缓存。
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import polars as pl
from loguru import logger

from long_earn.backtest.data.cache import DataCache
from long_earn.backtest.data.polars_adapter import to_polars_panel
from long_earn.backtest.data.symbol import xt_to_ak

# akshare 中文列名 → 标准英文列名
KLINE_COLUMN_MAP = {
    "日期": "date",
    "股票代码": "symbol",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
}


class AkshareFallbackProvider:
    """akshare 降级数据提供者。

    当 miniqmt 不可用且 DuckDB 缓存无数据时使用。
    数据获取后自动写入 DuckDB 缓存。
    """

    def __init__(self, cache: DataCache | None = None) -> None:
        self.cache = cache or DataCache()
        self._ak: Any = None

    @property
    def is_available(self) -> bool:
        """检测 akshare 是否可用。"""
        if self._ak is not None:
            return True
        try:
            import akshare as ak  # noqa: PLC0415

            self._ak = ak
            return True
        except Exception as exc:
            logger.warning(f"akshare 不可用: {exc}")
            return False

    def get_price_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """通过 akshare 获取行情数据面板。

        获取后自动写入 DuckDB 缓存。
        """
        if not symbols or not self.is_available:
            return pd.DataFrame()

        fields = fields or ["open", "high", "low", "close", "volume"]
        all_dfs: list[pd.DataFrame] = []

        for symbol in symbols:
            ak_code = xt_to_ak(symbol)
            try:
                df = self._ak.stock_zh_a_hist(
                    symbol=ak_code,
                    period="daily",
                    start_date=start_date.replace("-", ""),
                    end_date=end_date.replace("-", ""),
                    adjust="qfq",
                )
                if df is None or df.empty:
                    continue

                # 列名标准化
                df = df.rename(columns=KLINE_COLUMN_MAP)
                df = df[list(set(KLINE_COLUMN_MAP.values()) & set(df.columns))]
                df["symbol"] = symbol
                df["date"] = pd.to_datetime(df["date"])
                all_dfs.append(df)
            except Exception as e:
                logger.warning(f"akshare 获取 {symbol} 行情失败: {e}")

        if not all_dfs:
            return pd.DataFrame()

        result = pd.concat(all_dfs, ignore_index=True)

        # 写入 DuckDB 缓存
        if not result.empty:
            self.cache.save_prices(result)
            logger.info(
                f"[akshare 降级] 获取 {len(result)} 条行情，"
                f"{result['symbol'].nunique()} 只股票，已写入缓存"
            )

        # 按要求格式返回
        result = result.set_index(["date", "symbol"]).sort_index()
        available_fields = [f for f in fields if f in result.columns]
        return result[available_fields]

    def get_financial_panel(
        self,
        symbols: list[str],
        start_date: str,  # noqa: ARG002
        end_date: str,  # noqa: ARG002
        fields: list[str] | None = None,  # noqa: ARG002
    ) -> pd.DataFrame:
        """通过 akshare 获取财务数据面板。

        获取后自动写入 DuckDB 缓存。
        """
        if not symbols or not self.is_available:
            return pd.DataFrame()

        all_dfs: list[pd.DataFrame] = []

        for symbol in symbols:
            ak_code = xt_to_ak(symbol)
            try:
                df = self._ak.stock_financial_report_sina(
                    stock=ak_code, symbol="利润表"
                )
                if df is None or df.empty:
                    continue

                # 标准化列名
                result_df = pd.DataFrame()
                result_df["symbol"] = symbol
                if "报告日" in df.columns:
                    result_df["report_date"] = pd.to_datetime(
                        df["报告日"], format="%Y%m%d", errors="coerce"
                    )
                if "营业收入" in df.columns:
                    result_df["revenue"] = pd.to_numeric(
                        df["营业收入"], errors="coerce"
                    )
                if "净利润" in df.columns:
                    result_df["net_profit"] = pd.to_numeric(
                        df["净利润"], errors="coerce"
                    )

                result_df = result_df.dropna(subset=["report_date"])
                if not result_df.empty:
                    all_dfs.append(result_df)
            except Exception as e:
                logger.warning(f"akshare 获取 {symbol} 财务数据失败: {e}")

        if not all_dfs:
            return pd.DataFrame()

        result = pd.concat(all_dfs, ignore_index=True)

        # 写入 DuckDB 缓存
        if not result.empty:
            self.cache.save_financials(result)
            logger.info(
                f"[akshare 降级] 获取 {len(result)} 条财务数据，"
                f"{result['symbol'].nunique()} 只股票，已写入缓存"
            )

        return result

    def get_merged_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        price_fields: list[str] | None = None,
        financial_fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """获取合并面板（行情 + 财务）。"""
        price_df = self.get_price_panel(symbols, start_date, end_date, price_fields)
        fin_df = self.get_financial_panel(
            symbols, start_date, end_date, financial_fields
        )
        if price_df.empty and fin_df.empty:
            return pd.DataFrame()
        if price_df.empty:
            return fin_df
        if fin_df.empty:
            return price_df
        merged = price_df.join(fin_df, how="outer")
        merged = merged.groupby(level="symbol").ffill()
        return merged.sort_index()

    def get_merged_panel_as_polars(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> pl.DataFrame:
        """获取合并面板并转为 polars（实现 DataProvider Protocol）。"""
        df = self.get_merged_panel(symbols, start_date, end_date)
        return to_polars_panel(df)
