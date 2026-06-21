"""ciccwm 数据提供者单元测试。

通过 mock ciccwm_client 层验证 provider 逻辑，不发起真实 HTTP 请求。
"""

import tempfile
from pathlib import Path

import pandas as pd
import pytest

from long_earn.backtest.data import ciccwm_client as client
from long_earn.backtest.data.ciccwm_provider import CiccwmDataProvider


class _StubCache:
    """测试用空 cache 桩。"""

    def __init__(self) -> None:
        self.saved_prices: list[pd.DataFrame] = []
        self.saved_financials: list[pd.DataFrame] = []

    def save_prices(self, df: pd.DataFrame) -> None:
        self.saved_prices.append(df)

    def save_financials(self, df: pd.DataFrame) -> None:
        self.saved_financials.append(df)


class TestCiccwmProvider:
    """ciccwm 提供者基础功能测试。"""

    def setup_method(self) -> None:
        self._orig_path = client._CICCWM_CREDENTIAL_PATH
        self._tmpdir = tempfile.mkdtemp()
        client._CICCWM_CREDENTIAL_PATH = Path(self._tmpdir) / "config.json"

    def teardown_method(self) -> None:
        client._CICCWM_CREDENTIAL_PATH = self._orig_path

    def test_is_available_returns_false_when_no_credential(self) -> None:
        """无凭证时 is_available 返回 False。"""
        provider = CiccwmDataProvider(cache=_StubCache())
        assert not provider.is_available

    def test_get_price_panel_empty_on_no_credential(self) -> None:
        """无凭证时 get_price_panel 返回空 DataFrame。"""
        provider = CiccwmDataProvider(cache=_StubCache())
        result = provider.get_price_panel(
            symbols=["600519.SH"],
            start_date="2023-01-01",
            end_date="2023-01-31",
        )
        assert result.empty

    def test_get_financial_panel_empty_on_no_credential(self) -> None:
        """无凭证时 get_financial_panel 返回空 DataFrame。"""
        provider = CiccwmDataProvider(cache=_StubCache())
        result = provider.get_financial_panel(
            symbols=["600519.SH"],
            start_date="2023-01-01",
            end_date="2023-12-31",
        )
        assert result.empty

    def test_get_merged_panel_empty_on_no_credential(self) -> None:
        """无凭证时 get_merged_panel 返回空 DataFrame。"""
        provider = CiccwmDataProvider(cache=_StubCache())
        result = provider.get_merged_panel(
            symbols=["600519.SH"],
            start_date="2023-01-01",
            end_date="2023-12-31",
        )
        assert result.empty

    def test_get_price_panel_empty_symbols(self) -> None:
        """空 symbol 列表返回空 DataFrame。"""
        provider = CiccwmDataProvider(cache=_StubCache())
        result = provider.get_price_panel(
            symbols=[], start_date="2023-01-01", end_date="2023-01-31"
        )
        assert result.empty

    def test_get_financial_panel_empty_symbols(self) -> None:
        """空 symbol 列表返回空 DataFrame。"""
        provider = CiccwmDataProvider(cache=_StubCache())
        result = provider.get_financial_panel(
            symbols=[], start_date="2023-01-01", end_date="2023-12-31"
        )
        assert result.empty


