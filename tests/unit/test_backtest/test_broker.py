"""Broker 撮合精度测试

测试滑点方向、佣金计算、印花税计算及 FillEvent 字段完整性。
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
        price=None,  # 市价单
        order_id="ord-1",
    )


class TestBrokerSlippage:
    """滑点方向正确性测试"""

    def test_buy_slippage_is_upward(self):
        """买入时应向上滑点（成交价 > 当前价）"""
        broker = Broker()
        order = _make_order("BUY")
        current_price = 10.0

        fill = broker.execute_order(order, current_price)

        expected_slippage = 1 + broker.cost_config.slippage_rate
        assert fill.fill_price == pytest.approx(current_price * expected_slippage)
        assert fill.fill_price > current_price

    def test_sell_slippage_is_downward(self):
        """卖出时应向下滑点（成交价 < 当前价）"""
        broker = Broker()
        order = _make_order("SELL")
        current_price = 10.0

        fill = broker.execute_order(order, current_price)

        expected_slippage = 1 - broker.cost_config.slippage_rate
        assert fill.fill_price == pytest.approx(current_price * expected_slippage)
        assert fill.fill_price < current_price

    def test_slippage_direction_with_custom_config(self):
        """自定义滑点配置后方向仍然正确"""
        config = TradingCostConfig(slippage_bps=5.0)  # 5bps 滑点
        broker = Broker(cost_config=config)
        current_price = 50.0

        buy_fill = broker.execute_order(_make_order("BUY"), current_price)
        sell_fill = broker.execute_order(_make_order("SELL"), current_price)

        assert buy_fill.fill_price > current_price
        assert sell_fill.fill_price < current_price
        assert buy_fill.fill_price == pytest.approx(
            current_price * (1 + config.slippage_rate)
        )
        assert sell_fill.fill_price == pytest.approx(
            current_price * (1 - config.slippage_rate)
        )


class TestBrokerCommission:
    """佣金计算测试"""

    def test_commission_on_buy(self):
        """买入时计算双边佣金（成交金额 * 万三）"""
        broker = Broker()
        order = _make_order("BUY", quantity=100.0)
        current_price = 20.0

        fill = broker.execute_order(order, current_price)

        # 成交金额 = 100 * 20 * (1 + slippage) ≈ 2000
        expected_turnover = fill.fill_quantity * fill.fill_price
        expected_commission = expected_turnover * broker.cost_config.commission_rate
        assert fill.commission == pytest.approx(expected_commission)
        assert fill.commission > 0

    def test_commission_on_sell(self):
        """卖出时也计算双边佣金"""
        broker = Broker()
        order = _make_order("SELL", quantity=200.0)
        current_price = 15.0

        fill = broker.execute_order(order, current_price)

        expected_turnover = fill.fill_quantity * fill.fill_price
        expected_commission = expected_turnover * broker.cost_config.commission_rate
        assert fill.commission == pytest.approx(expected_commission)
        assert fill.commission > 0

    def test_commission_with_custom_rate(self):
        """自定义佣金费率"""
        config = TradingCostConfig(commission_rate=0.0001)  # 万一
        broker = Broker(cost_config=config)
        order = _make_order("BUY", quantity=100.0)

        fill = broker.execute_order(order, 10.0)

        expected_turnover = fill.fill_quantity * fill.fill_price
        expected = expected_turnover * 0.0001
        assert fill.commission == pytest.approx(expected)


class TestBrokerStampDuty:
    """印花税测试（仅卖出征收万五）"""

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
    """FillEvent 字段完整性测试"""

    def test_fill_event_references_order(self):
        """成交事件应引用原始订单"""
        broker = Broker()
        order = _make_order("BUY")

        fill = broker.execute_order(order, 10.0)

        assert fill.order_id == order.order_id
        assert fill.symbol == order.symbol
        assert fill.order_type == order.order_type
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

    def test_fill_event_timestamps_preserved(self):
        """成交事件的时间戳应与订单一致"""
        broker = Broker()
        ts = datetime(2024, 6, 15, 10, 30)
        order = OrderEvent(
            timestamp=ts,
            trace_id="trace-ts",
            event_id="evt-ts",
            symbol="000001",
            order_type="BUY",
            quantity=50.0,
            order_id="ord-ts",
        )

        fill = broker.execute_order(order, 10.0)

        assert fill.timestamp == ts

    def test_fill_event_has_unique_trace_id(self):
        """每次执行应生成唯一的 trace_id"""
        broker = Broker()
        order = _make_order("BUY")

        fill1 = broker.execute_order(order, 10.0)
        fill2 = broker.execute_order(order, 10.0)

        assert fill1.trace_id != fill2.trace_id
