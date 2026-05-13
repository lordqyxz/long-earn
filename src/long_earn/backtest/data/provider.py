"""数据提供者模块

封装 akshare 数据获取，支持本地 DuckDB 缓存。
"""

import logging
import time
from typing import Protocol

import akshare as ak
import pandas as pd

from long_earn.backtest.data.cache import DataCache

logger = logging.getLogger(__name__)

# akshare 业绩快报列名映射（中文 -> 英文标准名）
FINANCIAL_FIELD_MAP = {
    "净利润-同比增长": "net_profit_yoy",
    "营业总收入-同比增长": "revenue_yoy",
    "净资产收益率": "roe",
    "销售毛利率": "gross_margin",
    "每股收益": "eps",
    "净利润-净利润": "net_profit",
    "营业总收入-营业总收入": "revenue",
}

# 季度报告日期列表（支持常见的报告期）
QUARTER_END_DATES = ["0331", "0630", "0930", "1231"]


class DataProvider(Protocol):
    """数据提供者接口"""

    def get_price_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame: ...

    def get_financial_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame: ...

    def get_merged_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        price_fields: list[str] | None = None,
        financial_fields: list[str] | None = None,
    ) -> pd.DataFrame: ...


class AkshareDataProvider:
    """基于 akshare 的数据提供者（带 DuckDB 缓存）"""

    def __init__(self, cache: DataCache | None = None):
        self.cache = cache or DataCache()

    def get_price_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """获取行情数据面板"""
        if not symbols:
            return pd.DataFrame()

        fields = fields or ["open", "high", "low", "close", "volume"]
        cached_df = self.cache.get_prices(symbols, start_date, end_date)
        missing_symbols = symbols
        if cached_df is not None:
            cached_symbols = set(cached_df["symbol"].unique())
            missing_symbols = [s for s in symbols if s not in cached_symbols]
            if missing_symbols:
                logger.info(
                    f"行情缓存缺失 {len(missing_symbols)} 只股票，从 akshare 补充"
                )
        if missing_symbols:
            fetched = self._fetch_prices_from_akshare(
                missing_symbols, start_date, end_date
            )
            if fetched is not None and not fetched.empty:
                self.cache.save_prices(fetched)
        df = self.cache.get_prices(symbols, start_date, end_date, fields)
        if df is None or df.empty:
            logger.warning("无法获取行情数据")
            return pd.DataFrame()
        df = df.set_index(["date", "symbol"]).sort_index()
        return df[fields]

    def _fetch_prices_from_akshare(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame | None:
        """从 akshare 获取行情数据（使用 stock_zh_a_daily 接口）"""

        all_data = []
        ak_start = start_date.replace("-", "")
        ak_end = end_date.replace("-", "")

        def _to_akshare_symbol(code: str) -> str:
            """将股票代码转换为 akshare 格式（sh/sz 前缀）"""
            code = str(code).strip()
            if code.startswith("6") or code.startswith("68"):
                return f"sh{code}"
            return f"sz{code}"

        for i, symbol in enumerate(symbols):
            if (i + 1) % 50 == 0:
                logger.info(f"行情获取进度: {i + 1}/{len(symbols)}")
            try:
                ak_symbol = _to_akshare_symbol(symbol)
                df = ak.stock_zh_a_daily(
                    symbol=ak_symbol,
                    start_date=ak_start,
                    end_date=ak_end,
                    adjust="qfq",
                )
                if df is None or df.empty:
                    continue
                df["symbol"] = symbol
                df["date"] = pd.to_datetime(df["date"])
                all_data.append(
                    df[["symbol", "date", "open", "high", "low", "close", "volume"]]
                )
                # 请求间隔，避免触发限流
                time.sleep(0.15)
            except Exception as e:
                logger.warning(f"获取 {symbol} 行情失败: {e}")
                continue
        if not all_data:
            return None
        result = pd.concat(all_data, ignore_index=True)
        logger.info(
            f"从 akshare 获取行情 {len(result)} 条记录, "
            f"{result['symbol'].nunique()} 只股票"
        )
        return result

    def get_financial_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """获取财务数据面板（日级别）"""
        if not symbols:
            return pd.DataFrame()

        fields = fields or list(FINANCIAL_FIELD_MAP.values())
        quarters = self._get_quarters_between(start_date, end_date)
        cached_df = self.cache.get_financials(symbols, fields)
        missing_quarters = quarters
        if cached_df is not None and not cached_df.empty:
            cached_quarters = set(
                cached_df["report_date"].dt.strftime("%Y%m%d").unique()
            )
            missing_quarters = [q for q in quarters if q not in cached_quarters]
        if missing_quarters:
            fetched = self._fetch_financials_from_akshare(missing_quarters)
            if fetched is not None and not fetched.empty:
                self.cache.save_financials(fetched)
        df = self.cache.get_financials(symbols, fields)
        if df is None or df.empty:
            logger.warning("无法获取财务数据")
            return pd.DataFrame()
        trading_dates = pd.date_range(start=start_date, end=end_date, freq="B")
        panel = self._quarterly_to_daily(df, symbols, trading_dates, fields)
        return panel

    def _fetch_financials_from_akshare(
        self, quarters: list[str]
    ) -> pd.DataFrame | None:
        """从 akshare 获取季度财务数据"""
        all_data = []
        for quarter in quarters:
            logger.info(f"获取 {quarter} 季度财务数据...")
            try:
                df = ak.stock_yjbb_em(date=quarter)
                if df is None or df.empty:
                    continue
                df = df.rename(columns=FINANCIAL_FIELD_MAP)
                df["symbol"] = df["股票代码"].astype(str).str.strip()
                df["report_date"] = pd.to_datetime(quarter, format="%Y%m%d")
                available_cols = ["symbol", "report_date"] + [
                    c for c in FINANCIAL_FIELD_MAP.values() if c in df.columns
                ]
                all_data.append(df[available_cols])
                logger.info(f"  {quarter}: {len(df)} 条记录")
            except Exception as e:
                logger.warning(f"获取 {quarter} 财务数据失败: {e}")
                continue
        if not all_data:
            return None
        result = pd.concat(all_data, ignore_index=True)
        logger.info(
            f"从 akshare 获取财务数据 {len(result)} 条记录, "
            f"{result['symbol'].nunique()} 只股票"
        )
        return result

    def _quarterly_to_daily(
        self,
        quarterly_df: pd.DataFrame,
        symbols: list[str],
        trading_dates: pd.DatetimeIndex,
        fields: list[str],
    ) -> pd.DataFrame:
        """将季度财务数据前向填充到日级别"""
        panels = []
        for symbol in symbols:
            symbol_data = quarterly_df[quarterly_df["symbol"] == symbol].copy()
            if symbol_data.empty:
                continue
            symbol_data = symbol_data.sort_values("report_date")
            daily = pd.DataFrame(index=trading_dates)
            daily.index.name = "date"
            for _, row in symbol_data.iterrows():
                report_date = row["report_date"]
                mask = daily.index >= report_date
                for field in fields:
                    if field in row:
                        daily.loc[mask, field] = row[field]
            daily["symbol"] = symbol
            daily = daily.reset_index().set_index(["date", "symbol"])
            panels.append(daily)
        if not panels:
            return pd.DataFrame()
        result = pd.concat(panels)
        return result[fields]

    def get_merged_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        price_fields: list[str] | None = None,
        financial_fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """获取合并的数据面板"""
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

    @staticmethod
    def _get_quarters_between(start_date: str, end_date: str) -> list[str]:
        """获取日期范围内的所有季度报告期

        包含 start_date 之前的最近一期报告（用于前向填充）。
        """
        start = pd.to_datetime(start_date)
        end = pd.to_datetime(end_date)

        # 生成所有可能的季度
        all_quarters = []
        for year in range(start.year - 1, end.year + 1):
            for qe in QUARTER_END_DATES:
                all_quarters.append(f"{year}{qe}")

        # 筛选出在 [start, end] 范围内的报告
        quarters = [
            q
            for q in all_quarters
            if start <= pd.to_datetime(q, format="%Y%m%d") <= end
        ]

        # 额外添加 start_date 之前的最近一期报告
        before_start = [
            q for q in all_quarters if pd.to_datetime(q, format="%Y%m%d") < start
        ]
        if before_start:
            quarters.append(max(before_start))

        return sorted(set(quarters))


def get_data_provider(cache: DataCache | None = None) -> DataProvider:
    """获取默认的数据提供者"""
    return AkshareDataProvider(cache)