class TestQuarterlyToDaily:
    """季度→日级前向填充逻辑测试（与 miniqmt 版行为一致）。"""

    def _make_provider(self) -> CiccwmDataProvider:
        return CiccwmDataProvider(cache=_StubCache())

    def test_publication_lag_prevents_lookahead(self) -> None:
        """report_date + 60d 之前财务数据不可见（防未来函数）。"""
        provider = self._make_provider()
        quarterly = pd.DataFrame({
            "symbol": ["600519.SH"],
            "report_date": [pd.Timestamp("2023-03-31")],
            "roe": [0.15],
        })
        trading_dates = pd.date_range("2023-04-01", "2023-04-15", freq="B")
        result = provider._quarterly_to_daily(
            quarterly, symbols=["600519.SH"],
            trading_dates=trading_dates, fields=["roe"],
        )
        first_roe = result.xs("600519.SH", level="symbol")["roe"].iloc[0]
        assert pd.isna(first_roe), (
            f"披露窗口期内不应可见，实际 roe={first_roe}"
        )

    def test_visible_after_lag(self) -> None:
        """report_date + 60d 之后财务数据可见。"""
        provider = self._make_provider()
        quarterly = pd.DataFrame({
            "symbol": ["600519.SH"],
            "report_date": [pd.Timestamp("2023-03-31")],
            "roe": [0.15],
        })
        trading_dates = pd.date_range("2023-03-31", "2023-07-31", freq="B")
        result = provider._quarterly_to_daily(
            quarterly, symbols=["600519.SH"],
            trading_dates=trading_dates, fields=["roe"],
        )
        sym = result.xs("600519.SH", level="symbol")["roe"]
        # 披露窗口内不可见
        assert pd.isna(sym.loc[pd.Timestamp("2023-04-14")])
        # 窗口后可见
        assert sym.loc[pd.Timestamp("2023-06-01")] == 0.15

    def test_custom_lag_zero(self) -> None:
        """publication_lag_days=0 时数据即时可见（兼容旧行为）。"""
        provider = self._make_provider()
        quarterly = pd.DataFrame({
            "symbol": ["600519.SH"],
            "report_date": [pd.Timestamp("2023-03-31")],
            "roe": [0.15],
        })
        trading_dates = pd.date_range("2023-04-01", "2023-04-10", freq="B")
        result = provider._quarterly_to_daily(
            quarterly, symbols=["600519.SH"],
            trading_dates=trading_dates, fields=["roe"],
            publication_lag_days=0,
        )
        first_roe = result.xs("600519.SH", level="symbol")["roe"].iloc[0]
        assert first_roe == 0.15


class TestCiccwmExtensionMethods:
    """ciccwm 独占扩展方法测试。"""

    def setup_method(self) -> None:
        self._orig_path = client._CICCWM_CREDENTIAL_PATH
        self._tmpdir = tempfile.mkdtemp()
        client._CICCWM_CREDENTIAL_PATH = Path(self._tmpdir) / "config.json"

    def teardown_method(self) -> None:
        client._CICCWM_CREDENTIAL_PATH = self._orig_path

    def test_get_fund_flow_parse_error(self) -> None:
        """代码格式错误时 get_fund_flow 抛出 ValueError。"""
        provider = CiccwmDataProvider(cache=_StubCache())
        with pytest.raises(ValueError, match="未知的市场后缀"):
            provider.get_fund_flow("12345.XX")

    def test_get_related_blocks_parse_error(self) -> None:
        """代码格式错误时 get_related_blocks 抛出 ValueError。"""
        provider = CiccwmDataProvider(cache=_StubCache())
        with pytest.raises(ValueError, match="未知的市场后缀"):
            provider.get_related_blocks("12345.XX")

    def test_get_ranking_no_credential(self) -> None:
        """无凭证时 get_ranking 抛出 CiccwmCredentialError。"""
        provider = CiccwmDataProvider(cache=_StubCache())
        with pytest.raises(client.CiccwmCredentialError):
            provider.get_ranking()

    def test_get_hot_rank_no_credential(self) -> None:
        """无凭证时 get_hot_rank 抛出 CiccwmCredentialError。"""
        provider = CiccwmDataProvider(cache=_StubCache())
        with pytest.raises(client.CiccwmCredentialError):
            provider.get_hot_rank()

    def test_get_topic_news_no_credential(self) -> None:
        """无凭证时 get_topic_news 抛出 CiccwmCredentialError。"""
        provider = CiccwmDataProvider(cache=_StubCache())
        with pytest.raises(client.CiccwmCredentialError):
            provider.get_topic_news()
