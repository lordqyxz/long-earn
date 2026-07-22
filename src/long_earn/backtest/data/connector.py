"""数据连接器统一接口 — ADR-014 阶段 F（替代 DataProvider）。

设计哲学：
- **miniqmt 全能力统一**：行情/财务/universe/行业指数/行业成分股/板块树/
  交易日历/标的基础信息/实时快照 9 类数据能力聚于单一 Protocol
- **契合本体论顶层架构**：与 :class:`ontology.connector.ConnectorDataProvider`
  对齐，让概念查询直接分发到 connector 方法（行业指数/行业成分股等）
- **保留 DataProvider 引擎消费契约**：``get_merged_panel_as_polars`` 接口
  不变，引擎层零改动可运行
- **降级链编排**：:class:`CompositeDataConnector` 替代
  :class:`CompositeDataProvider`，主源失败 → fallback

向后兼容：
- ``DataProvider = DataConnector`` 别名（避免 40 文件激进改名）
- ``CompositeDataProvider = CompositeDataConnector`` 别名
- ``create_data_provider = create_data_connector`` 别名

迁移路径：消费方逐步改用 ``data_connector`` 字段名 + ``DataConnector`` 类型，
旧 ``data_provider`` 字段名通过 :class:`RuntimeContext` 兼容属性保留。
"""

from __future__ import annotations

from typing import Any, Protocol

import pandas as pd
import polars as pl
from loguru import logger

from long_earn.backtest.data.cache import DataCache
from long_earn.backtest.data.polars_adapter import to_polars_panel


