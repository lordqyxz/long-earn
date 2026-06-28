"""数据提供者模块

统一的数据获取接口，支持多数据源自动降级：
  DuckDB 缓存 → miniqmt (xtquant) → ciccwm → akshare

架构设计：
  - DataProvider Protocol：统一接口，上层服务只依赖此接口
  - CompositeDataProvider：组合提供者，按优先级自动选择数据源
  - 工厂函数 create_data_provider()：根据环境自动创建最佳提供者
"""

from __future__ import annotations

from loguru import logger
from typing import Any, Protocol

import pandas as pd
import polars as pl

from long_earn.backtest.data.cache import DataCache
from long_earn.backtest.data.polars_adapter import to_polars_panel


class DataProvider(Protocol):
    """数据提供者统一接口。

    所有数据源实现必须遵循此接口。
    """

    @property
    def is_available(self) -> bool:
        """数据源是否可用。"""
        ...

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

    def get_financial_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """获取财务数据面板（前向填充到日级）。

        Args:
            symbols: 股票代码列表
            start_date: 起始日期
            end_date: 结束日期
            fields: 需要的财务字段列表

        Returns:
            DataFrame，index 为 (date, symbol)
        """
        ...

    def get_merged_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        price_fields: list[str] | None = None,
        financial_fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """获取合并面板（行情 + 财务）。

        Args:
            symbols: 股票代码列表
            start_date: 起始日期
            end_date: 结束日期
            price_fields: 行情字段
            financial_fields: 财务字段

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

        默认实现委托 :class:`PandasToPolarsProvider` 包装 ``get_merged_panel``，
        子类可直接继承或覆盖以提供更高效的 polars 原生路径。

        Returns:
            polars DataFrame，含 timestamp / symbol / close 等列；空数据返回空 DataFrame
        """
        ...


class MarketIntelligenceProvider(Protocol):
    """市场情报能力接口（第二组接口，与 :class:`DataProvider` 分离）。

    定位差异：
      - ``DataProvider``（行情/财务）：有降级链兜底（DuckDB→miniqmt→ciccwm→akshare），
        失败静默降级到下一源。
      - ``MarketIntelligenceProvider``（资金流向/排行/板块/资讯）：ciccwm 独占，
        **无降级链**，失败显式报错或返回空（ADR-006 约定）。

    实现者：仅 :class:`CiccwmDataProvider`。上层通过 ``context.market_intelligence``
    显式获取，而非从 ``data_provider`` 上调扩展方法。
    """

    @property
    def is_available(self) -> bool:
        """情报源是否可用。"""
        ...

    def get_fund_flow(self, symbol: str) -> pd.DataFrame:
        """获取个股资金流向（当日）。"""
        ...

    def get_ranking(
        self,
        market: int = 6,
        limit: int = 10,
        sort_type: int = 1,
    ) -> pd.DataFrame:
        """获取涨跌幅排行。"""
        ...

    def get_related_blocks(self, symbol: str) -> list[dict[str, Any]]:
        """获取个股关联板块。"""
        ...

    def get_hot_rank(
        self,
        page_size: int = 10,
        page_num: int = 1,
        news_type: int = 1,
    ) -> pd.DataFrame:
        """获取今日热榜资讯。"""
        ...

    def get_topic_news(
        self,
        spec_subject_id: int | None = None,
        page_size: int = 20,
        page_num: int = 1,
        news_type: int = 1,
    ) -> pd.DataFrame:
        """获取专题资讯列表。"""
        ...


