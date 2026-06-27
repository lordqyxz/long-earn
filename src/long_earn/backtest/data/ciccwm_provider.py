"""中金财富 (ciccwm) 数据提供者。

实现 ``DataProvider`` Protocol（行情/财务），并提供 ciccwm 独占的扩展能力
（资金流向 / 涨跌幅排行 / 关联板块 / 热榜资讯）。

降级链定位（ADR-006）::

    DuckDB 缓存 → miniqmt → ciccwm → akshare

ciccwm 紧跟 miniqmt，优先于 akshare：
  - **共享数据**（行情/财务）：miniqmt 不可用时 ciccwm 接管，字段口径比 akshare 稳定。
  - **独占数据**（资金流向/排行/板块/资讯）：miniqmt 与 akshare 均无对应能力，
    失败时显式报错或返回空，不静默吞错。

符号格式转换在 provider 边界完成：
  - long-earn 内部用 xtquant 格式 ``600519.SH`` / ``000001.SZ``
  - ciccwm 用 ``code`` + ``market`` 数值（0=深 / 1=沪 / 2=北 / 31=港 / 74=美股）
"""

from __future__ import annotations

import re
from typing import Any

import pandas as pd
from loguru import logger

from long_earn.backtest.data import ciccwm_client as client
from long_earn.backtest.data.cache import DataCache

# ── 符号格式转换 ─────────────────────────────────────────────────────────

# xtquant 格式：600519.SH / 000001.SZ / 600519.BJ / 00700.HK
_XT_SYMBOL_RE = re.compile(r"^(\d{4,6})\.([A-Z]+)$")

# 后缀 → ciccwm market 数值
_SUFFIX_TO_MARKET: dict[str, int] = {
    "SZ": client.MARKET_SHENZHEN,
    "SH": client.MARKET_SHANGHAI,
    "BJ": client.MARKET_BSE,
    "HK": client.MARKET_HK,
    "US": client.MARKET_US,
}

# market 数值 → 后缀（反向转换）
_MARKET_TO_SUFFIX: dict[int, str] = {v: k for k, v in _SUFFIX_TO_MARKET.items()}

# 标准行情字段
DEFAULT_PRICE_FIELDS = ["open", "high", "low", "close", "volume"]

# 财务字段映射：ciccwm 财务指标字段 → 标准字段名（与 miniqmt FINANCIAL_FIELD_MAP 对齐）
# ciccwm 财务返回的字段名是中文缩写，这里映射到 long-earn 标准字段
CICCWM_FINANCIAL_FIELD_MAP: dict[str, str] = {
    "净利润": "net_profit",
    "营业收入": "revenue",
    "净资产收益率": "roe",
    "每股收益": "eps",
    "毛利率": "gross_margin",
    "净利润同比增长": "net_profit_yoy",
    "营业收入同比增长": "revenue_yoy",
}

# 财务默认披露延迟（天）：与 miniqmt provider 保持一致，避免未来函数
DEFAULT_PUBLICATION_LAG_DAYS = 60

# 排行最大条数（ciccwm 接口硬限制）
RANKING_MAX_LIMIT = 80

# 历史行情默认天数
HISTORY_DEFAULT_DAYS = 5


def _xt_to_ciccwm(symbol: str) -> tuple[str, int]:
    """将 xtquant 格式代码转为 ciccwm (code, market)。

    Args:
        symbol: xtquant 格式，如 ``600519.SH`` / ``000001.SZ``

    Returns:
        (code, market) 元组，如 ``("600519", 1)``

    Raises:
        ValueError: 无法识别的代码格式
    """
    m = _XT_SYMBOL_RE.match(symbol)
    if not m:
        raise ValueError(f"无法解析的代码格式: {symbol}")
    code = m.group(1)
    suffix = m.group(2)
    market = _SUFFIX_TO_MARKET.get(suffix)
    if market is None:
        raise ValueError(f"未知市场后缀: {suffix} (symbol={symbol})")
    return code, market


def _ciccwm_to_xt(code: str, market: int) -> str:
    """将 ciccwm (code, market) 转为 xtquant 格式。"""
    suffix = _MARKET_TO_SUFFIX.get(market)
    if suffix is None:
        raise ValueError(f"未知市场代码: {market}")
    return f"{code}.{suffix}"


