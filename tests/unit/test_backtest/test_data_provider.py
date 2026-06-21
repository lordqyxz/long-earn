"""数据层未来函数防护测试

财务数据 _quarterly_to_daily 必须用"披露日"而非"报告期截止日"作为可见日期，
否则回测会在截止日次日就用上未公布数据 → 经典未来函数泄漏，违反 ADR-005 金融级可信。
"""

import pandas as pd

from long_earn.backtest.data.miniqmt_provider import MiniQmtDataProvider


class _StubCache:
    """测试用空 cache 桩（_quarterly_to_daily 不依赖 cache）"""

    def __init__(self):
        pass


class TestMergedPanelFfillSorted:
    """get_merged_panel ffill 前必须先 sort_index，否则 outer merge 后行序乱，
    ffill 会用未来值填到过去——典型的隐蔽未来函数泄漏。
    """

    def _make_provider(
        self, price_df: pd.DataFrame, fin_df: pd.DataFrame
    ) -> MiniQmtDataProvider:
        provider = MiniQmtDataProvider.__new__(MiniQmtDataProvider)
        # monkey-patch 底层取数器，强行返回测试数据
        provider.cache = _StubCache()  # type: ignore[assignment]
        provider.client = _StubCache()  # type: ignore[assignment]
        provider.get_price_panel = (  # type: ignore[method-assign]
            lambda *_a, **_k: price_df
        )
        provider.get_financial_panel = (  # type: ignore[method-assign]
            lambda *_a, **_k: fin_df
        )
        return provider

    def test_ffill_does_not_pull_future_into_past(self):
        """构造场景：财务数据只有 2023-06-01 的 ROE=0.2，
        若 ffill 在 sort 之前跑，可能把 0.2 填进 2023-04-01。"""
        # price_df：包含 4 月、5 月、6 月，ROE 列没有
        price_dates = pd.to_datetime(["2023-04-01", "2023-05-01", "2023-06-01"])
        price_df = pd.DataFrame(
            {
                "open": [10.0, 11.0, 12.0],
                "close": [10.5, 11.5, 12.5],
            },
            index=pd.MultiIndex.from_product(
                [price_dates, ["000001"]], names=["date", "symbol"]
            ),
        )

        # fin_df：仅 6-01 有 ROE=0.2
        fin_df = pd.DataFrame(
            {"roe": [0.2]},
            index=pd.MultiIndex.from_tuples(
                [(pd.Timestamp("2023-06-01"), "000001")], names=["date", "symbol"]
            ),
        )

        provider = self._make_provider(price_df, fin_df)
        result = provider.get_merged_panel(
            symbols=["000001"],
            start_date="2023-04-01",
            end_date="2023-06-30",
        )

        sym = result.xs("000001", level="symbol")
        # 4-01 / 5-01: ROE 仍应是 NaN（未来数据不能填到过去）
        assert pd.isna(sym.loc[pd.Timestamp("2023-04-01"), "roe"])
        assert pd.isna(sym.loc[pd.Timestamp("2023-05-01"), "roe"])
        # 6-01: ROE 应是 0.2
        assert sym.loc[pd.Timestamp("2023-06-01"), "roe"] == 0.2

    def test_ffill_propagates_old_value_forward(self):
        """合法场景：4-01 的 ROE=0.1 应被前向填充到 5-01 / 6-01"""
        price_dates = pd.to_datetime(["2023-04-01", "2023-05-01", "2023-06-01"])
        price_df = pd.DataFrame(
            {"close": [10.0, 11.0, 12.0]},
            index=pd.MultiIndex.from_product(
                [price_dates, ["000001"]], names=["date", "symbol"]
            ),
        )
        fin_df = pd.DataFrame(
            {"roe": [0.1]},
            index=pd.MultiIndex.from_tuples(
                [(pd.Timestamp("2023-04-01"), "000001")], names=["date", "symbol"]
            ),
        )

        provider = self._make_provider(price_df, fin_df)
        result = provider.get_merged_panel(
            symbols=["000001"],
            start_date="2023-04-01",
            end_date="2023-06-30",
        )

        sym = result.xs("000001", level="symbol")
        # ROE 沿时间向后传播
        assert sym.loc[pd.Timestamp("2023-04-01"), "roe"] == 0.1
        assert sym.loc[pd.Timestamp("2023-05-01"), "roe"] == 0.1
        assert sym.loc[pd.Timestamp("2023-06-01"), "roe"] == 0.1


class TestQuarterlyToDailyNoLookahead:
    def _make_provider(self) -> MiniQmtDataProvider:
        provider = MiniQmtDataProvider.__new__(MiniQmtDataProvider)
        provider.cache = _StubCache()  # type: ignore[assignment]
        provider.client = _StubCache()  # type: ignore[assignment]
        return provider

    def _make_quarterly_df(self) -> pd.DataFrame:
        # 一个 Q1 季报，截止日 2023-03-31，含 ROE 字段
        return pd.DataFrame(
            {
                "symbol": ["000001"],
                "report_date": [pd.Timestamp("2023-03-31")],
                "roe": [0.15],
            }
        )

    def test_day_after_report_date_not_visible(self):
        """report_date=2023-03-31 时 2023-04-01 还在披露窗口内，
        必须 NaN（不可见），而不是用了未来才公布的 ROE。"""
        provider = self._make_provider()
        trading_dates = pd.date_range("2023-04-01", "2023-04-15", freq="B")

        result = provider._quarterly_to_daily(
            self._make_quarterly_df(),
            symbols=["000001"],
            trading_dates=trading_dates,
            fields=["roe"],
        )

        # 早于 report_date + 60 天的所有日期，roe 必须为 NaN
        first_day_roe = result.xs("000001", level="symbol")["roe"].iloc[0]
        assert pd.isna(first_day_roe), (
            f"截止日次日就泄漏了未来财务数据 roe={first_day_roe}"
        )

    def test_visible_only_after_publication_lag(self):
        """report_date=2023-03-31 + 60d = 2023-05-30，之后才能用"""
        provider = self._make_provider()
        trading_dates = pd.date_range("2023-03-31", "2023-07-31", freq="B")

        result = provider._quarterly_to_daily(
            self._make_quarterly_df(),
            symbols=["000001"],
            trading_dates=trading_dates,
            fields=["roe"],
        )
        sym_data = result.xs("000001", level="symbol")["roe"]

        # 2023-04-15: 不可见
        d_lookback = sym_data.loc[pd.Timestamp("2023-04-14")]
        assert pd.isna(d_lookback)
        # 2023-06-01: 已过披露窗口，可见
        d_visible = sym_data.loc[pd.Timestamp("2023-06-01")]
        assert d_visible == 0.15

    def test_custom_publication_lag_zero(self):
        """publication_lag_days=0 退化为旧行为（不推荐，仅为兼容）"""
        provider = self._make_provider()
        trading_dates = pd.date_range("2023-04-01", "2023-04-10", freq="B")

        result = provider._quarterly_to_daily(
            self._make_quarterly_df(),
            symbols=["000001"],
            trading_dates=trading_dates,
            fields=["roe"],
            publication_lag_days=0,
        )

        # publication_lag_days=0 时 2023-04-01 就可见（旧行为）
        first_day_roe = result.xs("000001", level="symbol")["roe"].iloc[0]
        assert first_day_roe == 0.15