class CompositeDataProvider:
    """组合数据提供者：DuckDB 缓存 → miniqmt → ciccwm → akshare 自动降级。

    数据获取策略：
    1. 优先从 DuckDB 缓存读取
    2. 缓存缺失/过期时，尝试 miniqmt 增量更新
    3. miniqmt 不可用且缓存无数据时，降级到 ciccwm（HTTP，无本地依赖）
    4. ciccwm 也不可用时，最终降级到 akshare
    5. 每次从远程获取的数据自动写入 DuckDB 缓存

    miniqmt 后端可注入：传入 ``miniqmt_provider`` 可替换默认的本地
    :class:`MiniQmtDataProvider`，未来可用于远端 xtquant 服务（同样实现
    :class:`DataProvider` 接口）。``cache`` 仅在未注入 provider 时用于
    构造默认本地 provider；注入 provider 时由调用方自行管理其缓存。
    """

    def __init__(
        self,
        cache: DataCache | None = None,
        *,
        miniqmt_provider: DataProvider | None = None,
    ) -> None:
        self.cache = cache or DataCache()
        # 显式注入的 miniqmt 后端（面向 DataProvider 业务接口，对齐 ciccwm/akshare）。
        # 不注入时延迟加载本地 MiniQmtDataProvider；注入时直接使用，不再延迟加载。
        self._injected_miniqmt: DataProvider | None = miniqmt_provider
        self._miniqmt: DataProvider | None = miniqmt_provider
        self._ciccwm: DataProvider | None = None
        self._akshare: DataProvider | None = None
        self._miniqmt_available: bool | None = None
        self._ciccwm_available: bool | None = None
        self._akshare_available: bool | None = None

    @staticmethod
    def _normalize_date(date_str: str) -> str:
        """标准化日期格式为 YYYY-MM-DD（DuckDB 缓存要求）。"""
        if not date_str:
            return date_str
        # YYYYMMDD -> YYYY-MM-DD
        if len(date_str) == 8 and date_str.isdigit():
            return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        return date_str

    @property
    def miniqmt(self) -> DataProvider | None:
        """延迟加载 miniqmt 提供者（已注入则直接返回）。"""
        if self._miniqmt is not None:
            return self._miniqmt
        # 未注入：延迟加载本地 MiniQmtDataProvider
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
        # 已注入的 provider：直接读其 is_available
        if self._injected_miniqmt is not None:
            try:
                self._miniqmt_available = self._injected_miniqmt.is_available
            except Exception:
                self._miniqmt_available = False
            return self._miniqmt_available
        # 未注入：检测本地 xtquant
        try:
            from long_earn.backtest.data.miniqmt_provider import MiniQmtClient

            self._miniqmt_available = MiniQmtClient.get().is_available
        except Exception:
            self._miniqmt_available = False
        return self._miniqmt_available

    @property
    def ciccwm_available(self) -> bool:
        """检测 ciccwm 是否可用。"""
        if self._ciccwm_available is not None:
            return self._ciccwm_available
        provider = self._get_ciccwm()
        self._ciccwm_available = provider.is_available if provider else False
        return self._ciccwm_available

    def _get_ciccwm(self) -> DataProvider | None:
        """延迟加载 ciccwm 提供者。"""
        if self._ciccwm is not None:
            return self._ciccwm
        try:
            from long_earn.backtest.data.ciccwm_provider import (
                CiccwmDataProvider,
            )

            self._ciccwm = CiccwmDataProvider(self.cache)
            return self._ciccwm
        except Exception as e:
            logger.warning(f"ciccwm 提供者加载失败: {e}")
            return None

    @property
    def akshare_available(self) -> bool:
        """检测 akshare 是否可用。"""
        if self._akshare_available is not None:
            return self._akshare_available
        provider = self._get_akshare()
        self._akshare_available = provider.is_available if provider else False
        return self._akshare_available

    def _get_akshare(self) -> DataProvider | None:
        """延迟加载 akshare 提供者。"""
        if self._akshare is not None:
            return self._akshare
        try:
            from long_earn.backtest.data.akshare_provider import (
                AkshareFallbackProvider,
            )

            self._akshare = AkshareFallbackProvider(self.cache)
            return self._akshare
        except Exception as e:
            logger.warning(f"akshare 提供者加载失败: {e}")
            return None

    def _log_source(self, source: str) -> None:
        """记录数据来源。"""
        logger.info(f"[数据来源: {source}]")

    # ── 行情面板 ─────────────────────────────────────────────────────────

    def get_price_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """获取行情数据面板（自动降级）。

        降级链：DuckDB 缓存 → miniqmt → ciccwm → akshare。
        """
        if not symbols:
            return pd.DataFrame()

        start_date = self._normalize_date(start_date)
        end_date = self._normalize_date(end_date)

        # 1. 始终尝试 miniqmt 提供者（内部已含 DuckDB 缓存优先逻辑）
        #    即使 miniqmt 不可用，它也会从 DuckDB 缓存读取
        mq = self.miniqmt
        if mq is not None:
            df = mq.get_price_panel(symbols, start_date, end_date, fields)
            if not df.empty:
                return df

        # 2. ciccwm 降级（紧跟 miniqmt，优先于 akshare，字段口径更稳定）
        ci = self._get_ciccwm()
        if ci is not None and ci.is_available:
            self._log_source("ciccwm（miniqmt 不可用且缓存无数据，降级获取）")
            df = ci.get_price_panel(symbols, start_date, end_date, fields)
            if not df.empty:
                return df

        # 3. akshare 最终降级
        ak = self._get_akshare()
        if ak is not None:
            self._log_source("akshare（miniqmt + ciccwm 均不可用，最终降级）")
            df = ak.get_price_panel(symbols, start_date, end_date, fields)
            if not df.empty:
                return df

        # 4. 所有数据源均不可用
        logger.warning(
            "所有数据源均不可用（miniqmt + ciccwm + akshare），行情数据获取失败"
        )
        return pd.DataFrame()

    # ── 财务面板 ─────────────────────────────────────────────────────────

    def get_financial_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """获取财务数据面板（自动降级）。

        降级链：DuckDB 缓存 → miniqmt → ciccwm → akshare。
        """
        if not symbols:
            return pd.DataFrame()

        start_date = self._normalize_date(start_date)
        end_date = self._normalize_date(end_date)

        # 1. 始终尝试 miniqmt 提供者（内部已含 DuckDB 缓存优先逻辑）
        mq = self.miniqmt
        if mq is not None:
            df = mq.get_financial_panel(symbols, start_date, end_date, fields)
            if not df.empty:
                return df

        # 2. ciccwm 降级
        ci = self._get_ciccwm()
        if ci is not None and ci.is_available:
            self._log_source("ciccwm（miniqmt 不可用且缓存无数据，降级获取）")
            df = ci.get_financial_panel(symbols, start_date, end_date, fields)
            if not df.empty:
                return df

        # 3. akshare 最终降级
        ak = self._get_akshare()
        if ak is not None:
            self._log_source("akshare（miniqmt + ciccwm 均不可用，最终降级）")
            df = ak.get_financial_panel(symbols, start_date, end_date, fields)
            if not df.empty:
                return df

        logger.warning("所有数据源均不可用，财务数据获取失败")
        return pd.DataFrame()

    # ── 股票池（universe，自动降级） ──────────────────────────────────────

    def get_symbols(self, universe_type: str, date: str = "") -> list[str]:
        """获取股票池（自动降级：miniqmt → ciccwm → akshare）。

        将 universe 纳入与行情/财务同构的降级链，避免 xtquant 不可用时
        股票池获取断链。各 leaf provider 需实现 ``get_symbols`` 时自行处理
        板块/指数映射；未实现的 provider 静默跳过。
        """
        # 1. miniqmt（已注入或延迟加载的 MiniQmtDataProvider 内含 universe 能力）
        mq = self.miniqmt
        if mq is not None:
            symbols = self._try_get_symbols(mq, universe_type, date)
            if symbols:
                return symbols

        # 2. ciccwm 降级
        ci = self._get_ciccwm()
        if ci is not None and ci.is_available:
            symbols = self._try_get_symbols(ci, universe_type, date)
            if symbols:
                self._log_source("ciccwm universe（miniqmt 不可用，降级获取股票池）")
                return symbols

        # 3. akshare 最终降级
        ak = self._get_akshare()
        if ak is not None:
            symbols = self._try_get_symbols(ak, universe_type, date)
            if symbols:
                self._log_source("akshare universe（miniqmt + ciccwm 均不可用，最终降级）")
                return symbols

        logger.warning(
            f"所有数据源均不可用，股票池 '{universe_type}' 获取失败"
        )
        return []

    @staticmethod
    def _try_get_symbols(
        provider: DataProvider, universe_type: str, date: str
    ) -> list[str]:
        """安全调用 provider 的 get_symbols，未实现或异常时返回空列表。"""
        try:
            fn = getattr(provider, "get_symbols", None)
            if fn is None:
                return []
            return list(fn(universe_type, date) or [])
        except Exception as e:
            logger.warning(f"{type(provider).__name__}.get_symbols 失败: {e}")
            return []

    # ── 合并面板 ─────────────────────────────────────────────────────────

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
        # 检查 fin_df 是否有正确的 MultiIndex
        if not isinstance(fin_df.index, pd.MultiIndex) or fin_df.index.nlevels < 2:
            # 财务数据 index 不规范，只返回行情数据
            return price_df
        # 统一 index names，确保一致
        if price_df.index.names != fin_df.index.names:
            fin_df.index.names = price_df.index.names
        # 使用 reset_index + merge + set_index 避免 MultiIndex join 问题
        p = price_df.reset_index()
        f = fin_df.reset_index()
        idx_cols = [c for c in p.columns if c in f.columns][:2]
        if len(idx_cols) < 2:
            return price_df
        p[idx_cols[0]] = pd.to_datetime(p[idx_cols[0]])
        f[idx_cols[0]] = pd.to_datetime(f[idx_cols[0]])
        merged = pd.merge(p, f, on=idx_cols, how="outer")
        merged = merged.set_index(idx_cols)
        merged = merged.groupby(level=idx_cols[1]).ffill()
        return merged.sort_index()

    def get_merged_panel_as_polars(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> pl.DataFrame:
        """获取合并面板并转为 polars（实现 DataProvider Protocol）。

        直接委托 :func:`to_polars_panel` 包装自身的 ``get_merged_panel``，
        让引擎可经统一接口消费降级链结果。
        """
        df = self.get_merged_panel(symbols, start_date, end_date)
        return to_polars_panel(df)


def create_data_provider(
    cache: DataCache | None = None,
    *,
    miniqmt_provider: DataProvider | None = None,
) -> CompositeDataProvider:
    """工厂函数：创建组合数据提供者。

    自动检测可用数据源，按优先级组合：
    DuckDB 缓存 → miniqmt → ciccwm → akshare

    Args:
        cache: DuckDB 缓存实例，默认自动创建
        miniqmt_provider: 可选的 miniqmt 后端提供者（面向 ``DataProvider``
            业务接口）。不传则延迟加载本地 :class:`MiniQmtDataProvider`；
            传入远端实现（同样实现 :class:`DataProvider`）即可切换到远端
            xtquant 服务，无需改动上层。

    Returns:
        CompositeDataProvider 实例
    """
    return CompositeDataProvider(cache, miniqmt_provider=miniqmt_provider)


# 向后兼容别名
get_data_provider = create_data_provider
