"""RealtimeDataProvider + PriceAlert 接口契约测试

覆盖：
1. MiniQmtRealtimeProvider：xtquant 不可用时返回空值不抛
2. CiccwmRealtimeProvider：is_available / get_latest_quote / 不支持订阅
3. CompositeRealtimeProvider：miniqmt → ciccwm 降级
4. PriceAlert：above/below 阈值触发 / 无效参数 / 无法获取价格
5. check_alerts 批量检查
"""

from unittest.mock import MagicMock, patch

import pytest

from long_earn.backtest.data.realtime import (
    CiccwmRealtimeProvider,
    CompositeRealtimeProvider,
    MiniQmtRealtimeProvider,
)
from long_earn.monitoring.realtime_alert import PriceAlert, check_alerts

# ── MiniQmtRealtimeProvider ──────────────────────────────────────────


class TestMiniQmtRealtimeProvider:
    """xtquant 不可用时的容错路径（CI 默认环境）"""

    def test_unavailable_returns_empty_quote(self):
        """xtquant 不可用时 get_latest_quote 返回空 dict 不抛"""
        provider = MiniQmtRealtimeProvider()
        if not provider.is_available:
            assert provider.get_latest_quote("600519.SH") == {}

    def test_unavailable_subscribe_returns_empty(self):
        """xtquant 不可用时 subscribe_quote 返回空 ID"""
        provider = MiniQmtRealtimeProvider()
        if not provider.is_available:
            assert provider.subscribe_quote(["600519.SH"]) == ""

    def test_unavailable_unsubscribe_noop(self):
        """xtquant 不可用时 unsubscribe 不抛异常"""
        provider = MiniQmtRealtimeProvider()
        provider.unsubscribe("nonexistent")  # 不抛


# ── CiccwmRealtimeProvider ──────────────────────────────────────────


class TestCiccwmRealtimeProvider:
    """ciccwm HTTP 轮询模式"""

    def test_subscribe_returns_empty(self):
        """ciccwm 不支持订阅，返回空 ID"""
        provider = CiccwmRealtimeProvider()
        assert provider.subscribe_quote(["600519.SH"]) == ""

    def test_unsubscribe_noop(self):
        """ciccwm unsubscribe 不抛"""
        provider = CiccwmRealtimeProvider()
        provider.unsubscribe("any")

    def test_get_latest_quote_with_mock_provider(self):
        """mock CiccwmDataProvider 验证字段映射"""
        provider = CiccwmRealtimeProvider()
        mock_dp = MagicMock()
        mock_dp.is_available = True
        mock_dp.get_info.return_value = {
            "code": "600519",
            "price": 1820.5,
            "volume": 12345,
            "time": "2026-06-22 10:30:00",
            "open": 1800.0,
            "high": 1830.0,
            "low": 1790.0,
            "preClose": 1810.0,
        }
        provider._provider = mock_dp

        quote = provider.get_latest_quote("600519.SH")
        assert quote["price"] == 1820.5
        assert quote["volume"] == 12345
        assert quote["source"] == "ciccwm"

    def test_get_latest_quote_provider_unavailable(self):
        """ciccwm 不可用时返回空 dict"""
        provider = CiccwmRealtimeProvider()
        provider._provider = None
        with patch.object(CiccwmRealtimeProvider, "is_available", False):
            assert provider.get_latest_quote("600519.SH") == {}


# ── CompositeRealtimeProvider ───────────────────────────────────────


