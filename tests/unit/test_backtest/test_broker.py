"""Broker 撮合接口测试

测试滑点方向、佣金计算、印花税计算及 FillEvent 字段完整性。
合并重复测试，聚焦接口契约。
"""

from datetime import datetime

import pytest

from long_earn.backtest.domain.entities import FillEvent, OrderEvent
from long_earn.backtest.engine.broker import Broker, TradingCostConfig


def _make_order(order_type: str, quantity: float = 100.0) -> OrderEvent:
    return OrderEvent(
        timestamp=datetime(2024, 1, 1),
        trace_id="trace-1",
        event_id="evt-1",
        symbol="000001",
        order_type=order_type,
        quantity=quantity,
        price=None,
        order_id="ord-1",
    )


class TestBrokerSlippage:
    """滑点方向接口测试"""

    def test_buy_slippage_is_upward(self):
        """买入时应向上滑点（成交价 > 当前价）"""
        broker = Broker()
        order = _make_order("BUY")
        current_price = 10.0

        fill = broker.execute_order(order, current_price)

        assert fill.fill_price > current_price

    def test_sell_slippage_is_downward(self):
        """卖出时应向下滑点（成交价 < 当前价）"""
        broker = Broker()
        order = _make_order("SELL")
        current_price = 10.0

        fill = broker.execute_order(order, current_price)

        assert fill.fill_price < current_price

    def test_slippage_with_custom_config(self):
        """自定义滑点配置后方向仍然正确"""
        config = TradingCostConfig(slippage_bps=5.0)
        broker = Broker(cost_config=config)
        current_price = 50.0

        buy_fill = broker.execute_order(_make_order("BUY"), current_price)
        sell_fill = broker.execute_order(_make_order("SELL"), current_price)

        assert buy_fill.fill_price > current_price
        assert sell_fill.fill_price < current_price


class TestBrokerCommission:
    """佣金计算接口测试"""

    def test_commission_calculated_on_fill(self):
        """成交时计算双边佣金"""
        broker = Broker()
        order = _make_order("BUY", quantity=100.0)

        fill = broker.execute_order(order, 20.0)

        expected_turnover = fill.fill_quantity * fill.fill_price
        expected_commission = expected_turnover * broker.cost_config.commission_rate
        assert fill.commission == pytest.approx(expected_commission)
        assert fill.commission > 0

    def test_commission_with_custom_rate(self):
        """自定义佣金费率"""
        config = TradingCostConfig(commission_rate=0.0001)
        broker = Broker(cost_config=config)
        order = _make_order("BUY", quantity=100.0)

        fill = broker.execute_order(order, 10.0)

        expected_turnover = fill.fill_quantity * fill.fill_price
        expected = expected_turnover * 0.0001
        assert fill.commission == pytest.approx(expected)


class TestBrokerStampDuty:
    """印花税接口测试（仅卖出征收万五）"""

    def test_stamp_duty_on_sell(self):
        """卖出时征收印花税"""
        broker = Broker()
        order = _make_order("SELL", quantity=100.0)

        fill = broker.execute_order(order, 10.0)

        expected_turnover = fill.fill_quantity * fill.fill_price
        expected_stamp = expected_turnover * broker.cost_config.stamp_duty
        assert fill.stamp_duty == pytest.approx(expected_stamp)
        assert fill.stamp_duty > 0

    def test_no_stamp_duty_on_buy(self):
        """买入时不征收印花税"""
        broker = Broker()
        order = _make_order("BUY", quantity=100.0)

        fill = broker.execute_order(order, 10.0)

        assert fill.stamp_duty == 0.0


class TestFillEventIntegrity:
    """FillEvent 字段完整性接口测试"""

    def test_fill_event_references_order(self):
        """成交事件应引用原始订单"""
        broker = Broker()
        order = _make_order("BUY")

        fill = broker.execute_order(order, 10.0)

        assert fill.order_id == order.order_id
        assert fill.symbol == order.symbol
        assert fill.fill_quantity == order.quantity

    def test_fill_event_has_financial_fields(self):
        """成交事件应包含完整的财务字段"""
        broker = Broker()
        order = _make_order("SELL", quantity=500.0)

        fill = broker.execute_order(order, 25.5)

        assert isinstance(fill, FillEvent)
        assert fill.fill_price > 0
        assert fill.fill_quantity > 0
        assert fill.commission >= 0
        assert fill.slippage >= 0
        assert fill.stamp_duty >= 0
