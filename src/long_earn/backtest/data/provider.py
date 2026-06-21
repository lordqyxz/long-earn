"""数据提供者模块

统一的数据获取接口，支持多数据源自动降级：
  DuckDB 缓存 → miniqmt (xtquant) → ciccwm (HTTP) → akshare

降级语义：
  - 共享数据（行情、财务）：按上述链逐级降级，任一返回非空即止。
    ciccwm 紧跟 miniqmt，严格优先于 akshare（字段口径更稳定）。
  - ciccwm 独占数据（资金流向、涨跌幅排行、关联板块、热榜资讯）：
    仅 ciccwm 能提供，不进降级链。通过 CompositeDataProvider.ciccwm 直接访问。

架构设计：
  - DataProvider Protocol：统一接口，上层服务只依赖此接口
  - CompositeDataProvider：组合提供者，按优先级自动选择数据源
  - 工厂函数 create_data_provider()：根据环境自动创建最佳提供者
"""

from __future__ import annotations

import logging
from typing import Protocol

import pandas as pd

from long_earn.backtest.data.cache import DataCache
from long_earn.backtest.data.ciccwm_provider import CiccwmDataProvider

logger = logging.getLogger(__name__)

# 紧凑日期长度：YYYYMMDD（8 位数字），需标准化为 YYYY-MM-DD
_COMPACT_DATE_LEN = 8


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


class CompositeDataProvider:
    """组合数据提供者：DuckDB 缓存 → miniqmt → ciccwm → akshare 自动降级。

    数据获取策略：
    1. 优先从 DuckDB 缓存读取
    2. 缓存缺失/过期时，尝试 miniqmt 增量更新
    3. miniqmt 不可用且缓存无数据时，降级到 ciccwm（HTTP，零本地依赖）
    4. ciccwm 亦不可用时，降级到 akshare（公共源）
    5. 每次从远程获取的数据自动写入 DuckDB 缓存

    ciccwm 独占能力（资金流向、排行、板块、热榜）通过 ciccwm 属性直接访问。
    """

    def __init__(self, cache: DataCache | None = None) -> None:
        self.cache = cache or DataCache()
        self._miniqmt: DataProvider | None = None
        self._ciccwm: CiccwmDataProvider | None = None
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
        if len(date_str) == _COMPACT_DATE_LEN and date_str.isdigit():
            return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        return date_str

    @property
    def miniqmt(self) -> DataProvider | None:
        """延迟加载 miniqmt 提供者。"""
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
        try:
            from long_earn.backtest.data.miniqmt_provider import MiniQmtClient

            self._miniqmt_available = MiniQmtClient.get().is_available
        except Exception:
            self._miniqmt_available = False
        return self._miniqmt_available

    @property
    def ciccwm(self) -> CiccwmDataProvider | None:
        """延迟加载 ciccwm 提供者。"""
        if self._ciccwm is not None:
            return self._ciccwm
        try:
            self._ciccwm = CiccwmDataProvider(self.cache)
            return self._ciccwm
        except Exception as e:
            logger.warning(f"ciccwm 提供者加载失败: {e}")
            return None

    @property
    def ciccwm_available(self) -> bool:
        """检测 ciccwm 是否可用。"""
        if self._ciccwm_available is not None:
            return self._ciccwm_available
        provider = self.ciccwm
        self._ciccwm_available = provider.is_available if provider else False
        return self._ciccwm_available

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

    @property
    def ciccwm_provider(self) -> CiccwmDataProvider | None:
        """获取 ciccwm 提供者实例，用于调用独占扩展方法。

        独占方法：get_fund_flow, get_ranking, get_related_blocks,
                  get_hot_rank, get_topic_news

        Returns:
            CiccwmDataProvider 实例，不可用时返回 None
        """
        return self.ciccwm

    def get_price_panel(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
        fields: list[str] | None = None,
    ) -> pd.DataFrame:
        """获取行情数据面板（自动降级）。"""
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

        # 2. miniqmt 不可用，降级到 ciccwm（HTTP 零本地依赖）
        if self.ciccwm_available:
            self._log_source("ciccwm（miniqmt 不可用，降级获取）")
            ciccwm = self.ciccwm
            if ciccwm is not None:
                df = ciccwm.get_price_panel(symbols, start_date, end_date, fields)
                if not df.empty:
                    return df

        # 3. ciccwm 亦不可用，降级到 akshare
        ak = self._get_akshare()
        if ak is not None:
            self._log_source("akshare（miniqmt/ciccwm 不可用，降级获取）")
            df = ak.get_price_panel(symbols, start_date, end_date, fields)
            if not df.empty:
                return df

        # 4. 所有数据源均不可用
        logger.warning(
            "所有数据源均不可用（miniqmt 不可用 + akshare 不可用），行情数据获取失败"
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
        """获取财务数据面板（自动降级）。"""
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

        # 2. miniqmt 不可用，降级到 ciccwm
        if self.ciccwm_available:
            self._log_source("ciccwm（miniqmt 不可用，降级获取）")
            ciccwm = self.ciccwm
            if ciccwm is not None:
                df = ciccwm.get_financial_panel(
                    symbols, start_date, end_date, fields
                )
                if not df.empty:
                    return df

        # 3. ciccwm 亦不可用，降级到 akshare
        ak = self._get_akshare()
        if ak is not None:
            self._log_source("akshare（miniqmt 不可用且缓存无数据，降级获取）")
            df = ak.get_financial_panel(symbols, start_date, end_date, fields)
            if not df.empty:
                return df

        logger.warning("所有数据源均不可用，财务数据获取失败")
        return pd.DataFrame()

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
        if not isinstance(fin_df.index, pd.MultiIndex) or fin_df.index.nlevels < 2:  # noqa: PLR2004
            # 财务数据 index 不规范，只返回行情数据
            return price_df
        # 统一 index names，确保一致
        if price_df.index.names != fin_df.index.names:
            fin_df.index.names = price_df.index.names
        # 使用 reset_index + merge + set_index 避免 MultiIndex join 问题
        p = price_df.reset_index()
        f = fin_df.reset_index()
        idx_cols = [c for c in p.columns if c in f.columns][:2]
        if len(idx_cols) < 2:  # noqa: PLR2004
            return price_df
        p[idx_cols[0]] = pd.to_datetime(p[idx_cols[0]])
        f[idx_cols[0]] = pd.to_datetime(f[idx_cols[0]])
        merged = pd.merge(p, f, on=idx_cols, how="outer")
        merged = merged.set_index(idx_cols)
        merged = merged.groupby(level=idx_cols[1]).ffill()
        return merged.sort_index()


def create_data_provider(cache: DataCache | None = None) -> CompositeDataProvider:
    """工厂函数：创建组合数据提供者。

    自动检测可用数据源，按优先级组合：
    DuckDB 缓存 → miniqmt → ciccwm → akshare

    Args:
        cache: DuckDB 缓存实例，默认自动创建

    Returns:
        CompositeDataProvider 实例
    """
    return CompositeDataProvider(cache)


# 向后兼容别名
get_data_provider = create_data_provider
