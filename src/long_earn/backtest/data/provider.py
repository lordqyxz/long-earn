"""数据提供者模块（miniqmt 版）

封装 xtquant.xtdata 数据获取，支持本地 DuckDB 缓存。
"""

from __future__ import annotations

import logging
from typing import Protocol

import pandas as pd

from long_earn.backtest.data.cache import DataCache
from long_earn.backtest.data.miniqmt_provider import (
    MiniQmtDataProvider,
    get_data_provider as _miniqmt_get_provider,
)

logger = logging.getLogger(__name__)


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


def get_data_provider(cache: DataCache | None = None) -> MiniQmtDataProvider:
    """获取默认的数据提供者（miniqmt 版）。"""
    return _miniqmt_get_provider(cache)
