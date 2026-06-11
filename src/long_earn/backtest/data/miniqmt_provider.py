"""基于 miniqmt (xtquant.xtdata) 的本地数据提供者。

替代 akshare，直接调用 xtquant.xtdata 获取行情、财务、板块等数据。

xtquant 数据格式说明：
  - get_market_data_ex(): 返回 {symbol: {field: [value, ...]}}，含 'time' 字段（时间戳秒）
  - get_financial_data(): 返回 {symbol: DataFrame}，含 'report_time', 'net_profit', 'net_profit_yoy' 等
  - get_stock_list_in_sector(): 返回 [symbol, ...]
  - get_instrument_detail(): 返回 {field: value} 字典
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from long_earn.backtest.data.cache import DataCache

logger = logging.getLogger(__name__)


# 指数代码 -> 板块名称映射
INDEX_SECTOR_MAP = {
    "csi300": "沪深300",
    "csi500": "中证500",
    "sse50": "上证50",
    "csi1000": "中证1000",
}

# 财务字段映射：xtquant 字段 -> 标准字段名
FINANCIAL_FIELD_MAP = {
    "net_profit_yoy": "net_profit_yoy",
    "roe": "roe",
    "revenue_yoy": "revenue_yoy",
    "gross_profit_margin": "gross_margin",
    "net_profit": "net_profit",
    "operating_revenue": "revenue",
    "eps": "eps",
}


class MiniQmtClient:
    """封装 xtquant.xtdata 的本地同步客户端。

    按需延迟加载 xtquant，避免在不可用的环境中报错。
    """

    _instance: MiniQmtClient | None = None

    def __init__(self) -> None:
        self._xtdata: Any = None

    @classmethod
    def get(cls) -> MiniQmtClient:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _ensure_xtdata(self) -> Any:
        if self._xtdata is not None:
            return self._xtdata
        try:
            from xtquant import xtdata

            self._xtdata = xtdata
            logger.info("xtquant.xtdata 加载成功")
        except Exception as exc:
            logger.error(f"xtquant.xtdata 不可用: {exc}")
            raise RuntimeError("xtquant.xtdata is not available") from exc
        return self._xtdata

    # ── K线 ───────────────────────────────────────────────────────────────

    def get_kline(
        self,
        stock_list: list[str],
        start_time: str = "",
        end_time: str = "",
        period: str = "1d",
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """获取多只股票的 K 线数据，返回标准化 DataFrame。

        返回列：date, symbol, open, high, low, close, volume
        """
        xtdata = self._ensure_xtdata()
        result_fields = fields or ["time", "open", "high", "low", "close", "volume"]
        if "time" not in result_fields:
            result_fields = ["time", *fields] if fields else result_fields

        raw = xtdata.get_market_data_ex(
            field_list=result_fields,
            stock_list=stock_list,
            period=period,
            start_time=start_time,
            end_time=end_time,
            count=-1,
            dividend_type="front",
            fill_data=False,
        )

        rows: list[dict[str, Any]] = []
        for symbol, data in (raw or {}).items():
            times = data.get("time", [])
            opens = data.get("open", [])
            highs = data.get("high", [])
            lows = data.get("low", [])
            closes = data.get("close", [])
            volumes = data.get("volume", [])
            n = len(times)
            for i in range(n):
                ts = int(times[i]) if times[i] else 0
                dt = pd.to_datetime(ts, unit="s", utc=True).tz_convert(
                    "Asia/Shanghai"
                )
                rows.append(
                    {
                        "date": dt.strftime("%Y-%m-%d"),
                        "symbol": symbol,
                        "open": float(opens[i]) if i < len(opens) else 0.0,
                        "high": float(highs[i]) if i < len(highs) else 0.0,
                        "low": float(lows[i]) if i < len(lows) else 0.0,
                        "close": float(closes[i]) if i < len(closes) else 0.0,
                        "volume": float(volumes[i]) if i < len(volumes) else 0.0,
                    }
                )

        df = pd.DataFrame(rows)
        if not df.empty:
            df["date"] = pd.to_datetime(df["date"])
        logger.debug(f"get_kline 返回 {len(df)} 行，{len(stock_list)} 只股票")
        return df

    # ── 财务数据 ──────────────────────────────────────────────────────────

    def get_financial(
        self,
        stock_list: list[str],
        start_time: str = "",
        end_time: str = "",
        table: str = "Income",
    ) -> pd.DataFrame:
        """获取财务数据。

        返回列：report_date, symbol, net_profit, net_profit_yoy, revenue, roe, ...
        """
        xtdata = self._ensure_xtdata()
        raw = xtdata.get_financial_data(
            stock_list=stock_list,
            table_list=[table],
            start_time=start_time,
            end_time=end_time,
            report_type="report_time",
        )

        rows: list[dict[str, Any]] = []
        for symbol, tables in (raw or {}).items():
            for table_name, df_table in tables.items():
                if df_table is None or (hasattr(df_table, "empty") and df_table.empty):
                    continue
                tmp = df_table.copy() if hasattr(df_table, "copy") else pd.DataFrame(df_table)
                if isinstance(tmp, pd.DataFrame) and not tmp.empty:
                    for col in tmp.columns:
                        # 如果是时间戳列，转为 datetime
                        if tmp[col].dtype == "int64" and col in ("report_time", "pub_time"):
                            tmp[col] = pd.to_datetime(tmp[col], unit="s", errors="ignore")
                    tmp["symbol"] = symbol
                    tmp["report_date"] = tmp.get("report_time", pd.NaT)
                    rows.extend(tmp.to_dict("records"))

        result = pd.DataFrame(rows)
        if not result.empty and "report_date" in result.columns:
            result["report_date"] = pd.to_datetime(result["report_date"], errors="coerce")
        return result

    # ── 板块/股票池 ──────────────────────────────────────────────────────

    def get_sector_stocks(self, sector_name: str) -> list[str]:
        """获取某个板块/指数的成分股列表。"""
        xtdata = self._ensure_xtdata()
        try:
            result = xtdata.get_stock_list_in_sector(sector_name)
            return list(result or [])
        except Exception as e:
            logger.warning(f"获取板块 {sector_name} 成分股失败: {e}")
            return []

    # ── 标的信息 ──────────────────────────────────────────────────────────

    def get_instrument_detail(self, stock_code: str) -> dict[str, Any]:
        """获取标的基础信息。"""
        xtdata = self._ensure_xtdata()
        result = xtdata.get_instrument_detail(stock_code)
        return dict(result or {})

    # ── 实时行情 ──────────────────────────────────────────────────────────

    def get_full_tick(self, code_list: list[str]) -> dict[str, Any]:
        """获取最新逐笔行情。"""
        xtdata = self._ensure_xtdata()
        result = xtdata.get_full_tick(code_list)
        return dict(result or {})


# ─────────────────────────────────────────────────────────────────────────
# 缓存层：在 DuckDB 中缓存 miniqmt 历史数据
# ─────────────────────────────────────────────────────────────────────────


class MiniQmtDataProvider:
    """基于 miniqmt (xtquant.xtdata) + DuckDB 缓存的数据提供者。"""

    def __init__(self, cache: DataCache | None = None) -> None:
        self.cache = cache or DataCache()
        self.client = MiniQmtClient.get()

    # ── 行情面板 ─────────────────────────────────────────────────────────

    def get_price_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """获取行情数据面板（优先读缓存，缺失再下载）。"""
        if not symbols:
            return pd.DataFrame()

        fields = fields or ["open", "high", "low", "close", "volume"]
        cached_df = self.cache.get_prices(symbols, start_date, end_date)

        missing_symbols = list(symbols)
        if cached_df is not None and not cached_df.empty:
            cached_symbols = set(cached_df["symbol"].unique())
            missing_symbols = [s for s in symbols if s not in cached_symbols]

        if missing_symbols:
            logger.info(f"行情缓存缺失 {len(missing_symbols)} 只，从 miniqmt 补充")
            fetched = self._fetch_kline(missing_symbols, start_date, end_date)
            if fetched is not None and not fetched.empty:
                self.cache.save_prices(fetched)

        df = self.cache.get_prices(symbols, start_date, end_date, fields)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.set_index(["date", "symbol"]).sort_index()
        return df[fields]

    def _fetch_kline(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame | None:
        """从 miniqmt 下载 K 线数据。"""
        try:
            start_fmt = start_date.replace("-", "")
            end_fmt = end_date.replace("-", "")
            df = self.client.get_kline(
                stock_list=symbols,
                start_time=start_fmt,
                end_time=end_fmt,
                period="1d",
            )
            if df.empty:
                return None
            logger.info(f"miniqmt 获取 {len(df)} 条行情，{df['symbol'].nunique()} 只股票")
            return df
        except Exception as e:
            logger.warning(f"miniqmt 行情下载失败: {e}")
            return None

    # ── 财务面板 ─────────────────────────────────────────────────────────

    def get_financial_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """获取财务数据面板（前向填充到日级）。"""
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
            fetched = self._fetch_financials(symbols, start_date, end_date)
            if fetched is not None and not fetched.empty:
                self.cache.save_financials(fetched)

        df = self.cache.get_financials(symbols, fields)
        if df is None or df.empty:
            return pd.DataFrame()

        trading_dates = pd.date_range(start=start_date, end=end_date, freq="B")
        panel = self._quarterly_to_daily(df, symbols, trading_dates, fields)
        return panel

    def _fetch_financials(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame | None:
        """从 miniqmt 下载财务数据。"""
        try:
            start_fmt = start_date.replace("-", "")
            end_fmt = end_date.replace("-", "")
            # 先下载利润表
            income_df = self.client.get_financial(
                stock_list=symbols,
                start_time=start_fmt,
                end_time=end_fmt,
                table="Income",
            )
            if income_df.empty:
                return None

            # 字段标准化
            result_df = pd.DataFrame()
            result_df["symbol"] = income_df.get("symbol", symbols[0])
            result_df["report_date"] = income_df.get("report_date", pd.NaT)

            # xtquant 中的常用字段
            if "MFSumOperatingRevenueYOY" in income_df.columns:
                result_df["revenue_yoy"] = income_df["MFSumOperatingRevenueYOY"]
            if "MFSumNetProfitYOY" in income_df.columns:
                result_df["net_profit_yoy"] = income_df["MFSumNetProfitYOY"]
            if "net_profit" in income_df.columns:
                result_df["net_profit"] = income_df["net_profit"]
            if "MFSumOperatingRevenue" in income_df.columns:
                result_df["revenue"] = income_df["MFSumOperatingRevenue"]
            if "gross_profit_margin" in income_df.columns:
                result_df["gross_margin"] = income_df["gross_profit_margin"]

            # 保留 xtquant 返回的其他可用字段
            for col in income_df.columns:
                if col not in result_df.columns and col not in (
                    "symbol",
                    "report_time",
                    "pub_time",
                ):
                    result_df[col] = income_df[col]

            logger.info(
                f"miniqmt 获取 {len(result_df)} 条财务数据，{result_df['symbol'].nunique()} 只股票"
            )
            return result_df
        except Exception as e:
            logger.warning(f"miniqmt 财务数据下载失败: {e}")
            return None

    def _quarterly_to_daily(
        self,
        quarterly_df: pd.DataFrame,
        symbols: list[str],
        trading_dates: pd.DatetimeIndex,
        fields: list[str],
    ) -> pd.DataFrame:
        """将季度财务数据前向填充到日级。"""
        panels: list[pd.DataFrame] = []
        for symbol in symbols:
            symbol_data = quarterly_df[quarterly_df["symbol"] == symbol].copy()
            if symbol_data.empty:
                continue
            symbol_data = symbol_data.sort_values("report_date")
            daily = pd.DataFrame(index=trading_dates)
            daily.index.name = "date"
            for _, row in symbol_data.iterrows():
                report_date = row["report_date"]
                if pd.isna(report_date):
                    continue
                mask = daily.index >= pd.to_datetime(report_date)
                for field in fields:
                    if field in row and pd.notna(row[field]):
                        daily.loc[mask, field] = float(row[field])
            daily["symbol"] = symbol
            daily = daily.reset_index().set_index(["date", "symbol"])
            panels.append(daily)
        if not panels:
            return pd.DataFrame()
        result = pd.concat(panels)
        return result

    def get_merged_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        price_fields: list[str] | None = None,
        financial_fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """获取合并的数据面板（行情 + 财务）。"""
        price_df = self.get_price_panel(symbols, start_date, end_date, price_fields)
        fin_df = self.get_financial_panel(symbols, start_date, end_date, financial_fields)
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
        """获取日期范围内的所有季度报告期。"""
        start = pd.to_datetime(start_date)
        end = pd.to_datetime(end_date)
        all_quarters: list[str] = []
        for year in range(start.year - 1, end.year + 1):
            for qe in ["0331", "0630", "0930", "1231"]:
                all_quarters.append(f"{year}{qe}")
        quarters = [
            q
            for q in all_quarters
            if start <= pd.to_datetime(q, format="%Y%m%d") <= end
        ]
        before_start = [
            q
            for q in all_quarters
            if pd.to_datetime(q, format="%Y%m%d") < start
        ]
        if before_start:
            quarters.append(max(before_start))
        return sorted(set(quarters))


# ─────────────────────────────────────────────────────────────────────────
# 股票池
# ─────────────────────────────────────────────────────────────────────────


class MiniQmtUniverseProvider:
    """基于 miniqmt 板块/指数接口的股票池提供者。"""

    def __init__(self, cache: DataCache | None = None) -> None:
        self.cache = cache or DataCache()
        self.client = MiniQmtClient.get()

    def get_symbols(self, universe_type: str, date: str = "") -> list[str]:
        """获取指定类型的股票池。"""
        if "+" in universe_type:
            parts = universe_type.split("+")
            symbols: set[str] = set()
            for part in parts:
                symbols.update(self._get_single_universe(part.strip(), date))
            return sorted(symbols)
        return self._get_single_universe(universe_type, date)

    def _get_single_universe(self, universe_type: str, date: str) -> list[str]:
        # 指数：沪深300 / 中证500 / 上证50 / 中证1000
        if universe_type in INDEX_SECTOR_MAP:
            return self._get_index_constituents(INDEX_SECTOR_MAP[universe_type], date)
        # 中文板块名
        sector_name = universe_type
        if sector_name in ("all_a", "全A股"):
            return self._get_all_a_stocks(date)
        # 默认：按板块名查询
        cached = self.cache.get_universe(sector_name, date)
        if cached:
            return cached
        result = self.client.get_sector_stocks(sector_name)
        if result:
            self.cache.save_universe(sector_name, date, result)
            logger.info(f"获取 {sector_name} 板块: {len(result)} 只")
        return result

    def _get_index_constituents(self, index_name: str, date: str) -> list[str]:
        cached = self.cache.get_universe(index_name, date)
        if cached:
            return cached
        result = self.client.get_sector_stocks(index_name)
        if result:
            self.cache.save_universe(index_name, date, result)
            logger.info(f"获取 {index_name} 成分股: {len(result)} 只")
        return result

    def _get_all_a_stocks(self, date: str) -> list[str]:
        cached = self.cache.get_universe("all_a", date)
        if cached:
            return cached
        # 尝试从多个板块聚合
        result: list[str] = []
        for idx_name in INDEX_SECTOR_MAP.values():
            try:
                symbols = self.client.get_sector_stocks(idx_name)
                result.extend(symbols)
            except Exception:
                continue
        unique = sorted(set(result))
        if unique:
            self.cache.save_universe("all_a", date, unique)
            logger.info(f"全A股聚合: {len(unique)} 只")
        return unique


def get_data_provider(cache: DataCache | None = None) -> MiniQmtDataProvider:
    """获取默认数据提供者。"""
    return MiniQmtDataProvider(cache)


def get_universe_provider(cache: DataCache | None = None) -> MiniQmtUniverseProvider:
    """获取默认股票池提供者。"""
    return MiniQmtUniverseProvider(cache)
