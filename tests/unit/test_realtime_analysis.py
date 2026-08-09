"""实时行情 + 价格告警 + 资金流向分析师测试（ADR-011）。

接口契约层测试：验证 Provider 降级链、告警触发、资金流向分析师容错。
mock 数据源，不依赖真实 xtquant/ciccwm。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from long_earn.backtest.data.realtime import (
    CiccwmRealtimeProvider,
    CompositeRealtimeProvider,
    MiniQmtRealtimeProvider,
    RealtimeDataProvider,
)
from long_earn.monitoring.realtime_alert import PriceAlert, PriceAlertMonitor

# ── 实时行情 Provider ──────────────────────────────────────────────────
#
# RealtimeDataProvider 契约套：面向接口参数化测试。
# 所有实现共用同一套契约测试，新增实现只需添加 provider 实例到参数列表。


def _make_ciccwm_provider() -> CiccwmRealtimeProvider:
    """构造 CiccwmRealtimeProvider（凭证不可用状态）。"""
    provider = CiccwmRealtimeProvider()
    provider._available = False
    return provider


def _make_miniqmt_provider_unavailable() -> MiniQmtRealtimeProvider:
    """构造 MiniQmtRealtimeProvider（xtquant 不可用状态）。"""
    with patch("long_earn.backtest.data.realtime.MiniQmtClient") as mock_client:
        mock_instance = mock_client.get.return_value
        mock_instance.is_available = False
        return MiniQmtRealtimeProvider()


@pytest.fixture(params=["ciccwm", "miniqmt"])
def unavailable_provider(request: pytest.FixtureRequest) -> RealtimeDataProvider:
    """参数化：返回不可用状态的 RealtimeDataProvider 实例。"""
    if request.param == "ciccwm":
        return _make_ciccwm_provider()
    return _make_miniqmt_provider_unavailable()


class TestRealtimeProviderContract:
    """RealtimeDataProvider 接口契约：所有实现共用的参数化测试套。

    新增 RealtimeDataProvider 实现时，只需在 fixture 中添加一个分支即可
    自动继承本套契约测试。
    """

    def test_unavailable_returns_empty_quote(
        self, unavailable_provider: RealtimeDataProvider
    ) -> None:
        """不可用时 get_latest_quote 返回空 dict。"""
        assert unavailable_provider.get_latest_quote("600519.SH") == {}

    def test_is_available_returns_false(
        self, unavailable_provider: RealtimeDataProvider
    ) -> None:
        """不可用时 is_available 返回 False。"""
        assert unavailable_provider.is_available is False

    def test_unsubscribe_is_noop(
        self, unavailable_provider: RealtimeDataProvider
    ) -> None:
        """unsubscribe 是空操作，不抛异常。"""
        unavailable_provider.unsubscribe("fake_id")  # 不应抛异常

    def test_subscribe_unavailable_returns_empty(
        self, unavailable_provider: RealtimeDataProvider
    ) -> None:
        """不可用时 subscribe_quote 返回空 ID。"""
        assert unavailable_provider.subscribe_quote(["600519.SH"], lambda _d: None) == ""


class TestCompositeRealtimeProvider:
    """组合实时行情提供者降级链。"""

    def test_falls_back_to_ciccwm(self) -> None:
        """miniqmt 不可用时降级到 ciccwm。"""
        composite = CompositeRealtimeProvider()
        # mock miniqmt 不可用
        composite._miniqmt = MagicMock(spec=MiniQmtRealtimeProvider)
        composite._miniqmt.is_available = False
        # mock ciccwm 可用并返回数据
        composite._ciccwm = MagicMock(spec=CiccwmRealtimeProvider)
        composite._ciccwm.is_available = True
        composite._ciccwm.get_latest_quote.return_value = {
            "price": 1800.0,
            "source": "ciccwm",
        }
        result = composite.get_latest_quote("600519.SH")
        assert result["price"] == 1800.0
        assert result["source"] == "ciccwm"

    def test_all_unavailable_returns_empty(self) -> None:
        """所有源不可用时返回空 dict。"""
        composite = CompositeRealtimeProvider()
        composite._miniqmt = MagicMock(spec=MiniQmtRealtimeProvider)
        composite._miniqmt.is_available = False
        composite._ciccwm = MagicMock(spec=CiccwmRealtimeProvider)
        composite._ciccwm.is_available = False
        assert composite.get_latest_quote("600519.SH") == {}


# ── 价格告警监控器 ─────────────────────────────────────────────────────


class TestPriceAlertMonitor:
    """价格阈值告警触发逻辑。"""

    def _make_mock_provider(self) -> MagicMock:
        provider = MagicMock()
        provider.subscribe_quote.return_value = "sub_123"
        return provider

    def test_add_alert_above(self) -> None:
        """添加 above 告警规则。"""
        monitor = PriceAlertMonitor(self._make_mock_provider())
        monitor.add_alert("600519.SH", 1800.0, "above")
        assert len(monitor.alerts) == 1
        assert monitor.alerts[0].direction == "above"

    def test_add_alert_invalid_direction_raises(self) -> None:
        """非法 direction 抛 ValueError。"""
        monitor = PriceAlertMonitor(self._make_mock_provider())
        with pytest.raises(ValueError, match="direction"):
            monitor.add_alert("600519.SH", 1800.0, "sideways")

    def test_trigger_above(self) -> None:
        """价格突破上限时触发告警。"""
        monitor = PriceAlertMonitor(self._make_mock_provider())
        monitor.add_alert("600519.SH", 1800.0, "above")
        triggered: list[PriceAlert] = []
        monitor.on_trigger = triggered.append
        # 模拟 tick 回调
        monitor._handle_tick({"symbol": "600519.SH", "price": 1850.0})
        assert len(triggered) == 1
        assert triggered[0].triggered is True

    def test_no_trigger_below_threshold(self) -> None:
        """价格未达阈值时不触发。"""
        monitor = PriceAlertMonitor(self._make_mock_provider())
        monitor.add_alert("600519.SH", 1800.0, "above")
        triggered: list[PriceAlert] = []
        monitor.on_trigger = triggered.append
        monitor._handle_tick({"symbol": "600519.SH", "price": 1750.0})
        assert len(triggered) == 0

    def test_trigger_below(self) -> None:
        """价格跌破下限时触发告警。"""
        monitor = PriceAlertMonitor(self._make_mock_provider())
        monitor.add_alert("600519.SH", 1800.0, "below")
        triggered: list[PriceAlert] = []
        monitor.on_trigger = triggered.append
        monitor._handle_tick({"symbol": "600519.SH", "price": 1750.0})
        assert len(triggered) == 1

    def test_start_with_no_alerts_returns_empty(self) -> None:
        """无告警规则时 start 返回空 ID。"""
        monitor = PriceAlertMonitor(self._make_mock_provider())
        assert monitor.start() == ""

    def test_start_subscribes_symbols(self) -> None:
        """start 订阅所有告警涉及的股票。"""
        provider = self._make_mock_provider()
        monitor = PriceAlertMonitor(provider)
        monitor.add_alert("600519.SH", 1800.0)
        monitor.add_alert("000001.SZ", 15.0)
        sub_id = monitor.start()
        assert sub_id == "sub_123"
        # subscribe_quote 被调用，symbols 含两只股票
        call_args = provider.subscribe_quote.call_args
        symbols = call_args[0][0]
        assert "600519.SH" in symbols
        assert "000001.SZ" in symbols


# ── 资金流向分析师 ─────────────────────────────────────────────────────


class TestFundFlowAnalyst:
    """资金流向分析师接口契约 + 容错。"""

    def _make_context(
        self,
        market_intelligence: object | None = None,
    ) -> MagicMock:
        ctx = MagicMock()
        ctx.market_intelligence = market_intelligence
        ctx.logger = MagicMock()
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = MagicMock(content="分析结果")
        mock_llm_service = MagicMock()
        mock_llm_service.get_llm.return_value = mock_llm
        ctx.require_llm.return_value = mock_llm_service
        return ctx

    def test_market_intelligence_none_returns_placeholder(self) -> None:
        """ciccwm 不可用时返回占位文本，不抛异常。"""
        from long_earn.stock_analysis.agents.fund_flow_analyst import FundFlowAnalyst

        ctx = self._make_context(market_intelligence=None)
        analyst = FundFlowAnalyst(ctx)
        result = analyst.analyze({"stock_info": {"symbol": "600519.SH"}})
        assert isinstance(result, str)

    def test_fetch_fund_flow_with_mi(self) -> None:
        """market_intelligence 可用时 fetch_fund_flow 调用接口。"""
        from long_earn.stock_analysis.agents.fund_flow_analyst import FundFlowAnalyst

        mi = MagicMock()
        mi.get_fund_flow.return_value = pd.DataFrame({"net_inflow": [1000000, 2000000]})
        ctx = self._make_context(market_intelligence=mi)
        analyst = FundFlowAnalyst(ctx)
        df = analyst.fetch_fund_flow("600519.SH")
        assert not df.empty
        mi.get_fund_flow.assert_called_once_with("600519.SH")

    def test_fetch_fund_flow_swallows_exceptions(self) -> None:
        """接口异常时返回空 DataFrame，不抛异常。"""
        from long_earn.stock_analysis.agents.fund_flow_analyst import FundFlowAnalyst

        mi = MagicMock()
        mi.get_fund_flow.side_effect = RuntimeError("network error")
        ctx = self._make_context(market_intelligence=mi)
        analyst = FundFlowAnalyst(ctx)
        df = analyst.fetch_fund_flow("600519.SH")
        assert df.empty
