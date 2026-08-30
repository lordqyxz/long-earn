"""宽表 panel_daily（合并面板物化）+ ADBC 直读测试。

覆盖四类契约：
1. **PIT 等价性**：宽表财务列（SQL fin_span 半开区间）vs 旧路径
   （get_financials UNION + quarterly_to_daily_asof merge_asof backward）
   逐位一致 —— 宽表替代旧路径的数据层依据
2. **脏标记**：save_prices / save_financial_table 写事务内原子打标 →
   ensure_panel_fresh 惰性增量重建 → 标记清除、行数对齐
3. **覆盖引导**：panel_daily 缺口（首读 bootstrap / 部分损坏）→
   read_wide_panel 自动增量重建
4. **降级门控**：缓存 miss / price 末端不足 → None（回退旧路径信号）

均为 PG-backed 测试（共享 long_earn 库），测试数据用随机 symbol 前缀
隔离并在 teardown 清理；共享库存在脏标记积压（下载未完成）时跳过，
避免单测触发全库重建。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import numpy as np
import pandas as pd
import polars as pl
import pytest

from long_earn.backtest.data.cache import DataCache
from long_earn.backtest.data.financial.panel import build_daily_financial_panel
from long_earn.backtest.data.financial.schemas import (
    PANEL_FINANCIAL_FIELDS,
    FinancialSchemaRegistry,
)
from long_earn.backtest.data.wide_panel import read_wide_panel

# 共享库脏标记积压阈值：超过则说明下载脚本运行中，单测触发全库重建
# 不可接受，跳过宽表测试
_DIRTY_BACKLOG_SKIP = 50


def test_financial_field_map_mirrors_panel_fields() -> None:
    """miniqmt_provider.FINANCIAL_FIELD_MAP 与 PANEL_FINANCIAL_FIELDS 镜像锁定。

    两处字段清单是同一份契约的旧/新实现（取数映射 / 宽表列集），
    集合漂移意味着宽表列与 provider 返回列不再对齐。
    """
    from long_earn.backtest.data.miniqmt_provider import FINANCIAL_FIELD_MAP

    assert set(FINANCIAL_FIELD_MAP) == set(PANEL_FINANCIAL_FIELDS)


# ── PG-backed 测试数据 fixture ────────────────────────────────────────

_SEED_DATES = (
    "2024-03-28",
    "2024-03-29",
    "2024-04-19",
    "2024-04-22",
    "2024-08-23",
    "2024-08-26",
    "2024-09-30",
)


@dataclass
class SeededPanel:
    """宽表测试种子数据（随机 symbol 前缀隔离）。"""

    cache: DataCache
    symbols: list[str]
    start: str = "2024-03-01"
    end: str = "2024-09-30"


def _seed(cache: DataCache, symbols: list[str]) -> None:
    """写入行情 + 财务种子数据（PIT 边界覆盖：公告前后 / 跨表公告日 / 同日双报）。"""
    sym_a, sym_b = symbols
    n = len(_SEED_DATES)
    cache.save_prices(
        pd.DataFrame(
            {
                "symbol": [sym_a] * n + [sym_b] * n,
                "date": list(_SEED_DATES) * 2,
                "open": [10.0] * (2 * n),
                "high": [10.5] * (2 * n),
                "low": [9.5] * (2 * n),
                "close": [10.0 + i for i in range(n)] * 2,
                "volume": [1000.0] * (2 * n),
                "is_tradable": [True] * (2 * n),
            }
        )
    )
    # income：年报(03-30 公告) → Q1(04-20) → Q2/Q3 同日双报(08-25)
    cache.save_financial_table(
        "income_stmt",
        pd.DataFrame(
            {
                "symbol": [sym_a] * 4,
                "report_date": ["2023-12-31", "2024-03-31", "2024-06-30", "2024-09-30"],
                "announce_date": [
                    "2024-03-30",
                    "2024-04-20",
                    "2024-08-25",
                    "2024-08-25",
                ],
                "revenue": [100.0, 110.0, 120.0, 130.0],
            }
        ),
    )
    # pershareindex：Q1 预计算 roe（04-20 公告）
    cache.save_financial_table(
        "pershareindex",
        pd.DataFrame(
            {
                "symbol": [sym_a],
                "report_date": ["2024-03-31"],
                "announce_date": ["2024-04-20"],
                "roe": [15.0],
            }
        ),
    )
    # capital：Q1 总股本（04-22 公告——晚于同报告期 income，union 取 MAX
    # 后 Q1 整体 04-22 可见，两侧路径同语义）
    cache.save_financial_table(
        "capital",
        pd.DataFrame(
            {
                "symbol": [sym_a],
                "report_date": ["2024-03-31"],
                "announce_date": ["2024-04-22"],
                "total_shares": [1000.0],
            }
        ),
    )


def _cleanup(cache: DataCache, symbols: list[str]) -> None:
    """清理测试数据（price / 宽表 / 脏标记 / 8 张财务细表）。"""
    conn = cache._get_conn()
    tables = ["price_daily", "panel_daily", "panel_dirty"] + [
        s.table_name for s in FinancialSchemaRegistry.TABLES
    ]
    for table in tables:
        conn.execute(
            f"DELETE FROM {table} WHERE symbol = ANY(%s::varchar[])", [symbols]
        )


@pytest.fixture()
def seeded() -> Iterator[SeededPanel]:
    """种子数据 fixture：脏积压保护 + 随机 symbol 隔离 + teardown 清理。"""
    cache = DataCache()
    backlog = cache._get_conn().execute("SELECT COUNT(*) FROM panel_dirty").fetchone()
    if backlog and backlog[0] > _DIRTY_BACKLOG_SKIP:
        cache.close()
        pytest.skip(f"共享库脏标记积压 {backlog[0]} 只（下载未完成），跳过宽表测试")
    uid = uuid4().hex[:8]
    symbols = [f"WP-A-{uid}.SH", f"WP-B-{uid}.SZ"]
    try:
        _seed(cache, symbols)
        yield SeededPanel(cache=cache, symbols=symbols)
    finally:
        _cleanup(cache, symbols)
        cache.close()


# ── PIT 等价性：宽表 vs 旧路径 ────────────────────────────────────────


@pytest.mark.integration
def test_wide_panel_pit_equivalence(seeded: SeededPanel) -> None:
    """宽表财务列与旧路径（UNION + merge_asof backward）逐位一致。

    行情列直接物化自 price_daily，等价性核心在财务 PIT 对齐：
    - 公告日前 → NaN（未见未来）
    - 公告日当天 → 生效
    - 同公告日双报告期 → 取 report_date 最新
    - 跨表公告日 → union MAX(announce_date) 语义一致
    """
    wide = read_wide_panel(seeded.cache, seeded.symbols, seeded.start, seeded.end)
    assert wide is not None, "种子数据已写入，宽表快路径应命中"

    # 旧路径财务面板（get_financials UNION + quarterly_to_daily_asof）
    legacy = build_daily_financial_panel(
        seeded.cache,
        seeded.symbols,
        seeded.start,
        seeded.end,
        list(PANEL_FINANCIAL_FIELDS),
    )
    assert not legacy.empty

    # 行集对齐：宽表行（price_daily 行）⊆ 旧路径行（全表交易日 × symbols）
    wide_pd = wide.select("timestamp", "symbol", *PANEL_FINANCIAL_FIELDS).to_pandas()
    legacy_reset = legacy.reset_index()
    legacy_reset["timestamp"] = pd.to_datetime(legacy_reset["date"])
    legacy_reset = legacy_reset.rename(
        columns={f: f"{f}_legacy" for f in PANEL_FINANCIAL_FIELDS}
    )
    merged = wide_pd.merge(
        legacy_reset[
            ["timestamp", "symbol", *[f"{f}_legacy" for f in PANEL_FINANCIAL_FIELDS]]
        ],
        on=["timestamp", "symbol"],
        how="left",
    )
    assert len(merged) == len(wide_pd)

    for field in PANEL_FINANCIAL_FIELDS:
        got = pd.to_numeric(merged[field], errors="coerce").to_numpy(dtype=float)
        want = pd.to_numeric(merged[f"{field}_legacy"], errors="coerce").to_numpy(
            dtype=float
        )
        ok = np.isclose(got, want, equal_nan=True) | (np.isnan(got) & np.isnan(want))
        assert ok.all(), f"财务列 {field} 宽表与旧路径不一致"

    # PIT 关键边界抽检（symbol A 的 revenue 演进）
    sym_a = seeded.symbols[0]
    rev = (
        wide.filter(pl.col("symbol") == sym_a)
        .select("timestamp", "revenue")
        .sort("timestamp")
    )
    by_date = {
        str(row["timestamp"].date()): row["revenue"]
        for row in rev.iter_rows(named=True)
    }  # type: ignore[union-attr]
    assert np.isnan(by_date["2024-03-28"])  # 年报公告（03-30）前不可见
    assert np.isnan(by_date["2024-03-29"])
    assert by_date["2024-04-19"] == 100.0  # 年报生效
    assert by_date["2024-04-22"] == 110.0  # Q1（union MAX announce 04-22）
    assert by_date["2024-08-23"] == 110.0  # Q2/Q3 公告（08-25）前
    assert by_date["2024-08-26"] == 130.0  # 同日双报取 report_date 最新（Q3）
    assert by_date["2024-09-30"] == 130.0


@pytest.mark.integration
def test_wide_panel_output_contract(seeded: SeededPanel) -> None:
    """宽表输出契约：列集 / dtype / 排序与旧路径（to_polars_panel）对齐。"""
    from long_earn.backtest.data.cache import PANEL_PRICE_FIELDS

    wide = read_wide_panel(seeded.cache, seeded.symbols, seeded.start, seeded.end)
    assert wide is not None

    expected_cols = [
        "timestamp",
        "symbol",
        *PANEL_PRICE_FIELDS,
        *PANEL_FINANCIAL_FIELDS,
    ]
    assert wide.columns == expected_cols
    assert wide.schema["timestamp"] == pl.Datetime("ns")
    # NaN 语义：无财报覆盖（symbol B）的财务列是 NaN 而非 null，
    # 与 from_pandas 契约一致（因子算子行为不变）
    sym_b_rows = wide.filter(pl.col("symbol") == seeded.symbols[1])
    assert (sym_b_rows["revenue"].is_nan()).all()
    # 排序契约：(timestamp, symbol) 升序（VisibilityGuard is_sorted 快路径）
    assert wide.equals(wide.sort("timestamp", "symbol"))


@pytest.mark.integration
def test_wide_panel_price_columns_passthrough(seeded: SeededPanel) -> None:
    """行情列物化自 price_daily：逐位等于种子值。"""
    wide = read_wide_panel(seeded.cache, seeded.symbols, seeded.start, seeded.end)
    assert wide is not None
    sym_a = seeded.symbols[0]
    got = (
        wide.filter(pl.col("symbol") == sym_a)
        .select("timestamp", "close", "is_tradable")
        .sort("timestamp")
    )
    assert got["close"].to_list() == [10.0 + i for i in range(len(_SEED_DATES))]
    assert got["is_tradable"].all()


# ── 脏标记 + 惰性重建 ────────────────────────────────────────────────


@pytest.mark.integration
def test_dirty_flag_and_lazy_rebuild(seeded: SeededPanel) -> None:
    """save_prices 写事务内原子打脏标记 → ensure_panel_fresh 增量重建。"""
    cache = seeded.cache
    conn = cache._get_conn()

    def _dirty_symbols() -> set[str]:
        rows = conn.execute("SELECT symbol FROM panel_dirty").fetchall()
        return {r[0] for r in rows}

    def _panel_rows(symbol: str) -> int:
        row = conn.execute(
            "SELECT COUNT(*) FROM panel_daily WHERE symbol = %s", [symbol]
        ).fetchone()
        return int(row[0]) if row else 0

    # seed 阶段 save_prices/save_financial_table 已打标
    assert set(seeded.symbols) <= _dirty_symbols()

    cache.ensure_panel_fresh()
    assert _dirty_symbols() == set()
    for sym in seeded.symbols:
        assert _panel_rows(sym) == len(_SEED_DATES)

    # 增量更新：追加一日行情 → 脏标记重现 → 惰性重建后行数对齐
    sym_a = seeded.symbols[0]
    cache.save_prices(
        pd.DataFrame(
            {
                "symbol": [sym_a],
                "date": ["2024-10-08"],
                "close": [99.0],
                "open": [98.0],
                "high": [99.5],
                "low": [97.5],
                "volume": [1.0],
            }
        )
    )
    assert sym_a in _dirty_symbols()
    cache.ensure_panel_fresh()
    assert sym_a not in _dirty_symbols()
    assert _panel_rows(sym_a) == len(_SEED_DATES) + 1


# ── 覆盖引导（bootstrap）与降级门控 ──────────────────────────────────


@pytest.mark.integration
def test_coverage_bootstrap_rebuild(seeded: SeededPanel) -> None:
    """panel_daily 存在缺口 → read_wide_panel 覆盖引导自动重建。"""
    conn = seeded.cache._get_conn()
    sym_a = seeded.symbols[0]
    # 先物化一次
    wide = read_wide_panel(seeded.cache, seeded.symbols, seeded.start, seeded.end)
    assert wide is not None
    assert wide.filter(pl.col("symbol") == sym_a).height == len(_SEED_DATES)
    # 制造缺口：删掉 A 的物化行（模拟部分损坏 / 旧库未物化）
    conn.execute("DELETE FROM panel_daily WHERE symbol = %s", [sym_a])
    # 覆盖引导：uncovered 检查发现 A 行数不一致 → 增量重建
    wide2 = read_wide_panel(seeded.cache, seeded.symbols, seeded.start, seeded.end)
    assert wide2 is not None
    assert wide2.filter(pl.col("symbol") == sym_a).height == len(_SEED_DATES)
    # 重建后财务列恢复 PIT 语义（NaN 表示公告前，非空值存在即重建成功）
    rev_a = wide2.filter(pl.col("symbol") == sym_a)["revenue"]
    assert rev_a.filter(~rev_a.is_nan()).len() == 5  # 04-19 起五日有值


@pytest.mark.integration
def test_wide_panel_fallback_on_cache_miss(seeded: SeededPanel) -> None:
    """price_daily 无该 symbol（缓存 miss）→ None（回退旧路径触发下载）。"""
    missing = [f"WP-NONE-{uuid4().hex[:8]}.SH"]
    assert read_wide_panel(seeded.cache, missing, seeded.start, seeded.end) is None


@pytest.mark.integration
def test_wide_panel_fallback_on_stale_price(seeded: SeededPanel) -> None:
    """price 末端距请求 end_date 超容忍阈值 → None（回退旧路径增量补数）。"""
    # 种子末端 2024-09-30，请求 end 2030 年 → 缓存明显不足
    stale_end = "2030-01-01"
    assert (
        read_wide_panel(seeded.cache, seeded.symbols, seeded.start, stale_end) is None
    )


def test_wide_panel_graceful_on_stub_cache() -> None:
    """stub cache（无 panel 方法）→ AttributeError 被门控捕获 → None 回退。

    单测注入 stub cache 时的隔离保证：快路径失败不抛异常，
    调用方（connector）凭 None 回退旧路径。
    """

    class _StubCache:
        """最小 stub：无任何宽表方法。"""

        def __getattr__(self, name: str) -> Any:
            raise AttributeError(name)

    result = read_wide_panel(
        _StubCache(),  # type: ignore[arg-type]
        ["000001.SZ"],
        "2024-01-01",
        "2024-12-31",
    )
    assert result is None
