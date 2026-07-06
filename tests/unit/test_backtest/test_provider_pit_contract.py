"""数据提供者接口契约测试（面向接口，所有实现共用一套）。

测试对象：DataProvider Protocol 的三个实现
  - AkshareFallbackProvider
  - MiniQmtUniverseProvider（含 _quarterly_to_daily）
  - CiccwmDataProvider

测试哲学（见 project_memory.md）：
  - 契约优先于实现：验证 Protocol 定义的行为，不验证实现细节
  - 所有实现共用一套参数化测试，一处契约变更，所有实现同步验证
  - 用 mock 数据构造可控场景，不依赖真实网络调用

核心契约：
  C1. PIT 契约：get_financial_panel 必须返回日频面板，应用 60 天披露延迟，
      杜绝未来函数（timestamp=T 的行只含 visible_from <= T 的报告值）
  C2. 空返回语义：symbols 为空或数据源不可用时返回空 DataFrame
  C3. 索引契约：返回 DataFrame 的 index 必须是 (date, symbol) MultiIndex
  C4. 合并面板契约：get_merged_panel 返回行情+财务列，财务列按 symbol ffill
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import pytest

from long_earn.backtest.data.akshare_provider import (
    DEFAULT_PUBLICATION_LAG_DAYS as AKSHARE_LAG,
    AkshareFallbackProvider,
)
from long_earn.backtest.data.ciccwm_provider import (
    DEFAULT_PUBLICATION_LAG_DAYS as CICCWM_LAG,
    CiccwmDataProvider,
)
from long_earn.backtest.data.miniqmt_provider import (
    MiniQmtDataProvider,
)

# ── Provider 工厂 ──────────────────────────────────────────────


# ── 统一构造的季频测试数据 ─────────────────────────────────────

def _make_quarterly_data() -> pd.DataFrame:
    """构造一份季频财务数据，用于所有 provider 的 PIT 测试。

    关键场景：2020-Q1 报告期 2020-03-31，值 revenue=100。
    60 天后 = 2020-05-30 才可见。
    """
    return pd.DataFrame(
        {
            "symbol": ["600519.SH"] * 2,
            "report_date": pd.to_datetime(
                ["2020-03-31", "2020-06-30"]
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


# ── C1. PIT 契约：60 天披露延迟，杜绝未来函数 ────────────────────


class TestPITContract:
    """PIT 契约测试：所有 provider 的 _quarterly_to_daily 必须应用 60 天披露延迟。

    核心场景：2020-Q1 报告期 2020-03-31，revenue=100。
    - 2020-03-31 ~ 2020-05-29（披露前）：revenue 必须为 NaN
    - 2020-05-30 起（披露后）：revenue 必须为 100

    直接测 _quarterly_to_daily 方法，避免 get_financial_panel 的网络/mock 链路干扰。
    """

    def test_no_future_function_before_disclosure(
        self, provider_instance: Any
    ):
        """C1.1 报告期截止日当天不能用未公布数据——PIT 延迟生效"""
        provider = provider_instance
        quarterly_df = _make_quarterly_data()
        trading_dates = pd.date_range("2020-04-01", "2020-04-30", freq="B")
        result = provider._quarterly_to_daily(
            quarterly_df, ["600519.SH"], trading_dates, ["revenue"]
        )
        assert not result.empty, (
            f"{type(provider).__name__}._quarterly_to_daily 不应返回空"
        )
        # 4 月所有日期都在 visible_from(2020-05-30) 之前，revenue 必须全为 NaN
        assert result["revenue"].isna().all(), (
            f"PIT 违规：{type(provider).__name__} 在 2020-04-01~04-30（报告期后 15-45 天，"
            f"披露延迟 60 天内）返回了非 NaN revenue，"
            f"该财报 2020-05-30 才可见"
        )

    def test_data_visible_after_disclosure_lag(
        self, provider_instance: Any
    ):
        """C1.2 披露延迟后数据可见——60 天后能看到报告值"""
        provider = provider_instance
        quarterly_df = _make_quarterly_data()
        # 2020-06-01 ~ 2020-07-15：第一份报告 2020-05-30 可见，
        # 第二份报告 2020-08-29 才可见
        trading_dates = pd.date_range("2020-06-01", "2020-07-15", freq="B")
        result = provider._quarterly_to_daily(
            quarterly_df, ["600519.SH"], trading_dates, ["revenue"]
        )
        assert not result.empty
        visible_rows = result[result["revenue"].notna()]
        assert not visible_rows.empty, "应有可见数据行"
        # 所有可见行的 revenue 都应是第一份报告的 100
        assert (visible_rows["revenue"] == 100.0).all(), (
            f"{type(provider).__name__} 在 2020-06-01~07-15 的可见行应返回"
            f"第一份报告 revenue=100，实际={visible_rows['revenue'].tolist()}"
        )

    def test_second_report_overrides_after_its_disclosure(
        self, provider_instance: Any
    ):
        """C1.3 第二份报告披露后覆盖第一份——60 天后看到新值"""
        provider = provider_instance
        quarterly_df = _make_quarterly_data()
        # 2020-10-01：第二份报告 2020-06-30 + 60 天 = 2020-08-29 已过
        trading_dates = pd.date_range("2020-10-01", "2020-10-15", freq="B")
        result = provider._quarterly_to_daily(
            quarterly_df, ["600519.SH"], trading_dates, ["revenue"]
        )
        assert not result.empty
        visible_rows = result[result["revenue"].notna()]
        assert not visible_rows.empty
        # 10 月时第二份报告已可见（revenue=200）
        assert (visible_rows["revenue"] == 200.0).all(), (
            f"{type(provider).__name__} 在 2020-10 月应返回第二份报告 revenue=200，"
            f"实际={visible_rows['revenue'].tolist()}"
        )

    def test_publication_lag_constant_unified(self):
        """C1.4 三个 provider 的披露延迟常量必须一致（60 天）"""
        assert AKSHARE_LAG == 60
        assert CICCWM_LAG == 60
        # miniqmt 用默认参数 60，从 _quarterly_to_daily 签名验证
        import inspect

        sig = inspect.signature(MiniQmtDataProvider._quarterly_to_daily)
        lag_default = sig.parameters["publication_lag_days"].default
        assert lag_default == 60, (
            f"miniqmt _quarterly_to_daily publication_lag_days 默认值={lag_default}，"
            "应与 akshare/ciccwm 一致为 60"
        )

    def test_zero_lag_degrades_to_immediate_visibility(
        self, provider_instance: Any
    ):
        """C1.5 publication_lag_days=0 退化为立即可见（兼容旧行为，不推荐）"""
        provider = provider_instance
        quarterly_df = pd.DataFrame(
            {
                "symbol": ["600519.SH"],
                "report_date": [pd.Timestamp("2020-03-31")],
                "revenue": [100.0],
            }
        )
        trading_dates = pd.date_range("2020-03-31", "2020-04-10", freq="B")
        result = provider._quarterly_to_daily(
            quarterly_df,
            ["600519.SH"],
            trading_dates,
            ["revenue"],
            publication_lag_days=0,
        )
        # lag=0 时报告期截止日当天就可见
        assert not result.empty
        visible_rows = result[result["revenue"].notna()]
        assert not visible_rows.empty
        assert (visible_rows["revenue"] == 100.0).all()


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
