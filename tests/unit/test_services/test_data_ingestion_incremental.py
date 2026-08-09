"""数据下载智能增量单元测试

覆盖点（按项目规范：仅接口层 + 关键环节）：
1. schema 版本化守卫：版本匹配保留数据，版本不匹配 DROP 重建
2. get_financial_latest_announce / get_financial_latest_announces 接口契约
3. get_price_latest_dates 批量接口契约
4. _select_financials_to_refresh 财务增量判定逻辑（关键环节）
5. _select_prices_to_refresh 行情按交易日精确判定逻辑（关键环节）

不测：_download_concurrent / _run_batch_subprocess（subprocess + xtquant，集成测试范畴）
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd

from long_earn.backtest.data.cache import DataCache
from long_earn.services.data_ingestion_service import DataIngestionService

# ── 测试夹具 ──────────────────────────────────────────────────────


def _make_cache(tmp_path) -> DataCache:
    """创建临时 DuckDB 缓存（隔离，不污染真实数据）"""
    return DataCache(db_path=tmp_path / "test_cache.duckdb")


def _make_service(cache: DataCache) -> DataIngestionService:
    """构造 DataIngestionService，注入临时缓存，mock 掉 xtquant 客户端"""
    svc = DataIngestionService.__new__(DataIngestionService)
    svc.cache = cache
    svc.data_provider = MagicMock()
    svc.data_provider.cache = cache
    svc.client = MagicMock()
    svc.logger = None
    return svc


def _save_financial_rows(
    cache: DataCache,
    symbol: str,
    announce_dates: list[str],
    report_dates: list[str] | None = None,
) -> None:
    """向 income_stmt（财务代表表）写入若干行。

    ADR-014 阶段 B：旧 financial_quarterly 已废弃，增量判定改为查 income_stmt。
    此夹具直接写 income_stmt 表（含一个占位 revenue 字段避免空行被过滤）。

    Args:
        symbol: 股票代码
        announce_dates: 公告日列表（每行一个）
        report_dates: 报告期列表；None 时按 announce_dates 顺序生成递增报告期。
            PRIMARY KEY (symbol, report_date) 要求每行 report_date 唯一，否则 upsert 互覆
    """
    if report_dates is None:
        # 按公告日顺序生成递增报告期（季度末），保证 PK 唯一
        report_dates = [f"{d[:4]}-12-31" for d in announce_dates]
        # 若同年公告日多个，用月份区分
        seen_years: set[str] = set()
        for i, d in enumerate(announce_dates):
            year = d[:4]
            if year in seen_years:
                report_dates[i] = f"{year}-06-30"
            else:
                seen_years.add(year)
    df = pd.DataFrame(
        {
            "symbol": [symbol] * len(announce_dates),
            "report_date": report_dates,
            "announce_date": announce_dates,
            "revenue": [100.0] * len(announce_dates),  # 占位字段，避免空行被过滤
        }
    )
    cache.save_financial_table("income_stmt", df)


def _save_price_rows(
    cache: DataCache,
    symbol: str,
    dates: list[str],
) -> None:
    """向 price_daily 写入若干行（symbol/date/close 必填，其余占位）"""
    df = pd.DataFrame(
        {
            "symbol": [symbol] * len(dates),
            "date": dates,
            "open": [10.0] * len(dates),
            "high": [11.0] * len(dates),
            "low": [9.0] * len(dates),
            "close": [10.5] * len(dates),
            "volume": [1000.0] * len(dates),
        }
    )
    cache.save_prices(df)


# ── schema 版本化守卫 ─────────────────────────────────────────────


class TestSchemaVersioning:
    """schema 版本化：版本匹配保留数据，版本不匹配 DROP 重建"""

    def test_version_match_preserves_data(self, tmp_path):
        """版本匹配时重新实例化 DataCache，financial_quarterly 行数不变"""
        cache = _make_cache(tmp_path)
        _save_financial_rows(cache, "000001.SZ", ["2024-01-01", "2024-04-30"])
        assert cache.get_financial_latest_announce("000001.SZ") is not None

        # 关闭后重新实例化（模拟下次运行）
        cache.close()
        cache2 = _make_cache(tmp_path)

        # 版本匹配 → 数据保留
        latest = cache2.get_financial_latest_announce("000001.SZ")
        assert latest is not None
        # announce_date 较晚者胜出
        assert "2024-04-30" in str(latest)

    def test_version_mismatch_drops_table(self, tmp_path):
        """ADR-014 阶段 B：新架构 8 张细表用 CREATE IF NOT EXISTS 幂等建表，
        不再 DROP 重建。篡改版本号后重新实例化，数据保留（schema 稳定），
        版本号被刷回当前版本。"""
        cache = _make_cache(tmp_path)
        _save_financial_rows(cache, "000001.SZ", ["2024-01-01"])

        # 篡改 income_stmt 版本号为旧版本
        conn = cache._get_conn()
        conn.execute(
            "UPDATE _schema_meta SET version = 0 WHERE table_name = 'income_stmt'"
        )
        cache.close()

        # 重新实例化 → CREATE IF NOT EXISTS 幂等，数据保留
        cache2 = _make_cache(tmp_path)
        latest = cache2.get_financial_latest_announce("000001.SZ")
        # 新架构不 DROP，数据保留
        assert latest is not None
        assert "2024-01-01" in str(latest)

        # 版本号已刷回当前版本
        ver = (
            cache2._get_conn()
            .execute(
                "SELECT version FROM _schema_meta WHERE table_name = 'income_stmt'"
            )
            .fetchone()
        )
        assert ver is not None
        assert ver[0] >= 1


# ── get_financial_latest_announce 接口 ───────────────────────────


class TestGetFinancialLatestAnnounce:
    """get_financial_latest_announce 接口契约"""

    def test_returns_max_announce_date(self, tmp_path):
        cache = _make_cache(tmp_path)
        _save_financial_rows(
            cache, "600519.SH", ["2023-04-28", "2024-04-30", "2024-01-31"]
        )
        latest = cache.get_financial_latest_announce("600519.SH")
        assert latest is not None
        assert "2024-04-30" in str(latest)

    def test_returns_none_for_empty_symbol(self, tmp_path):
        cache = _make_cache(tmp_path)
        assert cache.get_financial_latest_announce("999999.SZ") is None


# ── _select_financials_to_refresh 增量判定 ────────────────────────


class TestSelectFinancialsToRefresh:
    """增量预检：按 announce_date 最新公告日阈值筛选需下载股票"""

    TODAY = "2026-07-12"  # 固定今日，避免时间漂移影响断言

    def test_empty_cache_marks_all_for_full_download(self, tmp_path):
        """缓存为空 → 全部进 full_missing 组，stale 为空"""
        cache = _make_cache(tmp_path)
        svc = _make_service(cache)

        full_missing, stale, stale_start = svc._select_financials_to_refresh(
            ["000001.SZ", "000002.SZ"], self.TODAY, start_date="2020-01-01"
        )
        assert full_missing == ["000001.SZ", "000002.SZ"]
        assert stale == []
        # stale 为空时 stale_start 无意义（返回 today）
        assert stale_start == self.TODAY

    def test_empty_cache_empty_start(self, tmp_path):
        """缓存为空且 start_date 空 → full_missing 包含全部，stale_start=today"""
        cache = _make_cache(tmp_path)
        svc = _make_service(cache)

        full_missing, stale, stale_start = svc._select_financials_to_refresh(
            ["000001.SZ"], self.TODAY, start_date=""
        )
        assert full_missing == ["000001.SZ"]
        assert stale == []
        assert stale_start == self.TODAY

    def test_stale_announce_marks_for_incremental(self, tmp_path):
        """最新公告日距今 > 120 天 → 进 stale 组，起始日 = 公告日 + 1 天"""
        cache = _make_cache(tmp_path)
        # 公告日 2025-01-01，距今 > 120 天 → 过期
        _save_financial_rows(cache, "000001.SZ", ["2025-01-01"])
        svc = _make_service(cache)

        full_missing, stale, stale_start = svc._select_financials_to_refresh(
            ["000001.SZ"], self.TODAY, start_date=""
        )
        assert full_missing == []
        assert stale == ["000001.SZ"]
        assert stale_start == "2025-01-02"  # 公告日 + 1 天

    def test_fresh_announce_skipped(self, tmp_path):
        """最新公告日距今 ≤ 120 天 → 跳过（既不在 full_missing 也不在 stale）"""
        cache = _make_cache(tmp_path)
        # 公告日 2026-06-01，距今 41 天 ≤ 120 → 新鲜
        _save_financial_rows(cache, "000001.SZ", ["2026-06-01"])
        svc = _make_service(cache)

        full_missing, stale, _ = svc._select_financials_to_refresh(
            ["000001.SZ"], self.TODAY, start_date=""
        )
        assert full_missing == []
        assert stale == []

    def test_mixed_population(self, tmp_path):
        """混合场景：无缓存 + 过期 + 新鲜 → 正确分组到 full_missing / stale"""
        cache = _make_cache(tmp_path)
        # 过期（公告日 2025-01-01）
        _save_financial_rows(cache, "000001.SZ", ["2025-01-01"])
        # 新鲜（公告日 2026-06-01）
        _save_financial_rows(cache, "000002.SZ", ["2026-06-01"])
        # 000003.SZ 无缓存
        svc = _make_service(cache)

        full_missing, stale, stale_start = svc._select_financials_to_refresh(
            ["000001.SZ", "000002.SZ", "000003.SZ"],
            self.TODAY,
            start_date="2020-01-01",
        )
        # 000001 过期 → stale；000003 无缓存 → full_missing；000002 新鲜 → 跳过
        assert full_missing == ["000003.SZ"]
        assert stale == ["000001.SZ"]
        # stale 起始日 = 2025-01-02
        assert stale_start == "2025-01-02"

    def test_mixed_stale_only_uses_min_stale_start(self, tmp_path):
        """仅过期股票（无全量缺失）→ 起始日取最早过期起始日"""
        cache = _make_cache(tmp_path)
        # 两只都过期，公告日不同
        _save_financial_rows(cache, "000001.SZ", ["2025-01-01"])  # 起始 2025-01-02
        _save_financial_rows(cache, "000002.SZ", ["2025-03-01"])  # 起始 2025-03-02
        svc = _make_service(cache)

        full_missing, stale, stale_start = svc._select_financials_to_refresh(
            ["000001.SZ", "000002.SZ"], self.TODAY, start_date="2020-01-01"
        )
        assert full_missing == []
        assert set(stale) == {"000001.SZ", "000002.SZ"}
        # 最早过期起始日 = 2025-01-02
        assert stale_start == "2025-01-02"


# ── 批量查询接口 ─────────────────────────────────────────────────


class TestBatchLatestInterfaces:
    """get_financial_latest_announces / get_price_latest_dates 批量接口契约"""

    def test_financial_latest_announces_batch(self, tmp_path):
        """批量返回每只股票最新公告日；缓存中不存在的 symbol 不出现在结果"""
        cache = _make_cache(tmp_path)
        _save_financial_rows(cache, "000001.SZ", ["2024-01-01", "2024-04-30"])
        _save_financial_rows(cache, "000002.SZ", ["2025-03-01"])

        result = cache.get_financial_latest_announces(
            ["000001.SZ", "000002.SZ", "999999.SZ"]
        )
        assert "000001.SZ" in result
        assert "2024-04-30" in str(result["000001.SZ"])
        assert "000002.SZ" in result
        assert "999999.SZ" not in result  # 不存在的不出现在 dict

    def test_financial_latest_announces_empty_input(self, tmp_path):
        """空输入返回空 dict"""
        cache = _make_cache(tmp_path)
        assert cache.get_financial_latest_announces([]) == {}

    def test_price_latest_dates_batch(self, tmp_path):
        """批量返回每只股票最新交易日；缓存中不存在的 symbol 不出现在结果"""
        cache = _make_cache(tmp_path)
        _save_price_rows(cache, "000001.SZ", ["2026-07-01", "2026-07-08"])
        _save_price_rows(cache, "000002.SZ", ["2026-07-05"])

        result = cache.get_price_latest_dates(["000001.SZ", "000002.SZ", "999999.SZ"])
        assert "000001.SZ" in result
        assert "2026-07-08" in str(result["000001.SZ"])
        assert "000002.SZ" in result
        assert "999999.SZ" not in result

    def test_price_latest_dates_empty_input(self, tmp_path):
        """空输入返回空 dict"""
        cache = _make_cache(tmp_path)
        assert cache.get_price_latest_dates([]) == {}


# ── _select_prices_to_refresh 行情增量判定 ────────────────────────


class TestSelectPricesToRefresh:
    """行情增量预检：按交易日精确判定，缺一天就补一天"""

    END = "2026-07-12"  # 目标截止日

    def test_empty_cache_marks_all_for_full_download(self, tmp_path):
        """缓存为空 → 全部进 full_missing 组，stale 为空"""
        cache = _make_cache(tmp_path)
        svc = _make_service(cache)

        full_missing, stale, stale_start = svc._select_prices_to_refresh(
            ["000001.SZ", "000002.SZ"], self.END, start_date="2020-01-01"
        )
        assert full_missing == ["000001.SZ", "000002.SZ"]
        assert stale == []
        assert stale_start == self.END  # stale 为空时返回 end_date

    def test_empty_cache_empty_start(self, tmp_path):
        """缓存为空且 start_date 空 → full_missing 包含全部"""
        cache = _make_cache(tmp_path)
        svc = _make_service(cache)

        full_missing, stale, stale_start = svc._select_prices_to_refresh(
            ["000001.SZ"], self.END, start_date=""
        )
        assert full_missing == ["000001.SZ"]
        assert stale == []
        assert stale_start == self.END

    def test_missing_days_marks_for_incremental(self, tmp_path):
        """最新交易日 < end_date → 进 stale 组，起始日 = 最新交易日 + 1 天"""
        cache = _make_cache(tmp_path)
        # 缓存到 2026-07-08，目标 2026-07-12 → 缺 4 天
        _save_price_rows(cache, "000001.SZ", ["2026-07-08"])
        svc = _make_service(cache)

        full_missing, stale, stale_start = svc._select_prices_to_refresh(
            ["000001.SZ"], self.END, start_date=""
        )
        assert full_missing == []
        assert stale == ["000001.SZ"]
        assert stale_start == "2026-07-09"  # 最新日 + 1 天

    def test_up_to_date_skipped(self, tmp_path):
        """最新交易日 >= end_date → 跳过（既不在 full_missing 也不在 stale）"""
        cache = _make_cache(tmp_path)
        # 缓存到 2026-07-12，目标 2026-07-12 → 已齐
        _save_price_rows(cache, "000001.SZ", ["2026-07-12"])
        svc = _make_service(cache)

        full_missing, stale, _ = svc._select_prices_to_refresh(
            ["000001.SZ"], self.END, start_date=""
        )
        assert full_missing == []
        assert stale == []

    def test_mixed_population(self, tmp_path):
        """混合场景：无缓存 + 待补 + 已齐 → 正确分组到 full_missing / stale"""
        cache = _make_cache(tmp_path)
        # 待补（缓存到 2026-07-08，缺 4 天）
        _save_price_rows(cache, "000001.SZ", ["2026-07-08"])
        # 已齐（缓存到 2026-07-12）
        _save_price_rows(cache, "000002.SZ", ["2026-07-12"])
        # 000003.SZ 无缓存
        svc = _make_service(cache)

        full_missing, stale, stale_start = svc._select_prices_to_refresh(
            ["000001.SZ", "000002.SZ", "000003.SZ"],
            self.END,
            start_date="2020-01-01",
        )
        # 000001 待补 → stale；000003 无缓存 → full_missing；000002 已齐 → 跳过
        assert full_missing == ["000003.SZ"]
        assert stale == ["000001.SZ"]
        # stale 起始日 = 2026-07-09
        assert stale_start == "2026-07-09"

    def test_mixed_stale_only_uses_min_stale_start(self, tmp_path):
        """仅待补股票（无全量缺失）→ 起始日取最早待补起始日"""
        cache = _make_cache(tmp_path)
        # 两只都待补，缓存最新日不同
        _save_price_rows(cache, "000001.SZ", ["2026-07-08"])  # 起始 2026-07-09
        _save_price_rows(cache, "000002.SZ", ["2026-07-10"])  # 起始 2026-07-11
        svc = _make_service(cache)

        full_missing, stale, stale_start = svc._select_prices_to_refresh(
            ["000001.SZ", "000002.SZ"], self.END, start_date="2020-01-01"
        )
        assert full_missing == []
        assert set(stale) == {"000001.SZ", "000002.SZ"}
        # 最早待补起始日 = 2026-07-09
        assert stale_start == "2026-07-09"
