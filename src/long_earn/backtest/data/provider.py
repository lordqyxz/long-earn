"""数据提供者模块 — 向后兼容别名层。

ADR-014 阶段 F：本模块已迁移到 :mod:`long_earn.backtest.data.connector`，
新接口 :class:`DataConnector` 替代 :class:`DataProvider`，承载 miniqmt 全
数据能力（行情/财务/universe/行业指数/行业成分股/板块树/交易日历/标的
基础信息/实时快照 9 类）。

为避免 40 文件激进改名，本模块保留以下别名：
- ``DataProvider = DataConnector``
- ``CompositeDataProvider = CompositeDataConnector``
- ``create_data_provider = create_data_connector``
- ``get_data_provider = create_data_connector``

旧代码无需改动可继续运行；新代码应直接 import ``DataConnector``。

注：``MarketIntelligenceProvider`` 仍保留在本模块（独立第二组接口，
ciccwm 独占，与 DataConnector 分离）。
"""

from __future__ import annotations

from typing import Any, Protocol

import pandas as pd

from long_earn.backtest.data.connector import (
    CompositeDataConnector,
    DataConnector,
    create_data_connector,
)

# ── 向后兼容别名 ────────────────────────────────────────────────────────
# 旧代码 import DataProvider / CompositeDataProvider 继续可用。

DataProvider = DataConnector
"""旧 DataProvider Protocol 别名 — 等价于 :class:`DataConnector`。"""

CompositeDataProvider = CompositeDataConnector
"""旧 CompositeDataProvider 别名 — 等价于 :class:`CompositeDataConnector`。"""

create_data_provider = create_data_connector
"""旧工厂函数别名 — 等价于 :func:`create_data_connector`。"""

get_data_provider = create_data_connector
"""旧工厂函数别名 — 等价于 :func:`create_data_connector`。"""


class MarketIntelligenceProvider(Protocol):
    """市场情报能力接口（第二组接口，与 :class:`DataConnector` 分离）。

    定位差异：
      - ``DataConnector``（行情/财务/行业/板块/日历/...）：有降级链兜底
        （DuckDB→miniqmt），失败静默降级到下一源。
      - ``MarketIntelligenceProvider``（资金流向/排行/板块/资讯）：ciccwm 独占，
        **无降级链**，失败显式报错或返回空（ADR-006 约定）。

    实现者：仅 :class:`CiccwmDataProvider`。上层通过 ``context.market_intelligence``
    显式获取，而非从 ``data_connector`` 上调扩展方法。
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


__all__ = [
    "CompositeDataConnector",
    "CompositeDataProvider",
    "DataConnector",
    "DataProvider",
    "MarketIntelligenceProvider",
    "create_data_connector",
    "create_data_provider",
    "get_data_provider",
]