class TestCompositeRealtimeProvider:
    """miniqmt → ciccwm 降级链"""

    def test_degrades_to_ciccwm_when_minqmt_unavailable(self):
        """miniqmt 不可用时降级到 ciccwm"""
        composite = CompositeRealtimeProvider()
        mock_mq = MagicMock()
        mock_mq.is_available = False
        mock_cc = MagicMock()
        mock_cc.is_available = True
        mock_cc.get_latest_quote.return_value = {"price": 100.0, "source": "ciccwm"}
        composite._miniqmt = mock_mq
        composite._ciccwm = mock_cc

        result = composite.get_latest_quote("600519.SH")
        assert result["source"] == "ciccwm"
        assert result["price"] == 100.0

    def test_uses_minqmt_when_available(self):
        """miniqmt 可用时优先使用"""
        composite = CompositeRealtimeProvider()
        mock_mq = MagicMock()
        mock_mq.is_available = True
        mock_mq.get_latest_quote.return_value = {"price": 200.0, "source": "miniqmt"}
        mock_cc = MagicMock()
        mock_cc.is_available = True
        composite._miniqmt = mock_mq
        composite._ciccwm = mock_cc

        result = composite.get_latest_quote("600519.SH")
        assert result["source"] == "miniqmt"
        mock_cc.get_latest_quote.assert_not_called()

    def test_all_unavailable_returns_empty(self):
        """全不可用时返回空 dict 不抛"""
        composite = CompositeRealtimeProvider()
        mock_mq = MagicMock()
        mock_mq.is_available = False
        mock_cc = MagicMock()
        mock_cc.is_available = False
        composite._miniqmt = mock_mq
        composite._ciccwm = mock_cc

        assert composite.get_latest_quote("600519.SH") == {}


# ── PriceAlert ───────────────────────────────────────────────────────


def _mock_provider(price: float = 100.0) -> MagicMock:
    """构造返回指定价格的 mock provider"""
    p = MagicMock()
    p.get_latest_quote.return_value = {
        "price": price,
        "volume": 1000,
        "time": "2026-06-22",
        "source": "mock",
    }
    return p


class TestPriceAlert:
    """价格阈值预警"""

    def test_above_triggered(self):
        """价格 >= 阈值 → 触发"""
        alert = PriceAlert(symbol="600519.SH", threshold=100.0, direction="above")
        result = alert.check(_mock_provider(price=105.0))
        assert result["triggered"] is True

    def test_above_not_triggered(self):
        """价格 < 阈值 → 未触发"""
        alert = PriceAlert(symbol="600519.SH", threshold=100.0, direction="above")
        result = alert.check(_mock_provider(price=95.0))
        assert result["triggered"] is False

    def test_below_triggered(self):
        """价格 <= 阈值 → 触发"""
        alert = PriceAlert(symbol="600519.SH", threshold=100.0, direction="below")
        result = alert.check(_mock_provider(price=95.0))
        assert result["triggered"] is True

    def test_below_not_triggered(self):
        """价格 > 阈值 → 未触发"""
        alert = PriceAlert(symbol="600519.SH", threshold=100.0, direction="below")
        result = alert.check(_mock_provider(price=105.0))
        assert result["triggered"] is False

    def test_invalid_direction_raises(self):
        """无效 direction 应抛 ValueError"""
        with pytest.raises(ValueError, match="direction"):
            PriceAlert(symbol="600519.SH", threshold=100.0, direction="invalid")

    def test_zero_threshold_raises(self):
        """阈值 <= 0 应抛 ValueError"""
        with pytest.raises(ValueError, match="threshold"):
            PriceAlert(symbol="600519.SH", threshold=0.0)

    def test_no_price_returns_not_triggered(self):
        """无法获取有效价格时返回未触发 + 错误消息"""
        alert = PriceAlert(symbol="600519.SH", threshold=100.0)
        provider = MagicMock()
        provider.get_latest_quote.return_value = {}  # 无 price 字段
        result = alert.check(provider)
        assert result["triggered"] is False
        assert "无法获取" in result["message"]

    def test_check_alerts_batch(self):
        """批量检查多个预警"""
        alerts = [
            PriceAlert("600519.SH", 100.0, "above"),
            PriceAlert("000001.SZ", 50.0, "below"),
        ]
        results = check_alerts(alerts, _mock_provider(price=120.0))
        assert len(results) == 2
        assert results[0]["triggered"] is True  # 120 >= 100
        assert results[1]["triggered"] is False  # 120 > 50，below 不触发
