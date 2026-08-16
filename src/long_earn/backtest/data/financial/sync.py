"""财务缓存同步 — 检测缺失/过期并委托数据源写回 DuckDB。

面板读路径见 :mod:`financial.panel`；本模块只管「缓存是否够用」。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Protocol

import pandas as pd
from loguru import logger

from long_earn.backtest.data.cache import DataCache

_SYNC_SLOW_SYMBOLS = 500
_SYNC_SLOW_SECONDS = 1.0


class FinancialCacheIngestor(Protocol):
    """能把季频财务数据拉取并写入 ``DataCache`` 的数据源（如 miniqmt）。"""

    @property
    def is_available(self) -> bool: ...

    def fetch_financials(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame | None: ...


def get_quarters_between(start_date: str, end_date: str) -> list[str]:
    """获取日期范围内的季度报告期（含 start 前最近一季，供 asof 预热）。"""
    start = pd.to_datetime(start_date)
    end = pd.to_datetime(end_date)
    all_quarters: list[str] = []
    for year in range(start.year - 1, end.year + 1):
        for qe in ("0331", "0630", "0930", "1231"):
            all_quarters.append(f"{year}{qe}")
    quarters = [
        q for q in all_quarters if start <= pd.to_datetime(q, format="%Y%m%d") <= end
    ]
    before_start = [
        q for q in all_quarters if pd.to_datetime(q, format="%Y%m%d") < start
    ]
    if before_start:
        quarters.append(max(before_start))
    return sorted(set(quarters))


def is_financial_stale(
    cache: DataCache,
    symbols: list[str],
    end_date: str = "",
) -> bool:
    """检测财务缓存是否过期（批量查最新公告日）。"""
    threshold = timedelta(days=120)
    now = datetime.now()
    if end_date:
        end_dt = pd.to_datetime(end_date)
        if (now - end_dt) > threshold:
            return False
    if not symbols:
        return False
    t0 = time.perf_counter()
    latest_map = cache.get_financial_latest_announces(symbols)
    elapsed = time.perf_counter() - t0
    if elapsed > _SYNC_SLOW_SECONDS or len(symbols) >= _SYNC_SLOW_SYMBOLS:
        logger.info(
            f"财务新鲜度检测: {len(symbols)} 只, "
            f"缓存命中 {len(latest_map)}, 耗时 {elapsed:.1f}s"
        )
    if len(latest_map) < len(symbols):
        return True
    for latest_str in latest_map.values():
        if (now - pd.to_datetime(latest_str)) > threshold:
            return True
    return False


def ensure_financial_cache(
    cache: DataCache,
    symbols: list[str],
    start_date: str,
    end_date: str,
    ingestor: FinancialCacheIngestor | None,
) -> None:
    """若缓存缺报告期或过期，从 ingestor 增量拉取并写回 DuckDB。"""
    if not symbols or ingestor is None:
        return
    if not getattr(ingestor, "is_available", False):
        return

    quarters = get_quarters_between(start_date, end_date)
    start_pd = pd.to_datetime(start_date)
    in_range_quarters = [
        q for q in quarters if pd.to_datetime(q, format="%Y%m%d") >= start_pd
    ]

    # P3-08: 限定报告期窗口 [start, end]——只查本切分窗口内的缓存报告期，
    # 收窄查询量并防越界取数（训练/测试/验证集纵深防御）。缺失判定只关心
    # in_range_quarters，过滤前后结果等价，仅减少数据搬运。
    cached_df = cache.get_financials(symbols, start_date=start_date, end_date=end_date)
    missing_quarters = in_range_quarters
    if cached_df is not None and not cached_df.empty:
        cached_quarters = set(cached_df["report_date"].dt.strftime("%Y%m%d").unique())
        missing_quarters = [q for q in in_range_quarters if q not in cached_quarters]

    need_refresh = bool(missing_quarters) or is_financial_stale(
        cache, symbols, end_date
    )
    if not need_refresh:
        return

    if missing_quarters:
        logger.info(f"财务缓存缺失 {len(missing_quarters)} 个报告期，从数据源补充")
    else:
        logger.info("财务缓存过期，从数据源增量更新")

    fetched = ingestor.fetch_financials(symbols, start_date, end_date)
    if fetched is not None and not fetched.empty:
        cache.save_financials(fetched)
