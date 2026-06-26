"""股票池管理模块（miniqmt 版）

使用 xtquant.xtdata 接口获取指数成分股和板块股票列表。
"""

from __future__ import annotations

from typing import Protocol

from long_earn.backtest.data.cache import DataCache
from long_earn.backtest.data.miniqmt_provider import (
    get_universe_provider as _miniqmt_universe,
)


class UniverseProvider(Protocol):
    """股票池提供者接口"""

    def get_symbols(self, universe_type: str, date: str) -> list[str]: ...


def get_universe_provider(cache: DataCache | None = None) -> UniverseProvider:
    """获取默认的股票池提供者（miniqmt 版）。"""
    return _miniqmt_universe(cache)
