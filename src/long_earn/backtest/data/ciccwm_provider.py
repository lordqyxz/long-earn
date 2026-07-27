"""中金财富 (ciccwm) 数据提供者。

实现 :class:`MarketIntelligenceProvider`（ciccwm 独占的扩展能力：
资金流向 / 涨跌幅排行 / 关联板块 / 热榜资讯），并保留行情降级能力。

定位（ADR-006 + ADR-007 Phase 3）::

    行情/财务：已统一到 miniqmt（DuckDB 缓存 → miniqmt），ciccwm 降级分支已屏蔽
    独占数据（资金流向/排行/板块/资讯）：ciccwm 独占，无降级，失败显式报错

符号格式转换在 provider 边界完成：
  - long-earn 内部用 xtquant 格式 ``600519.SH`` / ``000001.SZ``
  - ciccwm 用 ``code`` + ``market`` 数值（0=深 / 1=沪 / 2=北 / 31=港 / 74=美股）
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import polars as pl
from loguru import logger

from long_earn.backtest.data import ciccwm_client as client
from long_earn.backtest.data.cache import DataCache
from long_earn.backtest.data.polars_adapter import to_polars_panel
from long_earn.backtest.data.provider import MarketIntelligenceProvider
from long_earn.backtest.data.symbol import xt_to_ciccwm

# ── 字段映射常量 ───────────────────────────────────────────────────────────

# 标准行情字段
DEFAULT_PRICE_FIELDS = ["open", "high", "low", "close", "volume"]

# 排行最大条数（ciccwm 接口硬限制）
RANKING_MAX_LIMIT = 80

# 历史行情默认天数
HISTORY_DEFAULT_DAYS = 5


class CiccwmDataProvider(MarketIntelligenceProvider):
    """中金财富 (ciccwm) 数据提供者。

    实现 :class:`MarketIntelligenceProvider`（资金流向/排行/板块/资讯），
    ciccwm 独占，无降级，失败显式报错（ADR-006）。

    保留行情降级能力（get_price_panel），但财务数据已统一到 miniqmt
    （ADR-007 Phase 3），不再实现 get_financial_panel。

    纯 HTTP 实现，不依赖本地 miniQMT 客户端。
    获取的行情数据自动写入 DuckDB 缓存，后续查询可直接走缓存。
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
                code, market = xt_to_ciccwm(symbol)
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

    # ── DataProvider Protocol: 合并面板 ──────────────────────────────────

    def get_merged_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        price_fields: list[str] | None = None,
        financial_fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """获取合并面板（行情 + 财务）。

        财务数据已统一到 miniqmt（ADR-007 Phase 3），本 provider 仅返回行情面板。
        financial_fields 参数保留用于接口兼容，但不再获取财务数据。
        """
        return self.get_price_panel(symbols, start_date, end_date, price_fields)

    def get_merged_panel_as_polars(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> pl.DataFrame:
        """获取合并面板并转为 polars（实现 DataProvider Protocol）。"""
        df = self.get_merged_panel(symbols, start_date, end_date)
        return to_polars_panel(df)

    # ── DataConnector 扩展能力桩（ciccwm 不支持，返回空数据） ──────────
    # ADR-014 阶段 F：miniqmt 全能力接入后，DataConnector 新增 6 个方法，
    # ciccwm 不具备这些能力（行业指数/成分股/板块分类/交易日历/标的基础
    # 信息/实时快照），返回空数据并 logger.warning 提示调用方走降级链。

    def get_industry_index_panel(
        self,
        industry: str,
        start_date: str,
        end_date: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """ciccwm 不支持行业指数 K 线。返回空 DataFrame。"""
        logger.debug("ciccwm 不支持 get_industry_index_panel，返回空")
        return pd.DataFrame()

    def get_industry_constituents(self, industry: str) -> list[str]:
        """ciccwm 不支持行业成分股。返回空列表。"""
        logger.debug("ciccwm 不支持 get_industry_constituents，返回空")
        return []

    def get_sector_classifications(self) -> list[str]:
        """ciccwm 不支持板块分类树。返回空列表。"""
        logger.debug("ciccwm 不支持 get_sector_classifications，返回空")
        return []

    def get_trading_dates(
        self,
        start_date: str = "",
        end_date: str = "",
        market: str = "SSE",
    ) -> list[str]:
        """ciccwm 不支持交易日历。返回空列表。"""
        logger.debug("ciccwm 不支持 get_trading_dates，返回空")
        return []

    def get_instrument_detail(self, stock_code: str) -> dict[str, Any]:
        """ciccwm 不支持标的基础信息。返回空 dict。"""
        logger.debug("ciccwm 不支持 get_instrument_detail，返回空")
        return {}

    def get_full_tick(self, code_list: list[str]) -> dict[str, Any]:
        """ciccwm 不支持实时快照。返回空 dict。"""
        logger.debug("ciccwm 不支持 get_full_tick，返回空")
        return {}

    # ── MarketIntelligenceProvider 接口实现（ciccwm 独占，无降级链） ──────

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
            code, market = xt_to_ciccwm(symbol)
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
            code, market = xt_to_ciccwm(symbol)
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
