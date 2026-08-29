"""is_financial_stale 双水位判定测试（回测读路径死循环治理）。

覆盖点（系统关键环节——读路径新鲜度门）：
1. 水位新鲜 + 公告日久远 → 不 stale（沉默股票回归：单看公告日会把
   「数据旧」误判为「没查过」，读路径每次全量重拉）
2. 无数据且无水位 → stale
3. 水位超 recheck 间隔 → stale（到期重查）
4. end_date 过旧 → 短路判新鲜（请求区间语义，行为不变）

PG 不可达时整组跳过。测试数据用唯一 symbol 隔离并清理。
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pandas as pd
import pytest

from long_earn.backtest.data.cache import DataCache
from long_earn.backtest.data.financial.sync import (
    FINANCIAL_RECHECK_DAYS,
    is_financial_stale,
)
from long_earn.core.pg import pg_version


def _pg_available() -> bool:
    try:
        pg_version()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _pg_available(), reason="PostgreSQL 服务不可用")

_UNIQ = uuid4().hex[:10]


def _sym(base: str) -> str:
    return f"{base[:-3]}-{_UNIQ}{base[-3:]}"


@pytest.fixture(autouse=True)
def _cleanup_test_data():
    yield
    cache = DataCache()
    try:
        conn = cache._get_conn()
        conn.execute(
            "DELETE FROM financial_sync_watermark WHERE symbol LIKE %s",
            [f"%-{_UNIQ}.%"],
        )
        conn.execute("DELETE FROM income_stmt WHERE symbol LIKE %s", [f"%-{_UNIQ}.%"])
        conn.commit()
    finally:
        cache.close()


def _save_income(cache: DataCache, symbol: str, announce_date: str) -> None:
    df = pd.DataFrame(
        {
            "symbol": [symbol],
            "report_date": [announce_date[:4] + "-12-31"],
            "announce_date": [announce_date],
            "revenue": [100.0],
        }
    )
    cache.save_financial_table("income_stmt", df)


def _advance(cache: DataCache, symbol: str, checked_until: str) -> None:
    cache.advance_financial_sync_watermarks([symbol], checked_until)


def _d(days_ago: int) -> str:
    return (date.today() - timedelta(days=days_ago)).strftime("%Y-%m-%d")


class TestIsFinancialStaleWatermark:
    """双水位判定：水位证明「最近查过」即可跳过。"""

    def test_fresh_watermark_skips_stale_announce(self):
        """回归核心：公告日 300 天前但水位昨天 → 不 stale（沉默股票）。"""
        cache = DataCache()
        sym = _sym("002731.SZ")
        _save_income(cache, sym, _d(300))
        _advance(cache, sym, _d(1))
        assert is_financial_stale(cache, [sym]) is False

    def test_no_data_no_watermark_is_stale(self):
        """既无数据也无水位 → 需要补。"""
        cache = DataCache()
        sym = _sym("999001.SZ")
        assert is_financial_stale(cache, [sym]) is True

    def test_expired_watermark_is_stale(self):
        """水位超过 FINANCIAL_RECHECK_DAYS → 到期重查。"""
        cache = DataCache()
        sym = _sym("002732.SZ")
        _save_income(cache, sym, _d(300))
        _advance(cache, sym, _d(FINANCIAL_RECHECK_DAYS + 3))
        assert is_financial_stale(cache, [sym]) is True

    def test_old_end_date_short_circuits_fresh(self):
        """end_date 早于追溯阈值 → 请求区间本身够旧，短路判新鲜。"""
        cache = DataCache()
        sym = _sym("999002.SZ")
        assert is_financial_stale(cache, [sym], end_date=_d(200)) is False

    def test_mixed_panel_one_uncovered_is_stale(self):
        """混合面板：任一标的无数据无水位 → 整组 stale（保守拉取）。"""
        cache = DataCache()
        covered = _sym("002733.SZ")
        uncovered = _sym("999003.SZ")
        _save_income(cache, covered, _d(300))
        _advance(cache, covered, _d(1))
        assert is_financial_stale(cache, [covered, uncovered]) is True
