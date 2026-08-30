"""财务日频面板组装 — 与数据源解耦，只消费 PostgreSQL 季频缓存。

对外契约：返回 ``(date, symbol)`` MultiIndex 日频视图；
底层用 ``announce_date`` asof 对齐，不依赖 miniqmt / xtquant。
"""

from __future__ import annotations

import time

import pandas as pd
from loguru import logger

from long_earn.backtest.data.cache import DataCache

# 大池进度日志阈值（与 cache 模块一致）
_PANEL_LOG_MIN_SYMBOLS = 200


def _get_xshg_trading_dates(
    cache: DataCache,
    start_date: str,
    end_date: str,
) -> pd.DatetimeIndex:
    """从 PostgreSQL 缓存获取 XSHG 真实交易日历，回退到 freq="B"（AUDIT-P2-15）。

    优先从 price_daily 表查询实际交易日，避免 US 工作日历与中国节假日
    （春节、国庆等）不匹配的问题。
    """
    try:
        dates = cache.get_trading_dates(start_date, end_date)
        if dates:
            return pd.DatetimeIndex(dates)
    except Exception as exc:
        logger.warning(f"[交易日历] 从缓存获取失败，回退到 freq='B': {exc}")

    return pd.date_range(start=start_date, end=end_date, freq="B")


def quarterly_to_daily_asof(
    quarterly_df: pd.DataFrame,
    symbols: list[str],
    trading_dates: pd.DatetimeIndex,
    fields: list[str],
) -> pd.DataFrame:
    """季频 → 模拟日频：按公告日 backward asof 取最近已披露季报。

    ADR-007：可见起点为 ``announce_date``，非 report_date + 固定 lag。
    """
    if not symbols or len(trading_dates) == 0:
        return pd.DataFrame()

    n_symbols = len(symbols)
    t0 = time.perf_counter()
    if n_symbols >= _PANEL_LOG_MIN_SYMBOLS:
        logger.info(
            f"[财务日级展开] asof对齐: {n_symbols} 只 × "
            f"{len(trading_dates)} 日（动态取最近已公告行）"
        )

    left = (
        pd.MultiIndex.from_product([trading_dates, symbols], names=["date", "symbol"])
        .to_frame(index=False)
        # 统一为 ns 精度：trading_dates 可能来自 PG DATE（psycopg 返回
        # datetime.date → pd.to_datetime 推断为 datetime64[s]），而右侧
        # announce_date 为 datetime64[ns]，merge_asof 要求两侧同 dtype
        .assign(date=lambda df: pd.to_datetime(df["date"]).astype("datetime64[ns]"))
        .sort_values(["symbol", "date"], kind="mergesort")
    )

    value_cols = [f for f in fields if f in quarterly_df.columns]
    if (
        quarterly_df.empty
        or "announce_date" not in quarterly_df.columns
        or "symbol" not in quarterly_df.columns
    ):
        out = left.set_index(["date", "symbol"])
        for field in fields:
            out[field] = pd.NA
        return out[fields]

    right = quarterly_df.loc[:, ["symbol", "announce_date", *value_cols]].copy()
    # 统一 ns 精度（PG DATE → datetime.date 时 pd.to_datetime 会推断为
    # datetime64[s]，需与 left 侧 ns 对齐才能 merge_asof）
    right["announce_date"] = pd.to_datetime(
        right["announce_date"], errors="coerce"
    ).astype("datetime64[ns]")
    right = right.dropna(subset=["announce_date", "symbol"])
    right = right.sort_values(["symbol", "announce_date"], kind="mergesort")
    right_by_symbol = {
        sym: grp.drop(columns=["symbol"]).sort_values("announce_date", kind="mergesort")
        for sym, grp in right.groupby("symbol", sort=False)
    }

    merged_parts: list[pd.DataFrame] = []
    for symbol, left_g in left.groupby("symbol", sort=False):
        left_sorted = left_g.sort_values("date", kind="mergesort")
        right_g = right_by_symbol.get(symbol)
        if right_g is None or right_g.empty:
            part = left_sorted.copy()
            for col in value_cols:
                part[col] = pd.NA
            merged_parts.append(part)
            continue
        part = pd.merge_asof(
            left_sorted,
            right_g,
            left_on="date",
            right_on="announce_date",
            direction="backward",
        )
        merged_parts.append(part)
    merged = pd.concat(merged_parts, ignore_index=True)
    if "announce_date" in merged.columns:
        merged = merged.drop(columns=["announce_date"])

    for field in fields:
        if field not in merged.columns:
            merged[field] = pd.NA

    result = merged.set_index(["date", "symbol"]).sort_index()[fields]
    if n_symbols >= _PANEL_LOG_MIN_SYMBOLS:
        logger.info(
            f"[财务日级展开] asof完成: {len(result)} 行, "
            f"耗时 {time.perf_counter() - t0:.1f}s"
        )
    return result


def build_daily_financial_panel(
    cache: DataCache,
    symbols: list[str],
    start_date: str,
    end_date: str,
    fields: list[str],
) -> pd.DataFrame:
    """从 PostgreSQL 季频缓存组装日频财务面板（引擎消费契约）。"""
    if not symbols:
        return pd.DataFrame()

    if not hasattr(cache, "get_financials"):
        return pd.DataFrame()

    n = len(symbols)
    t0 = time.perf_counter()
    if n >= _PANEL_LOG_MIN_SYMBOLS:
        logger.info(f"[财务面板] 开始: {n} 只, {start_date}~{end_date}")

    # P3-08: 仅上界过滤（report_date <= end_date）——下界不放：backward asof
    # 需要 start 前最近一期财报预热，否则窗口起始几天的财务字段会是 NaN；
    # report_date 晚于 end 的报告期 announce_date 必晚于窗口内任何交易日，
    # asof 本就不会选中，上界过滤只是收窄查询量 + 纵深防御（防越界取数）。
    quarterly = cache.get_financials(symbols, fields, end_date=end_date)
    if quarterly is None or quarterly.empty:
        if n >= _PANEL_LOG_MIN_SYMBOLS:
            logger.info(f"[财务面板] 完成(空): 总耗时 {time.perf_counter() - t0:.1f}s")
        return pd.DataFrame()

    if n >= _PANEL_LOG_MIN_SYMBOLS:
        logger.info(f"[财务面板] 缓存季频: {len(quarterly)} 行, asof对齐到日级...")

    trading_dates = _get_xshg_trading_dates(cache, start_date, end_date)
    t_daily = time.perf_counter()
    panel = quarterly_to_daily_asof(quarterly, symbols, trading_dates, fields)
    if n >= _PANEL_LOG_MIN_SYMBOLS:
        logger.info(
            f"[财务面板] 完成: 日级视图 {len(panel)} 行, "
            f"asof耗时 {time.perf_counter() - t_daily:.1f}s, "
            f"总耗时 {time.perf_counter() - t0:.1f}s"
        )
    return panel
