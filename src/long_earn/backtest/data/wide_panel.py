"""宽表 panel_daily 直读快路径（ADBC Arrow 二进制协议）。

面板在 PG 内物化为 ``panel_daily``（``cache.py`` 维护物化与脏标记增量
重建），本模块负责消费侧四件事：

1. **新鲜度保证**：``ensure_panel_fresh``（写事务脏标记 → 读者惰性重建）
2. **覆盖引导**：``panel_uncovered_symbols`` 发现缺口 → 增量重建 bootstrap
3. **数据充足性门控**：``price_daily`` 末端日期达到请求 ``end_date``
   容忍阈值；不足说明缓存有缺口，回退旧路径（旧路径含 miniqmt
   增量下载，宽表路径只读已缓存数据）
4. **ADBC Arrow 直读**：psycopg 行协议 → ADBC 二进制批量协议，
   数百万行面板传输显著提速；``fetch_arrow_table`` → polars 零拷贝

降级契约：任何一步失败 / 数据不足返回 ``None``，
``CompositeDataConnector.get_merged_panel_as_polars`` 回退
``get_merged_panel`` 旧路径（pandas merge + ffill）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import cast
from urllib.parse import quote

import polars as pl
from loguru import logger

from long_earn.backtest.data.cache import PANEL_PRICE_FIELDS, DataCache
from long_earn.backtest.data.financial.schemas import PANEL_FINANCIAL_FIELDS
from long_earn.core.pg import resolve_pg_params

# 数据充足性容忍（日历日）：price_daily 末端距请求 end_date 的最大容忍，
# 覆盖春节/国庆长假 + 数据数日未更新的正常滞后；超过视为缓存缺口，
# 回退旧路径触发 miniqmt 增量下载。
_PRICE_STALE_TOLERANCE_DAYS = 10

# 数值列全集（行情侧 + 财务侧）：SQL NULL → NaN 对齐 pandas 缺失语义
_NUMERIC_PANEL_FIELDS: tuple[str, ...] = (
    "open",
    "high",
    "low",
    "close",
    "volume",
    *PANEL_FINANCIAL_FIELDS,
)


def _adbc_uri() -> str:
    """从 ``core.pg`` 统一参数构造 ADBC 连接 URI（密码 URL 编码）。"""
    p = resolve_pg_params()
    return (
        f"postgresql://{p['user']}:{quote(p['password'], safe='')}"
        f"@{p['host']}:{p['port']}/{p['dbname']}"
    )


def _panel_select_sql() -> str:
    """构造 panel_daily 范围查询（$1/$2/$3 占位符，ADBC 原生绑定）。"""
    price_cols = ", ".join(PANEL_PRICE_FIELDS)
    fin_cols = ", ".join(PANEL_FINANCIAL_FIELDS)
    return (
        f"SELECT symbol, date, {price_cols}, {fin_cols} "
        "FROM panel_daily "
        "WHERE symbol = ANY($1::varchar[]) "
        "AND date >= $2::date "
        "AND date <= $3::date "
        "ORDER BY date, symbol"
    )


def _price_data_sufficient(cache: DataCache, symbols: list[str], end_date: str) -> bool:
    """price_daily 末端是否足以覆盖请求 end_date（容忍假期与更新滞后）。"""
    max_date = cache.max_price_date(symbols)
    if max_date is None:
        return False  # price_daily 无这些 symbol —— 缓存 miss
    tolerance = timedelta(days=_PRICE_STALE_TOLERANCE_DAYS)
    return datetime.strptime(max_date[:10], "%Y-%m-%d") >= (
        datetime.strptime(end_date[:10], "%Y-%m-%d") - tolerance
    )


def _fetch_panel_arrow(
    symbols: list[str], start_date: str, end_date: str
) -> pl.DataFrame | None:
    """ADBC 执行宽表查询并 Arrow 直读（零行返回 None 表示缓存 miss）。"""
    import adbc_driver_postgresql.dbapi as pg_dbapi  # noqa: PLC0415

    conn = pg_dbapi.connect(_adbc_uri())
    try:
        cur = conn.cursor()
        cur.execute(_panel_select_sql(), [symbols, start_date[:10], end_date[:10]])
        table = cur.fetch_arrow_table()
    finally:
        conn.close()
    panel = cast(pl.DataFrame, pl.from_arrow(table))
    if panel.is_empty():
        return None
    return _align_legacy_contract(panel)


def _align_legacy_contract(panel: pl.DataFrame) -> pl.DataFrame:
    """对齐旧路径（pandas → ``to_polars_panel``）输出契约。

    - ``date`` (Date) → ``timestamp`` (Datetime ns)：``from_pandas`` 精度
    - 数值列 SQL NULL → NaN：pandas 缺失语义，因子算子（rolling 等）
      对 NaN 的行为与旧路径逐位一致（null 会改变聚合语义）
    - ``is_tradable`` NULL → True：price_daily DDL 默认值
    - 列序固定（timestamp, symbol, 行情, 财务），排序 (timestamp, symbol)
    """
    aligned = panel.with_columns(
        pl.col("date").cast(pl.Datetime("ns")).alias("timestamp"),
        *[pl.col(c).fill_null(float("nan")) for c in _NUMERIC_PANEL_FIELDS],
        pl.col("is_tradable").fill_null(True),
    ).drop("date")
    return aligned.select(
        "timestamp",
        "symbol",
        *PANEL_PRICE_FIELDS,
        *PANEL_FINANCIAL_FIELDS,
    )


def read_wide_panel(
    cache: DataCache,
    symbols: list[str],
    start_date: str,
    end_date: str,
) -> pl.DataFrame | None:
    """读取宽表合并面板（引擎消费契约，含新鲜度与覆盖保证）。

    Args:
        cache: PG 数据缓存（物化与脏标记管理方）
        symbols: 股票代码列表（xtquant 格式）
        start_date: 起始日期（YYYY-MM-DD）
        end_date: 结束日期（YYYY-MM-DD）

    Returns:
        polars DataFrame（timestamp/symbol/行情列/财务列，已按
        (timestamp, symbol) 排序）；缓存 miss / 数据不足 / 任何异常
        返回 None，调用方回退旧路径。
    """
    if not symbols:
        return None
    try:
        cache.ensure_panel_fresh()
        missing = cache.panel_uncovered_symbols(symbols)
        if missing:
            cache.rebuild_panel_symbols(sorted(missing))
        if not _price_data_sufficient(cache, symbols, end_date):
            logger.info(
                f"[宽表] price 缓存末端不足 {end_date[:10]}，回退旧路径增量补数"
            )
            return None
        return _fetch_panel_arrow(symbols, start_date, end_date)
    except Exception as exc:
        logger.warning(f"[宽表] 快路径失败，回退旧路径: {exc}")
        return None