class CiccwmDataProvider:
    """中金财富 (ciccwm) 数据提供者。

    实现 ``DataProvider`` Protocol（行情/财务），并额外提供 ciccwm 独占能力方法。
    纯 HTTP 实现，不依赖本地 miniQMT 客户端。

    获取的行情/财务数据自动写入 DuckDB 缓存，后续查询可直接走缓存。
    """

    def __init__(self, cache: DataCache | None = None) -> None:
        self.cache = cache or DataCache()
        self._available: bool | None = None

    @property
    def is_available(self) -> bool:
        """检测 ciccwm 是否可用（凭证文件存在且 API Key 非空）。"""
        if self._available is not None:
            return self._available
        available = client.is_credential_available()
        if not available:
            logger.warning("ciccwm 不可用：凭证文件缺失或 API Key 为空")
        self._available = available
        return available

    # ── DataProvider Protocol: 行情面板 ──────────────────────────────────

    def get_price_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """获取行情数据面板。

        逐 symbol 调 ``fetch_history``，按日期区间切片，转 ``(date, symbol)`` MultiIndex。
        获取后自动写入 DuckDB 缓存。

        Args:
            symbols: 股票代码列表（xtquant 格式，如 600519.SH）
            start_date: 起始日期（YYYY-MM-DD）
            end_date: 结束日期（YYYY-MM-DD）
            fields: 需要的字段列表，默认 open/high/low/close/volume

        Returns:
            DataFrame，index 为 (date, symbol)，列为 fields
        """
        if not symbols or not self.is_available:
            return pd.DataFrame()

        fields = fields or DEFAULT_PRICE_FIELDS
        start = pd.to_datetime(start_date)
        end = pd.to_datetime(end_date)
        # ciccwm 历史接口按天数拉取，需要覆盖整个日期范围
        # 多拉一些天数确保覆盖（交易日约占总天数的 70%）
        days_needed = max(HISTORY_DEFAULT_DAYS, (end - start).days + 30)

        all_dfs: list[pd.DataFrame] = []
        for symbol in symbols:
            try:
                code, market = _xt_to_ciccwm(symbol)
            except ValueError as e:
                logger.warning(f"跳过无法解析的代码 {symbol}: {e}")
                continue

            try:
                result = client.fetch_history(code, market, days=days_needed)
            except client.CICCWMCredentialError:
                raise
            except Exception as e:
                logger.warning(f"ciccwm 获取 {symbol} 历史行情失败: {e}")
                continue

            items = result.get("items", [])
            if not items:
                continue

            df = pd.DataFrame(items)
            if "date" not in df.columns:
                continue

            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["date"])
            # 切片到请求的日期范围
            df = df[(df["date"] >= start) & (df["date"] <= end)]
            if df.empty:
                continue

            df["symbol"] = symbol
            all_dfs.append(df)

        if not all_dfs:
            return pd.DataFrame()

        result_df = pd.concat(all_dfs, ignore_index=True)

        # 写入 DuckDB 缓存
        if not result_df.empty:
            cache_cols = ["symbol", "date"] + [
                c for c in ["open", "high", "low", "close", "volume"]
                if c in result_df.columns
            ]
            self.cache.save_prices(result_df[cache_cols])
            logger.info(
                f"[ciccwm] 获取 {len(result_df)} 条行情，"
                f"{result_df['symbol'].nunique()} 只股票，已写入缓存"
            )

        result_df = result_df.set_index(["date", "symbol"]).sort_index()
        available_fields = [f for f in fields if f in result_df.columns]
        return result_df[available_fields]

    # ── DataProvider Protocol: 财务面板 ──────────────────────────────────

    def get_financial_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """获取财务数据面板（前向填充到日级）。

        逐 symbol 调 ``query_finance("indicators", code)`` 获取主要指标，
        按报告期前向填充到日级（使用披露日 + 60 天延迟，避免未来函数）。

        Args:
            symbols: 股票代码列表
            start_date: 起始日期
            end_date: 结束日期
            fields: 需要的财务字段列表

        Returns:
            DataFrame，index 为 (date, symbol)
        """
        if not symbols or not self.is_available:
            return pd.DataFrame()

        standard_fields = fields or list(CICCWM_FINANCIAL_FIELD_MAP.values())
        all_dfs: list[pd.DataFrame] = []

        for symbol in symbols:
            try:
                code, _market = _xt_to_ciccwm(symbol)
            except ValueError as e:
                logger.warning(f"跳过无法解析的代码 {symbol}: {e}")
                continue

            # ciccwm 财务接口用纯数字代码（不含市场后缀）
            try:
                result = client.query_finance(
                    "indicators", code, qtime="12", gtype="0", limit=10
                )
            except client.CICCWMCredentialError:
                raise
            except Exception as e:
                logger.warning(f"ciccwm 获取 {symbol} 财务数据失败: {e}")
                continue

            items = result.get("items", [])
            if not items:
                continue

            records = self._normalize_finance_items(items, symbol)
            if records:
                all_dfs.append(pd.DataFrame(records))

        if not all_dfs:
            return pd.DataFrame()

        quarterly_df = pd.concat(all_dfs, ignore_index=True)

        # 写入 DuckDB 缓存
        if not quarterly_df.empty and "report_date" in quarterly_df.columns:
            self.cache.save_financials(quarterly_df)
            logger.info(
                f"[ciccwm] 获取 {len(quarterly_df)} 条财务数据，"
                f"{quarterly_df['symbol'].nunique()} 只股票，已写入缓存"
            )

        # 前向填充到日级（使用披露日 + 延迟，避免未来函数）
        trading_dates = pd.date_range(start=start_date, end=end_date, freq="B")
        return self._quarterly_to_daily(
            quarterly_df, symbols, trading_dates, standard_fields
        )

    def _normalize_finance_items(
        self,
        items: list[dict[str, Any]],
        symbol: str,
    ) -> list[dict[str, Any]]:
        """将 ciccwm 财务记录标准化为缓存兼容格式。

        ciccwm 财务接口返回的字段名是中文缩写，需要映射到标准字段名。
        报告期日期字段名不统一（可能是 ``报告期`` / ``截止日期`` / ``date`` 等）。
        """
        records: list[dict[str, Any]] = []
        for item in items:
            record: dict[str, Any] = {"symbol": symbol}

            # 提取报告期日期
            report_date = None
            for key in ("报告期", "截止日期", "date", "报告日期", "REPORT_DATE"):
                if item.get(key):
                    report_date = item[key]
                    break
            if report_date is None:
                continue
            record["report_date"] = pd.to_datetime(report_date, errors="coerce")

            # 映射财务字段
            for cn_field, std_field in CICCWM_FINANCIAL_FIELD_MAP.items():
                if cn_field in item and item[cn_field] is not None:
                    try:
                        record[std_field] = float(item[cn_field])
                    except (ValueError, TypeError):
                        record[std_field] = None

            records.append(record)

        # 过滤掉 report_date 为空的记录
        return [r for r in records if pd.notna(r.get("report_date"))]

    def _quarterly_to_daily(
        self,
        quarterly_df: pd.DataFrame,
        symbols: list[str],
        trading_dates: pd.DatetimeIndex,
        fields: list[str],
        publication_lag_days: int = DEFAULT_PUBLICATION_LAG_DAYS,
    ) -> pd.DataFrame:
        """将季度财务数据前向填充到日级。

        使用"披露日"而非"报告期截止日"作为可见日期，避免未来函数。
        与 ``MiniQmtDataProvider._quarterly_to_daily`` 逻辑一致。
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
        return pd.concat(panels)

    # ── DataProvider Protocol: 合并面板 ──────────────────────────────────

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
        # 统一 index names
        if price_df.index.names != fin_df.index.names:
            fin_df.index.names = price_df.index.names
        merged = price_df.join(fin_df, how="outer")
        merged = merged.groupby(level="symbol").ffill()
        return merged.sort_index()

    # ── 扩展能力（ciccwm 独占，不进 DataProvider Protocol） ────────────────

    def get_fund_flow(self, symbol: str) -> pd.DataFrame:
        """获取个股资金流向（当日）。

        ciccwm 独占能力 —— miniqmt 与 akshare 均无对应能力。

        Args:
            symbol: xtquant 格式代码，如 600519.SH

        Returns:
            资金流向 DataFrame；失败时返回空 DataFrame 并记录警告
        """
        if not self.is_available:
            logger.warning("ciccwm 不可用，资金流向数据无替代源")
            return pd.DataFrame()

        try:
            code, market = _xt_to_ciccwm(symbol)
        except ValueError as e:
            logger.warning(f"无法解析代码 {symbol}: {e}")
            return pd.DataFrame()

        try:
            result = client.fetch_fund_flow(code, market)
        except client.CICCWMCredentialError:
            raise
        except Exception as e:
            logger.warning(f"ciccwm 获取 {symbol} 资金流向失败: {e}")
            return pd.DataFrame()

        items = result.get("ListItem", [])
        if not items:
            return pd.DataFrame()

        records = client.list_items_to_records(result)
        if not records:
            return pd.DataFrame()
        return pd.DataFrame(records)

    def get_ranking(
        self,
        market: int = 6,
        limit: int = 10,
        sort_type: int = 1,
    ) -> pd.DataFrame:
        """获取涨跌幅排行。

        ciccwm 独占能力 —— miniqmt 与 akshare 均无对应能力。

        Args:
            market: 市场/板块代码（6=沪深A股，14=创业板，等）
            limit: 返回条数，**最大 80**
            sort_type: 1=涨幅倒序，0=跌幅正序

        Returns:
            排行 DataFrame；失败时返回空 DataFrame
        """
        if not self.is_available:
            logger.warning("ciccwm 不可用，涨跌幅排行无替代源")
            return pd.DataFrame()

        limit = min(limit, RANKING_MAX_LIMIT)

        try:
            result = client.fetch_ranking(market, limit, sort_type)
        except client.CICCWMCredentialError:
            raise
        except Exception as e:
            logger.warning(f"ciccwm 获取排行失败: {e}")
            return pd.DataFrame()

        items = result.get("items", [])
        if not items:
            return pd.DataFrame()
        return pd.DataFrame(items)

    def get_related_blocks(self, symbol: str) -> list[dict[str, Any]]:
        """获取个股关联板块。

        ciccwm 独占能力 —— miniqmt 与 akshare 均无对应能力。

        Args:
            symbol: xtquant 格式代码，如 600519.SH

        Returns:
            关联板块信息列表；失败时返回空列表
        """
        if not self.is_available:
            logger.warning("ciccwm 不可用，关联板块无替代源")
            return []

        try:
            code, market = _xt_to_ciccwm(symbol)
        except ValueError as e:
            logger.warning(f"无法解析代码 {symbol}: {e}")
            return []

        try:
            result = client.fetch_related_blocks(code, market)
        except client.CICCWMCredentialError:
            raise
        except Exception as e:
            logger.warning(f"ciccwm 获取 {symbol} 关联板块失败: {e}")
            return []

        # 关联板块的响应结构是 BlockInfo 列表
        blocks = result.get("BlockInfo", [])
        if isinstance(blocks, list):
            return blocks
        return []

    def get_hot_rank(
        self,
        page_size: int = 10,
        page_num: int = 1,
        news_type: int = 1,
    ) -> pd.DataFrame:
        """获取今日热榜资讯。

        ciccwm 独占能力 —— miniqmt 与 akshare 均无对应能力。

        Args:
            page_size: 每页数量，默认 10
            page_num: 页码，默认 1
            news_type: 资讯类型，默认 1

        Returns:
            热榜 DataFrame，含 redirect_url 列；失败时返回空 DataFrame
        """
        if not self.is_available:
            logger.warning("ciccwm 不可用，热榜资讯无替代源")
            return pd.DataFrame()

        try:
            result = client.query_hot_rank(page_num, page_size, news_type)
        except client.CICCWMCredentialError:
            raise
        except Exception as e:
            logger.warning(f"ciccwm 获取热榜失败: {e}")
            return pd.DataFrame()

        data = result.get("data", [])
        if not isinstance(data, list) or not data:
            return pd.DataFrame()
        return pd.DataFrame(data)

    def get_topic_news(
        self,
        spec_subject_id: int | None = None,
        page_size: int = 20,
        page_num: int = 1,
        news_type: int = 1,
    ) -> pd.DataFrame:
        """获取专题资讯列表。

        ciccwm 独占能力 —— miniqmt 与 akshare 均无对应能力。

        Args:
            spec_subject_id: 专题 ID，None 表示查询全部专题
            page_size: 每页数量，默认 20
            page_num: 页码，默认 1
            news_type: 资讯类型，默认 1

        Returns:
            专题资讯 DataFrame，含 redirect_url 列；失败时返回空 DataFrame
        """
        if not self.is_available:
            logger.warning("ciccwm 不可用，专题资讯无替代源")
            return pd.DataFrame()

        try:
            result = client.query_topic_info(
                spec_subject_id, page_num, page_size, news_type
            )
        except client.CICCWMCredentialError:
            raise
        except Exception as e:
            logger.warning(f"ciccwm 获取专题资讯失败: {e}")
            return pd.DataFrame()

        data = result.get("data", [])
        if not isinstance(data, list) or not data:
            return pd.DataFrame()
        return pd.DataFrame(data)
