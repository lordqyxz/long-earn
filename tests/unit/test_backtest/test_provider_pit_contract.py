"""数据提供者接口契约测试（面向接口，所有实现共用一套）。

测试对象：DataProvider Protocol 的三个实现
  - AkshareFallbackProvider
  - MiniQmtDataProvider（含 _quarterly_to_daily）
  - CiccwmDataProvider

测试哲学（见 project_memory.md）：
  - 契约优先于实现：验证 Protocol 定义的行为，不验证实现细节
  - 所有实现共用一套参数化测试，一处契约变更，所有实现同步验证
  - 用 mock 数据构造可控场景，不依赖真实网络调用

核心契约（ADR-007）：
  C1. PIT 契约：get_financial_panel 必须返回日频面板，基于 announce_date
      （真实财报发布日期）对齐，杜绝未来函数（timestamp=T 的行只含
      announce_date <= T 的报告值）
  C2. 空返回语义：symbols 为空或数据源不可用时返回空 DataFrame
  C3. 索引契约：返回 DataFrame 的 index 必须是 (date, symbol) MultiIndex
  C4. 端到端 PIT 验证：get_financial_panel 完整链路无未来函数
  C5. 字段提取契约：akshare 提取"公告日期"、miniqmt 提取"m_anntime"、
      ciccwm 用差异化 lag 估算 announce_date
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pandas as pd
import pytest

from long_earn.backtest.data.akshare_provider import AkshareFallbackProvider
from long_earn.backtest.data.ciccwm_provider import (
    CiccwmDataProvider,
    _lag_by_report_type,
)
from long_earn.backtest.data.miniqmt_provider import MiniQmtDataProvider

# ── 统一构造的季频测试数据 ─────────────────────────────────────


def _make_quarterly_data() -> pd.DataFrame:
    """构造一份季频财务数据，用于所有 provider 的 PIT 测试。

    关键场景（ADR-007）：
    - 2020-Q1 报告期 2020-03-31，真实公告日 2020-04-25，值 revenue=100
    - 2020-Q2 报告期 2020-06-30，真实公告日 2020-08-20，值 revenue=200
    """
    return pd.DataFrame(
        {
            "symbol": ["600519.SH"] * 2,
            "report_date": pd.to_datetime(
                ["2020-03-31", "2020-06-30"]
            ),
            "announce_date": pd.to_datetime(
                ["2020-04-25", "2020-08-20"]
            ),
            "revenue": [100.0, 200.0],
            "net_profit": [10.0, 20.0],
        }
    )


# ── 参数化：所有 provider 实现 ─────────────────────────────────


@pytest.fixture(params=["akshare", "ciccwm", "miniqmt"])
def provider_instance(request: pytest.FixtureRequest, tmp_path: Any):
    """参数化 fixture：返回一个 provider 实例。

    所有实现共用一套测试，体现"面向接口测试"哲学。
    直接测 _quarterly_to_daily（PIT 契约核心）和空返回契约，
    避免 mock 网络链路导致的测试不稳定。
    """
    from long_earn.backtest.data.cache import DataCache

    cache = DataCache(db_path=tmp_path / "test.duckdb")
    if request.param == "akshare":
        return AkshareFallbackProvider(cache=cache)
    elif request.param == "ciccwm":
        return CiccwmDataProvider(cache=cache)
    elif request.param == "miniqmt":
        return MiniQmtDataProvider(cache=cache)
    pytest.fail(f"未知 provider: {request.param}")


# ── C1. PIT 契约：基于 announce_date 对齐，杜绝未来函数 ────────────


class TestPITContract:
    """PIT 契约测试：所有 provider 的 _quarterly_to_daily 必须基于 announce_date 对齐。

    核心场景：
    - 2020-Q1 报告期 2020-03-31，announce_date=2020-04-25，revenue=100
    - 公告日前 revenue 必须为 NaN，公告日及之后 revenue 必须为 100
    """

    def test_no_future_function_before_announce_date(
        self, provider_instance: Any
    ):
        """C1.1 公告日前不能用未公布数据——PIT 契约生效"""
        provider = provider_instance
        quarterly_df = _make_quarterly_data()
        # 2020-04-01 ~ 04-24：都在 announce_date(2020-04-25) 之前
        trading_dates = pd.date_range("2020-04-01", "2020-04-24", freq="B")
        result = provider._quarterly_to_daily(
            quarterly_df, ["600519.SH"], trading_dates, ["revenue"]
        )
        assert not result.empty, (
            f"{type(provider).__name__}._quarterly_to_daily 不应返回空"
        )
        assert result["revenue"].isna().all(), (
            f"PIT 违规：{type(provider).__name__} 在 2020-04-01~04-24"
            f"（公告日 2020-04-25 之前）返回了非 NaN revenue，"
            f"该财报 2020-04-25 才公告"
        )

    def test_data_visible_after_announce_date(
        self, provider_instance: Any
    ):
        """C1.2 公告日后数据可见——announce_date 后能看到报告值"""
        provider = provider_instance
        quarterly_df = _make_quarterly_data()
        # 2020-04-25 ~ 2020-06-30：第一份报告已公告，第二份未公告
        trading_dates = pd.date_range("2020-04-25", "2020-06-30", freq="B")
        result = provider._quarterly_to_daily(
            quarterly_df, ["600519.SH"], trading_dates, ["revenue"]
        )
        assert not result.empty
        visible_rows = result[result["revenue"].notna()]
        assert not visible_rows.empty, "应有可见数据行（第一份报告已公告）"
        assert (visible_rows["revenue"] == 100.0).all(), (
            f"{type(provider).__name__} 在 2020-04-25~06-30 的可见行应返回"
            f"第一份报告 revenue=100，实际={visible_rows['revenue'].tolist()}"
        )

    def test_second_report_overrides_after_its_announce_date(
        self, provider_instance: Any
    ):
        """C1.3 第二份报告公告后覆盖第一份——announce_date 后看到新值"""
        provider = provider_instance
        quarterly_df = _make_quarterly_data()
        # 2020-09-01 ~ 09-15：第二份报告 announce_date=2020-08-20 已过
        trading_dates = pd.date_range("2020-09-01", "2020-09-15", freq="B")
        result = provider._quarterly_to_daily(
            quarterly_df, ["600519.SH"], trading_dates, ["revenue"]
        )
        assert not result.empty
        visible_rows = result[result["revenue"].notna()]
        assert not visible_rows.empty
        assert (visible_rows["revenue"] == 200.0).all(), (
            f"{type(provider).__name__} 在 2020-09 月应返回第二份报告 revenue=200，"
            f"实际={visible_rows['revenue'].tolist()}"
        )

    def test_announce_date_boundary_exact(self, provider_instance: Any):
        """C1.4 公告日当天数据可见——boundary 精确性验证"""
        provider = provider_instance
        quarterly_df = _make_quarterly_data()
        # 2020-04-25 是公告日，当天应可见
        trading_dates = pd.DatetimeIndex(["2020-04-24", "2020-04-25"])
        # 需要工作日；2020-04-24 是周五，2020-04-25 是周六
        # 改用工作日场景：构造一个周二的公告日
        quarterly_df2 = pd.DataFrame(
            {
                "symbol": ["600519.SH"],
                "report_date": pd.to_datetime(["2020-03-31"]),
                "announce_date": pd.to_datetime(["2020-04-28"]),  # 周二
                "revenue": [100.0],
            }
        )
        trading_dates = pd.DatetimeIndex(
            ["2020-04-27", "2020-04-28", "2020-04-29"]
        )
        result = provider._quarterly_to_daily(
            quarterly_df2, ["600519.SH"], trading_dates, ["revenue"]
        )
        # 04-27（公告日前）→ NaN；04-28（公告日当天）和 04-29 → 100
        vals = result["revenue"].tolist()
        assert pd.isna(vals[0]), f"公告日前一天应 NaN，实际={vals[0]}"
        assert vals[1] == 100.0, f"公告日当天应可见，实际={vals[1]}"
        assert vals[2] == 100.0, f"公告日后一天应可见，实际={vals[2]}"


# ── C2. 空返回语义 ─────────────────────────────────────────────


class TestEmptyReturnContract:
    """空返回契约：symbols 为空时返回空 DataFrame。"""

    def test_empty_symbols_returns_empty_df(self, provider_instance: Any):
        """C2.1 get_financial_panel symbols 为空 → 空 DataFrame"""
        provider = provider_instance
        result = provider.get_financial_panel(
            [], "2020-01-01", "2020-12-31", fields=["revenue"]
        )
        assert isinstance(result, pd.DataFrame)
        assert result.empty

    def test_empty_symbols_price_panel(self, provider_instance: Any):
        """C2.2 get_price_panel symbols 为空 → 空 DataFrame"""
        provider = provider_instance
        result = provider.get_price_panel(
            [], "2020-01-01", "2020-12-31"
        )
        assert isinstance(result, pd.DataFrame)
        assert result.empty


# ── C3. 索引契约 ───────────────────────────────────────────────


class TestIndexContract:
    """索引契约：_quarterly_to_daily 返回 DataFrame 必须是 (date, symbol) MultiIndex。"""

    def test_financial_panel_index_is_date_symbol_multiindex(
        self, provider_instance: Any
    ):
        """C3.1 _quarterly_to_daily 返回 (date, symbol) MultiIndex"""
        provider = provider_instance
        quarterly_df = _make_quarterly_data()
        trading_dates = pd.date_range("2020-06-01", "2020-07-15", freq="B")
        result = provider._quarterly_to_daily(
            quarterly_df, ["600519.SH"], trading_dates, ["revenue"]
        )
        assert not result.empty
        assert isinstance(result.index, pd.MultiIndex), (
            f"{type(provider).__name__}._quarterly_to_daily 必须返回 MultiIndex，"
            f"实际={type(result.index).__name__}"
        )
        names = set(result.index.names)
        assert "date" in names or "timestamp" in names, (
            f"index names 缺少 date/timestamp: {result.index.names}"
        )
        assert "symbol" in names, f"index names 缺少 symbol: {result.index.names}"


# ── C4. 端到端 PIT 验证：get_financial_panel 完整链路 ──────────────


class TestEndToEndPITNoFutureFunction:
    """端到端验证：get_financial_panel 返回的日频面板必须无未来函数。

    场景：2020-Q1 报告期 2020-03-31，announce_date=2020-04-25，revenue=100。
    - 2020-04-01 ~ 04-24（公告前）：revenue 必须为 NaN
    - 2020-04-25 起（公告后）：revenue 必须为 100
    """

    def test_akshare_financial_panel_no_future_function(self, tmp_path: Any):
        """C4.1 akshare get_financial_panel 端到端验证无未来函数。"""
        from long_earn.backtest.data.cache import DataCache

        cache = DataCache(db_path=tmp_path / "test_e2e.duckdb")
        provider = AkshareFallbackProvider(cache=cache)

        # mock akshare 返回中文列名 DataFrame，含"公告日期"列（ADR-007）
        mock_raw = pd.DataFrame(
            {
                "报告日": ["20200331", "20200630"],
                "公告日期": ["20200425", "20200820"],
                "营业收入": ["100.0", "200.0"],
                "净利润": ["10.0", "20.0"],
            }
        )
        provider._ak = MagicMock()
        provider._ak.stock_financial_report_sina.return_value = mock_raw

        # 公告日前查询
        result = provider.get_financial_panel(
            ["600519.SH"], "2020-04-01", "2020-04-24", fields=["revenue"]
        )
        assert not result.empty, "akshare get_financial_panel 应返回非空日频面板"
        assert result["revenue"].isna().all(), (
            f"PIT 未来函数未修复：akshare 在 2020-04-01~04-24（公告日 04-25 前）"
            f"返回了非 NaN revenue。返回值：{result['revenue'].tolist()[:5]}"
        )

    def test_akshare_financial_panel_visible_after_announce(self, tmp_path: Any):
        """C4.2 akshare 公告日后数据可见"""
        from long_earn.backtest.data.cache import DataCache

        cache = DataCache(db_path=tmp_path / "test_e2e2.duckdb")
        provider = AkshareFallbackProvider(cache=cache)

        mock_raw = pd.DataFrame(
            {
                "报告日": ["20200331", "20200630"],
                "公告日期": ["20200425", "20200820"],
                "营业收入": ["100.0", "200.0"],
                "净利润": ["10.0", "20.0"],
            }
        )
        provider._ak = MagicMock()
        provider._ak.stock_financial_report_sina.return_value = mock_raw

        # 公告日后查询（第一份已公告，第二份未公告）
        result = provider.get_financial_panel(
            ["600519.SH"], "2020-04-25", "2020-06-30", fields=["revenue"]
        )
        assert not result.empty
        visible_rows = result[result["revenue"].notna()]
        assert not visible_rows.empty, "应有可见数据行（第一份报告已公告）"
        assert (visible_rows["revenue"] == 100.0).all(), (
            f"akshare 公告后可见行应返回 revenue=100，"
            f"实际={visible_rows['revenue'].tolist()[:5]}"
        )

    def test_no_provider_returns_raw_quarterly_data(self, tmp_path: Any):
        """C4.3 防回归：所有 provider 的 _quarterly_to_daily 必须返回日频面板。

        季频原始数据（每个报告期一行）是未来函数的来源——
        如果返回季频，回测引擎在 2020-03-31 的 bar 上直接读到 Q1 财报值，
        绕过了 PIT 对齐。这个测试确保所有 provider 都走 _quarterly_to_daily。
        """
        from long_earn.backtest.data.cache import DataCache

        cache = DataCache(db_path=tmp_path / "test_freq.duckdb")
        providers = [
            ("akshare", AkshareFallbackProvider(cache=cache)),
            ("ciccwm", CiccwmDataProvider(cache=cache)),
            ("miniqmt", MiniQmtDataProvider(cache=cache)),
        ]

        for name, provider in providers:
            quarterly_df = _make_quarterly_data()
            trading_dates = pd.date_range("2020-04-01", "2020-09-30", freq="B")
            result = provider._quarterly_to_daily(
                quarterly_df, ["600519.SH"], trading_dates, ["revenue"]
            )
            assert not result.empty, f"{name} 不应返回空"
            # 日频面板应有 ~130 个交易日（4月-9月），远多于季频的 2 行
            assert len(result) > 10, (
                f"{name} 应返回日频面板（>10 行），"
                f"实际 {len(result)} 行（可能返回了季频原始数据）"
            )


# ── C5. 字段提取契约 ───────────────────────────────────────────


class TestAnnounceDateExtraction:
    """字段提取契约：各 provider 必须正确提取/构造 announce_date 字段。"""

    def test_akshare_extracts_gonggao_date(self, tmp_path: Any):
        """C5.1 akshare 提取"公告日期"列作为 announce_date"""
        from long_earn.backtest.data.cache import DataCache

        cache = DataCache(db_path=tmp_path / "test_extract.duckdb")
        provider = AkshareFallbackProvider(cache=cache)

        mock_raw = pd.DataFrame(
            {
                "报告日": ["20200331"],
                "公告日期": ["20200425"],
                "营业收入": ["100.0"],
                "净利润": ["10.0"],
            }
        )
        provider._ak = MagicMock()
        provider._ak.stock_financial_report_sina.return_value = mock_raw

        result = provider.get_financial_panel(
            ["600519.SH"], "2020-04-25", "2020-04-30", fields=["revenue"]
        )
        assert not result.empty
        # 公告日 2020-04-25 后应可见 revenue=100
        visible = result[result["revenue"].notna()]
        assert not visible.empty, "akshare 应提取公告日期并在公告后可见"

    def test_ciccwm_differentiated_lag_year_report(self):
        """C5.2 ciccwm 年报差异化 lag = 120 天（修复 40 交易日泄漏）"""
        # 年报 report_date=12-31，lag=120 天
        # 2019-12-31 + 120 天 = 2020-04-29（2020 是闰年，2 月多 1 天）
        year_end = pd.Timestamp("2019-12-31")
        assert _lag_by_report_type(year_end) == 120
        estimated_announce = year_end + pd.Timedelta(days=120)
        # 120 天后约 4 月底，法定披露截止 4-30，估算值应在 4 月最后一周内
        assert estimated_announce.month == 4, (
            f"年报估算公告日应在 4 月，实际={estimated_announce}"
        )
        assert estimated_announce.day >= 28, (
            f"年报估算公告日应在 4 月底（≥28），实际={estimated_announce}"
        )

    def test_ciccwm_differentiated_lag_half_year(self):
        """C5.3 ciccwm 半年报差异化 lag = 65 天"""
        half_year = pd.Timestamp("2020-06-30")
        assert _lag_by_report_type(half_year) == 65

    def test_ciccwm_differentiated_lag_q1_q3(self):
        """C5.4 ciccwm Q1/Q3 差异化 lag = 35 天"""
        q1 = pd.Timestamp("2020-03-31")
        q3 = pd.Timestamp("2020-09-30")
        assert _lag_by_report_type(q1) == 35
        assert _lag_by_report_type(q3) == 35

    def test_ciccwm_normalize_finance_items_fills_announce_date(self, tmp_path: Any):
        """C5.5 ciccwm _normalize_finance_items 必须填充 announce_date"""
        from long_earn.backtest.data.cache import DataCache

        cache = DataCache(db_path=tmp_path / "test_ciccwm.duckdb")
        provider = CiccwmDataProvider(cache=cache)
        items = [
            {"报告期": "2019-12-31", "净利润": "100"},  # 年报 → +120 天
            {"报告期": "2020-03-31", "净利润": "30"},  # Q1 → +35 天
        ]
        records = provider._normalize_finance_items(items, "600519.SH")
        assert len(records) == 2
        for r in records:
            assert "announce_date" in r
            assert pd.notna(r["announce_date"]), "announce_date 不应为空"
        # 年报 announce_date = 2019-12-31 + 120 天 ≈ 2020-04-29（闰年）
        year_rec = next(
            r for r in records if r["report_date"] == pd.Timestamp("2019-12-31")
        )
        assert year_rec["announce_date"].month == 4, (
            f"年报估算公告日应在 4 月，实际={year_rec['announce_date']}"
        )
        # Q1 announce_date = 2020-03-31 + 35 天 = 2020-05-05
        q1_rec = next(
            r for r in records if r["report_date"] == pd.Timestamp("2020-03-31")
        )
        assert q1_rec["announce_date"] == pd.Timestamp("2020-05-05"), (
            f"Q1 估算公告日应为 2020-05-05，实际={q1_rec['announce_date']}"
        )
