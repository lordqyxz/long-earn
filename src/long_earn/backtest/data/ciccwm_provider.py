"""ciccwm 财经数据提供者。

实现 DataProvider Protocol（行情历史、财务报表），并额外暴露 ciccwm 独占能力
（资金流向、涨跌幅排行、关联板块、热榜资讯）。

降级链定位：DuckDB 缓存 → miniqmt → ciccwm → akshare
- 共享数据（行情、财务）：按链降级，ciccwm 紧跟 miniqmt，严格优先于 akshare
- 独占数据（资金流向等）：仅 ciccwm 能提供，失败时显式报错而非静默降级
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

import pandas as pd

from long_earn.backtest.data import ciccwm_client as client
from long_earn.backtest.data.cache import DataCache

logger = logging.getLogger(__name__)

# 财务字段映射：ciccwm 中文/缩写字段名 → 标准字段名
_FINANCIAL_FIELD_MAP: dict[str, str] = {
    "basicEps": "eps",
    "roeWeighted": "roe",
    "roe": "roe",
    "netProfit": "net_profit",
    "operatingRevenue": "revenue",
    "netProfitYoy": "net_profit_yoy",
    "operatingRevenueYoy": "revenue_yoy",
    "grossProfitMargin": "gross_margin",
    "grossMargin": "gross_margin",
    "totalOperatingCost": "total_operating_cost",
    "totalEquity": "total_equity",
    "totalShareholdersEquity": "total_equity",
}

# 历史行情字段映射：ciccwm → 标准字段
_HISTORY_FIELD_MAP: dict[str, str] = {
    "openPrice": "open",
    "closePrice": "close",
    "highPrice": "high",
    "lowPrice": "low",
    "volume": "volume",
    "amount": "amount",
    "tradeDate": "date",
}

# 合并面板时用于定位索引列的阈值
_MIN_INDEX_LEVELS = 2

# 字段备选名（当标准映射未命中时尝试）
_FALLBACK_FIELDS = ["open", "high", "low", "close", "volume"]

# 请求日期的缓冲天数（确保覆盖完整区间）
_DATE_BUFFER_DAYS = 60


def _map_fields(
    rec: dict[str, Any],
    field_map: dict[str, str],
    target_fields: list[str],
) -> dict[str, Any]:
    """将 ciccwm 响应记录按字段映射转为标准行 dict。"""
    row: dict[str, Any] = {}
    for ciccwm_f, std_f in field_map.items():
        if std_f not in target_fields:
            continue
        val = rec.get(ciccwm_f)
        if val is not None:
            with contextlib.suppress(ValueError, TypeError):
                row[std_f] = float(val)
    return row


def _try_parse_date(date_str: str) -> pd.Timestamp | None:
    """尝试解析日期字符串，失败返回 None。"""
    try:
        return pd.to_datetime(date_str)
    except Exception:
        return None


class CiccwmDataProvider:
    """基于 ciccwm HTTP 的数据提供者。

    纯 HTTP，零本地依赖（不需要 miniQMT / xtquant），适合无终端环境。

    共享数据方法（DataProvider Protocol）：
        get_price_panel, get_financial_panel, get_merged_panel

    ciccwm 独占扩展方法（仅此 Provider 有）：
        get_fund_flow, get_ranking, get_related_blocks,
        get_hot_rank, get_topic_news
    """

    def __init__(self, cache: DataCache | None = None) -> None:
        self.cache = cache or DataCache()
        self._available: bool | None = None

    # ── 可用性检测 ──────────────────────────────────────────────────────

    @property
    def is_available(self) -> bool:
        """检测 ciccwm 是否可用。

        检查凭证文件是否存在且 CICCWM_API_KEY 非空。
        不执行网络探活（避免每次检测都发 HTTP 请求）。
        """
        if self._available is not None:
            return self._available
        try:
            client._load_api_key()
            self._available = True
        except client.CiccwmCredentialError as e:
            logger.info(f"ciccwm 不可用: {e}")
            self._available = False
        return self._available

    # ── 共享数据：行情面板（DataProvider Protocol）─────────────────────

    def _fetch_single_price(
        self,
        symbol: str,
        start_dt: pd.Timestamp,
        end_dt: pd.Timestamp,
        fields: list[str],
    ) -> pd.DataFrame | None:
        """获取单只股票的行情数据，返回 DataFrame 或 None。"""
        try:
            code, market = client._parse_symbol(symbol)
            total_days = (end_dt - start_dt).days + _DATE_BUFFER_DAYS
            days = max(5, min(total_days, 365 * 3))
            records = client.fetch_history(code, market, days=days)
            if not records:
                return None

            rows: list[dict[str, Any]] = []
            for rec in records:
                date_str = rec.get("tradeDate") or rec.get("date", "")
                if not date_str:
                    continue
                dt = _try_parse_date(date_str)
                if dt is None or dt < start_dt or dt > end_dt:
                    continue
                row: dict[str, Any] = {"date": dt, "symbol": symbol}
                row.update(_map_fields(rec, _HISTORY_FIELD_MAP, fields))
                # 备选字段名兜底
                for f in _FALLBACK_FIELDS:
                    if f in fields and f not in row and f in rec:
                        with contextlib.suppress(ValueError, TypeError):
                            row[f] = float(rec[f])
                rows.append(row)

            if not rows:
                return None
            return pd.DataFrame(rows)
        except Exception as e:
            logger.warning(f"ciccwm 获取 {symbol} 行情失败: {e}")
            return None

    def get_price_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """通过 ciccwm 获取行情数据面板。

        逐 symbol 调 fetch_history，按日期区间切片，转 (date, symbol) MultiIndex。
        获取后自动写入 DuckDB 缓存。
        """
        if not symbols or not self.is_available:
            return pd.DataFrame()

        fields = fields or ["open", "high", "low", "close", "volume"]
        start_dt = pd.to_datetime(start_date)
        end_dt = pd.to_datetime(end_date)

        all_dfs: list[pd.DataFrame] = []
        for symbol in symbols:
            df = self._fetch_single_price(symbol, start_dt, end_dt, fields)
            if df is not None:
                all_dfs.append(df)

        if not all_dfs:
            return pd.DataFrame()

        result = pd.concat(all_dfs, ignore_index=True)
        if not result.empty:
            self.cache.save_prices(result)
            logger.info(
                f"[ciccwm] 获取 {len(result)} 条行情，"
                f"{result['symbol'].nunique()} 只股票，已写入缓存"
            )

        result = result.set_index(["date", "symbol"]).sort_index()
        available_fields = [f for f in fields if f in result.columns]
        return result[available_fields]

    # ── 共享数据：财务面板（DataProvider Protocol）────────────────────

    def _fetch_single_financial(
        self, symbol: str, fields: list[str],
    ) -> pd.DataFrame | None:
        """获取单只股票的财务数据，返回 DataFrame 或 None。"""
        try:
            code, _market = client._parse_symbol(symbol)
            records = client.query_finance(
                statement="indicators", code=code, limit=20,
            )
            if not records:
                records = client.query_finance(
                    statement="income", code=code, limit=20,
                )
            if not records:
                return None

            rows: list[dict[str, Any]] = []
            for rec in records:
                date_str = rec.get("endDate") or rec.get("reportDate") or ""
                if not date_str:
                    continue
                report_date = _try_parse_date(date_str)
                if report_date is None:
                    continue
                row: dict[str, Any] = {
                    "symbol": symbol,
                    "report_date": report_date,
                }
                row.update(_map_fields(rec, _FINANCIAL_FIELD_MAP, fields))
                rows.append(row)

            if not rows:
                return None
            return pd.DataFrame(rows)
        except Exception as e:
            logger.warning(f"ciccwm 获取 {symbol} 财务数据失败: {e}")
            return None

    def get_financial_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """通过 ciccwm 获取财务数据面板。

        从 "indicators"（主要指标）表获取财务数据，前向填充到日级。
        获取后自动写入 DuckDB 缓存。
        """
        if not symbols or not self.is_available:
            return pd.DataFrame()

        fields = fields or [
            "eps", "roe", "net_profit", "revenue",
            "net_profit_yoy", "revenue_yoy", "gross_margin",
        ]

        all_dfs: list[pd.DataFrame] = []
        for symbol in symbols:
            df = self._fetch_single_financial(symbol, fields)
            if df is not None:
                all_dfs.append(df)

        if not all_dfs:
            return pd.DataFrame()

        result = pd.concat(all_dfs, ignore_index=True)
        result = result.dropna(subset=["report_date"])

        if not result.empty:
            self.cache.save_financials(result)
            logger.info(
                f"[ciccwm] 获取 {len(result)} 条财务数据，"
                f"{result['symbol'].nunique()} 只股票，已写入缓存"
            )

        trading_dates = pd.date_range(start=start_date, end=end_date, freq="B")
        panel = self._quarterly_to_daily(result, symbols, trading_dates, fields)
        return panel

    def _quarterly_to_daily(
        self,
        quarterly_df: pd.DataFrame,
        symbols: list[str],
        trading_dates: pd.DatetimeIndex,
        fields: list[str],
        publication_lag_days: int = 60,
    ) -> pd.DataFrame:
        """将季度财务数据前向填充到日级。

        与 MiniQmtDataProvider._quarterly_to_daily 逻辑相同：
        使用 publication_lag_days 作为披露窗口，防止未来函数泄漏。
        """
        publication_lag = pd.Timedelta(days=publication_lag_days)
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
                visible_from = pd.to_datetime(report_date) + publication_lag
                mask = daily.index >= visible_from
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

    # ── 共享数据：合并面板（DataProvider Protocol）─────────────────────

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
        if not isinstance(fin_df.index, pd.MultiIndex) or fin_df.index.nlevels < _MIN_INDEX_LEVELS:
            return price_df
        if price_df.index.names != fin_df.index.names:
            fin_df.index.names = price_df.index.names
        p = price_df.reset_index()
        f = fin_df.reset_index()
        idx_cols = [c for c in p.columns if c in f.columns][:2]
        if len(idx_cols) < _MIN_INDEX_LEVELS:
            return price_df
        p[idx_cols[0]] = pd.to_datetime(p[idx_cols[0]])
        f[idx_cols[0]] = pd.to_datetime(f[idx_cols[0]])
        merged = pd.merge(p, f, on=idx_cols, how="outer")
        merged = merged.set_index(idx_cols)
        merged = merged.sort_index()
        merged = merged.groupby(level=idx_cols[1]).ffill()
        return merged.sort_index()

    # ── 独占扩展方法 ──────────────────────────────────────────────────

    def get_info(self, symbol: str) -> dict[str, Any]:
        """获取个股基本信息（含最新价、涨跌幅等）。"""
        code, market = client._parse_symbol(symbol)
        return client.fetch_info(code, market) or {}

    def get_fund_flow(self, symbol: str) -> pd.DataFrame:
        """获取个股资金流向（ciccwm 独占能力）。"""
        code, market = client._parse_symbol(symbol)
        records = client.fetch_fund_flow(code, market)
        if not records:
            return pd.DataFrame()
        df = pd.DataFrame(records)
        for col in df.columns:
            if "date" in col.lower() or "time" in col.lower():
                with contextlib.suppress(Exception):
                    df[col] = pd.to_datetime(df[col])
        return df

    def get_ranking(
        self, market: int = 1, sort_type: int = 3, limit: int = 50,
    ) -> pd.DataFrame:
        """获取涨跌幅排行（ciccwm 独占能力）。"""
        records = client.fetch_ranking(market, sort_type=sort_type, limit=limit)
        if not records:
            return pd.DataFrame()
        return pd.DataFrame(records)

    def get_related_blocks(self, symbol: str) -> list[dict[str, Any]]:
        """获取个股关联板块（ciccwm 独占能力）。"""
        code, market = client._parse_symbol(symbol)
        return client.fetch_related_blocks(code, market)

    def get_hot_rank(self, page_size: int = 10) -> pd.DataFrame:
        """获取今日热榜（ciccwm 独占能力）。"""
        records = client.query_hot_rank(page_size=page_size)
        if not records:
            return pd.DataFrame()
        return pd.DataFrame(records)

    def get_topic_news(self, subject_id: int | None = None) -> pd.DataFrame:
        """获取专题资讯列表（ciccwm 独占能力）。"""
        records = client.query_topic_info(subject_id=subject_id)
        if not records:
            return pd.DataFrame()
        return pd.DataFrame(records)