class DataConnector(Protocol):
    """数据连接器统一接口 — miniqmt 全能力 + DataProvider 引擎契约。

    设计原则：
    1. **单一接口承载多数据能力**：不再为每类数据（行业/资产类别）单独
       定义 Protocol，避免接口爆炸
    2. **结构化子类型**：所有实现者共享同一 Protocol，调用方按需调用方法
    3. **PIT 对齐**：财务数据由实现者保证 ``announce_date`` 裁剪
    4. **降级链编排**：``CompositeDataConnector`` 编排多源降级
    """

    @property
    def is_available(self) -> bool:
        """数据源是否可用。"""
        ...

    # ── 行情面板（K 线，已接入 miniqmt）─────────────────────────────────

    def get_price_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """获取行情数据面板。

        Args:
            symbols: 股票代码列表（xtquant 格式，如 600519.SH）
            start_date: 起始日期（YYYY-MM-DD）
            end_date: 结束日期（YYYY-MM-DD）
            fields: 需要的字段列表，默认 open/high/low/close/volume

        Returns:
            DataFrame，index 为 (date, symbol)，列为 fields
        """
        ...

    # ── 财务面板（PIT 对齐，已接入 miniqmt 五表）───────────────────────

    def get_financial_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """获取财务数据面板（前向填充到日级，PIT 对齐）。

        Args:
            symbols: 股票代码列表
            start_date: 起始日期
            end_date: 结束日期
            fields: 需要的财务字段列表

        Returns:
            DataFrame，index 为 (date, symbol)
        """
        ...

    # ── 合并面板（行情 + 财务）─────────────────────────────────────────

    def get_merged_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        price_fields: list[str] | None = None,
        financial_fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """获取合并面板（行情 + 财务）。

        Returns:
            DataFrame，index 为 (date, symbol)，行情+财务列
        """
        ...

    def get_merged_panel_as_polars(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> pl.DataFrame:
        """获取合并面板并转为 polars DataFrame（引擎消费接口）。

        Returns:
            polars DataFrame，含 timestamp / symbol / close 等列；空数据返回空 DataFrame
        """
        ...

    # ── Universe 成分股（已接入 miniqmt 板块/指数接口）─────────────────

    def get_symbols(self, universe_type: str, date: str = "") -> list[str]:
        """获取股票池（指数成分股 / 板块成分股 / 全A 聚合）。

        Args:
            universe_type: universe 类型标识
                - 指数：``csi300`` / ``csi500`` / ``sse50`` / ``csi1000``
                - 板块：``main_board`` / ``gem`` / ``star_board`` / ``bse`` / ``szse_main``
                - 聚合：``main_board+gem``（沪深主板+创业板，默认 universe）
                - 全集：``all_a`` / ``全A股`` / ``etf``
            date: PIT 日期（YYYY-MM-DD），空字符串取最新

        Returns:
            成分股 xt_symbol 列表
        """
        ...

    # ── DataConnector 扩展能力（miniqmt 全能力接入）────────────────────

    def get_industry_index_panel(
        self,
        industry: str,
        start_date: str,
        end_date: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """获取行业指数 K 线面板。

        miniqmt 行业指数代码（如 "BK0428.SZ" 半导体 / "BK0475.SH" 白酒）
        通过 ``get_market_data_ex`` 查询，与股票 K 线格式一致。

        Args:
            industry: 行业指数代码或板块名
            start_date: YYYY-MM-DD 起始日
            end_date: YYYY-MM-DD 结束日
            fields: 字段列表，默认 open/high/low/close/volume

        Returns:
            DataFrame，index 为 (date, symbol)，列为 fields
        """
        ...

    def get_industry_constituents(self, industry: str) -> list[str]:
        """获取行业成分股列表。

        Args:
            industry: 行业标识（板块名 / 行业代码 / 行业指数代码）

        Returns:
            成分股 xt_symbol 列表
        """
        ...

    def get_sector_classifications(self) -> list[str]:
        """获取 miniqmt 支持的全部板块分类名。

        Returns:
            板块名列表（含行业板块、概念板块、指数板块）
        """
        ...

    def get_trading_dates(
        self,
        start_date: str = "",
        end_date: str = "",
        market: str = "SSE",
    ) -> list[str]:
        """获取交易日历。

        Args:
            start_date: YYYY-MM-DD 起始日
            end_date: YYYY-MM-DD 结束日
            market: 市场标识（SSE / SZSE），默认 SSE

        Returns:
            交易日列表（YYYY-MM-DD 字符串）
        """
        ...

    def get_instrument_detail(self, stock_code: str) -> dict[str, Any]:
        """获取标的基础信息。

        Args:
            stock_code: xt_symbol（如 "600519.SH"）

        Returns:
            原始字段字典（含名称/上市日期/总股本/流通股本/行业/板块等）
        """
        ...

    def get_full_tick(self, code_list: list[str]) -> dict[str, Any]:
        """获取最新逐笔行情（实时快照）。

        Args:
            code_list: xt_symbol 列表

        Returns:
            ``{symbol: tick_dict}`` 字典
        """
        ...


class CompositeDataConnector:
    """组合数据连接器：DuckDB 缓存 → miniqmt（当前阶段唯一生效路径）。

    替代 :class:`CompositeDataProvider`，新增行业指数/行业成分股/板块树/
    交易日历/标的基础信息/实时快照 6 类方法的降级编排。

    设计：
    1. 优先从 DuckDB 缓存读取
    2. 缓存缺失/过期时，尝试 miniqmt 增量更新
    3. 每次从远程获取的数据自动写入 DuckDB 缓存

    注：ciccwm / akshare 降级分支已暂时屏蔽，本阶段聚焦 miniqmt。
    ciccwm 的情报能力（MarketIntelligenceProvider）不受影响，
    通过 ``context.market_intelligence`` 独立获取。
    """

    def __init__(
        self,
        cache: DataCache | None = None,
        *,
        miniqmt_provider: DataConnector | None = None,
    ) -> None:
        self.cache = cache or DataCache()
        # 显式注入的 miniqmt 后端
        self._injected_miniqmt: DataConnector | None = miniqmt_provider
        self._miniqmt: DataConnector | None = miniqmt_provider
        self._miniqmt_available: bool | None = None

    @staticmethod
    def _normalize_date(date_str: str) -> str:
        """标准化日期格式为 YYYY-MM-DD（DuckDB 缓存要求）。"""
        if not date_str:
            return date_str
        if len(date_str) == 8 and date_str.isdigit():
            return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        return date_str

    @property
    def miniqmt(self) -> DataConnector | None:
        """延迟加载 miniqmt 提供者（已注入则直接返回）。"""
        if self._miniqmt is not None:
            return self._miniqmt
        try:
            from long_earn.backtest.data.miniqmt_provider import (
                MiniQmtDataProvider,
            )

            self._miniqmt = MiniQmtDataProvider(self.cache)
            return self._miniqmt
        except Exception as e:
            logger.warning(f"miniqmt 提供者加载失败: {e}")
            return None

    @property
    def miniqmt_available(self) -> bool:
        """检测 miniqmt 是否可用。"""
        if self._miniqmt_available is not None:
            return self._miniqmt_available
        if self._injected_miniqmt is not None:
            try:
                self._miniqmt_available = self._injected_miniqmt.is_available
            except Exception:
                self._miniqmt_available = False
            return self._miniqmt_available
        try:
            from long_earn.backtest.data.miniqmt_provider import MiniQmtClient

            self._miniqmt_available = MiniQmtClient.get().is_available
        except Exception:
            self._miniqmt_available = False
        return self._miniqmt_available

    def _log_source(self, source: str) -> None:
        """记录数据来源。"""
        logger.info(f"[数据来源: {source}]")

    # ── 引擎消费契约（行情/财务/合并面板）──────────────────────────────

    def get_price_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """获取行情数据面板。"""
        if not symbols:
            return pd.DataFrame()
        start_date = self._normalize_date(start_date)
        end_date = self._normalize_date(end_date)
        mq = self.miniqmt
        if mq is not None:
            df = mq.get_price_panel(symbols, start_date, end_date, fields)
            if not df.empty:
                self._log_source("miniqmt（含 DuckDB 缓存优先）")
                return df
        logger.warning("miniqmt 路径无数据，行情获取失败")
        return pd.DataFrame()

    def get_financial_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """获取财务数据面板。"""
        if not symbols:
            return pd.DataFrame()
        start_date = self._normalize_date(start_date)
        end_date = self._normalize_date(end_date)
        mq = self.miniqmt
        if mq is not None:
            df = mq.get_financial_panel(symbols, start_date, end_date, fields)
            if not df.empty:
                self._log_source("miniqmt（含 DuckDB 缓存优先）")
                return df
        logger.warning("miniqmt 路径无数据，财务数据获取失败")
        return pd.DataFrame()

    def get_symbols(self, universe_type: str, date: str = "") -> list[str]:
        """获取股票池。"""
        mq = self.miniqmt
        if mq is not None:
            symbols = self._try_get_symbols(mq, universe_type, date)
            if symbols:
                self._log_source("miniqmt universe（含 DuckDB 缓存优先）")
                return symbols
        logger.warning(f"miniqmt 路径无数据，股票池 '{universe_type}' 获取失败")
        return []

    @staticmethod
    def _try_get_symbols(
        provider: DataConnector, universe_type: str, date: str
    ) -> list[str]:
        """安全调用 provider 的 get_symbols。"""
        try:
            fn = getattr(provider, "get_symbols", None)
            if fn is None:
                return []
            return list(fn(universe_type, date) or [])
        except Exception as e:
            logger.warning(f"{type(provider).__name__}.get_symbols 失败: {e}")
            return []

    def get_merged_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        price_fields: list[str] | None = None,
        financial_fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """获取合并面板（行情 + 财务，自动降级）。"""
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
        if not isinstance(fin_df.index, pd.MultiIndex) or fin_df.index.nlevels < 2:
            return price_df
        if price_df.index.names != fin_df.index.names:
            fin_df.index.names = price_df.index.names
        p = price_df.reset_index()
        f = fin_df.reset_index()
        idx_cols = [c for c in p.columns if c in f.columns][:2]
        if len(idx_cols) < 2:
            return price_df
        p[idx_cols[0]] = pd.to_datetime(p[idx_cols[0]])
        f[idx_cols[0]] = pd.to_datetime(f[idx_cols[0]])
        merged = pd.merge(p, f, on=idx_cols, how="outer")
        merged = merged.set_index(idx_cols)
        # 关键：ffill 前必须按 (date, symbol) 升序排序，否则 outer merge 后行序混乱，
        # groupby.ffill 会用"原始行序"填充——可能拿未来值填到过去，构成数据层
        # 未来函数泄漏点。
        merged = merged.sort_index()
        merged = merged.groupby(level=idx_cols[1]).ffill()
        return merged.sort_index()

    def get_merged_panel_as_polars(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> pl.DataFrame:
        """获取合并面板并转为 polars（实现 DataConnector Protocol）。"""
        df = self.get_merged_panel(symbols, start_date, end_date)
        return to_polars_panel(df)

    # ── DataConnector 扩展能力降级编排 ─────────────────────────────────

    def get_industry_index_panel(
        self,
        industry: str,
        start_date: str,
        end_date: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """获取行业指数 K 线面板。"""
        if not industry:
            return pd.DataFrame()
        mq = self.miniqmt
        if mq is not None:
            df = mq.get_industry_index_panel(industry, start_date, end_date, fields)
            if df is not None and not df.empty:
                self._log_source("miniqmt industry_index_panel")
                return df
        logger.warning(f"miniqmt 行业指数 {industry} 无数据")
        return pd.DataFrame()

    def get_industry_constituents(self, industry: str) -> list[str]:
        """获取行业成分股列表。"""
        if not industry:
            return []
        mq = self.miniqmt
        if mq is not None:
            try:
                symbols = list(mq.get_industry_constituents(industry) or [])
                if symbols:
                    self._log_source(f"miniqmt industry_constituents:{industry}")
                    return symbols
            except Exception as e:
                logger.warning(f"get_industry_constituents({industry}) 失败: {e}")
        logger.warning(f"miniqmt 行业成分股 {industry} 无数据")
        return []

    def get_sector_classifications(self) -> list[str]:
        """获取 miniqmt 全部板块分类名。"""
        mq = self.miniqmt
        if mq is not None:
            try:
                sectors = list(mq.get_sector_classifications() or [])
                if sectors:
                    self._log_source("miniqmt sector_classifications")
                    return sectors
            except Exception as e:
                logger.warning(f"get_sector_classifications 失败: {e}")
        return []

    def get_trading_dates(
        self,
        start_date: str = "",
        end_date: str = "",
        market: str = "SSE",
    ) -> list[str]:
        """获取交易日历。"""
        mq = self.miniqmt
        if mq is not None:
            try:
                dates = list(mq.get_trading_dates(start_date, end_date, market) or [])
                if dates:
                    self._log_source("miniqmt trading_dates")
                    return dates
            except Exception as e:
                logger.warning(f"get_trading_dates 失败: {e}")
        return []

    def get_instrument_detail(self, stock_code: str) -> dict[str, Any]:
        """获取标的基础信息。"""
        mq = self.miniqmt
        if mq is not None:
            try:
                detail = mq.get_instrument_detail(stock_code) or {}
                if detail:
                    self._log_source(f"miniqmt instrument_detail:{stock_code}")
                    return detail
            except Exception as e:
                logger.warning(f"get_instrument_detail({stock_code}) 失败: {e}")
        return {}

    def get_full_tick(self, code_list: list[str]) -> dict[str, Any]:
        """获取最新逐笔行情（实时快照）。"""
        mq = self.miniqmt
        if mq is not None:
            try:
                tick = mq.get_full_tick(code_list) or {}
                if tick:
                    self._log_source("miniqmt full_tick")
                    return tick
            except Exception as e:
                logger.warning(f"get_full_tick 失败: {e}")
        return {}


def create_data_connector(
    cache: DataCache | None = None,
    *,
    miniqmt_provider: DataConnector | None = None,
) -> CompositeDataConnector:
    """工厂函数：创建组合数据连接器。

    Args:
        cache: DuckDB 缓存实例，默认自动创建
        miniqmt_provider: 可选的 miniqmt 后端连接器。不传则延迟加载本地
            :class:`MiniQmtDataProvider`；传入远端实现（同样实现
            :class:`DataConnector`）即可切换到远端 xtquant 服务。

    Returns:
        CompositeDataConnector 实例
    """
    logger.info("已创建 CompositeDataConnector，路径: DuckDB 缓存 → miniqmt")
    return CompositeDataConnector(cache, miniqmt_provider=miniqmt_provider)
