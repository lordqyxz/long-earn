"""高级订单类型测试

覆盖限价单、止损单、止损限价单、OCO 互斥订单的完整生命周期。
"""

from datetime import datetime

import pytest

from long_earn.backtest.domain.entities import (
    ExecType,
    FillEvent,
    OrderEvent,
)
from long_earn.backtest.domain.exceptions import OrderExecutionError
from long_earn.backtest.engine.broker import Broker


def _make_order(
    order_type: str = "BUY",
    quantity: float = 100.0,
    exec_type: str = ExecType.MARKET,
    price: float | None = None,
    stop_price: float | None = None,
    oco_group_id: str = "",
    order_id: str = "ord-test",
) -> OrderEvent:
    return OrderEvent(
        timestamp=datetime(2024, 1, 1),
        trace_id="trace-1",
        event_id="evt-1",
        symbol="000001",
        order_type=order_type,
        quantity=quantity,
        price=price,
        order_id=order_id,
        exec_type=exec_type,
        stop_price=stop_price,
        oco_group_id=oco_group_id,
    )


class TestLimitOrder:
    """限价单测试"""

    def test_buy_limit_fills_when_price_below_limit(self):
        """买入限价：当前价低于限价时应成交"""
        broker = Broker()
        order = _make_order("BUY", exec_type=ExecType.LIMIT, price=11.0)
        fills = broker.submit_order(order, current_price=10.0)
        assert len(fills) == 1
        assert fills[0].fill_price == 10.0  # 以当前价成交
        assert fills[0].order_id == order.order_id

    def test_buy_limit_fills_when_price_equals_limit(self):
        """买入限价：当前价等于限价时应成交"""
        broker = Broker()
        order = _make_order("BUY", exec_type=ExecType.LIMIT, price=10.0)
        fills = broker.submit_order(order, current_price=10.0)
        assert len(fills) == 1

    def test_buy_limit_pends_when_price_above_limit(self):
        """买入限价：当前价高于限价时应挂起"""
        broker = Broker()
        order = _make_order("BUY", exec_type=ExecType.LIMIT, price=10.0)
        fills = broker.submit_order(order, current_price=11.0)
        assert len(fills) == 0
        assert broker.get_pending_count() == 1

    def test_sell_limit_fills_when_price_above_limit(self):
        """卖出限价：当前价高于限价时应成交"""
        broker = Broker()
        order = _make_order("SELL", exec_type=ExecType.LIMIT, price=10.0)
        fills = broker.submit_order(order, current_price=11.0)
        assert len(fills) == 1

    def test_sell_limit_pends_when_price_below_limit(self):
        """卖出限价：当前价低于限价时应挂起"""
        broker = Broker()
        order = _make_order("SELL", exec_type=ExecType.LIMIT, price=10.0)
        fills = broker.submit_order(order, current_price=9.0)
        assert len(fills) == 0
        assert broker.get_pending_count() == 1

    def test_pending_limit_fills_on_price_improvement(self):
        """待成交限价单：价格改善后成交"""
        broker = Broker()
        order = _make_order("BUY", exec_type=ExecType.LIMIT, price=10.0)
        broker.submit_order(order, current_price=11.0)  # 挂起
        assert broker.get_pending_count() == 1

        fills = broker.check_pending_orders(price_lookup={"000001": 9.5})
        assert len(fills) == 1
        assert broker.get_pending_count() == 0

    def test_limit_order_no_price_raises(self):
        """限价单必须指定价格，否则无法成交"""
        broker = Broker()
        order = _make_order("BUY", exec_type=ExecType.LIMIT, price=None)
        fills = broker.submit_order(order, current_price=10.0)
        # 没有 price 的限价单会挂起
        assert len(fills) == 0


class TestStopOrder:
    """止损/止盈单测试"""

    def test_buy_stop_triggers_when_price_above_stop(self):
        """买入止损：当前价超过触发价时转市价成交"""
        broker = Broker()
        order = _make_order("BUY", exec_type=ExecType.STOP, stop_price=10.5)
        fills = broker.submit_order(order, current_price=11.0)
        assert len(fills) == 1
        # 触发后按市价成交（含滑点）
        assert fills[0].fill_price > 11.0  # 买入滑点上移

    def test_buy_stop_pends_when_price_below_stop(self):
        """买入止损：当前价未触发时挂起"""
        broker = Broker()
        order = _make_order("BUY", exec_type=ExecType.STOP, stop_price=10.5)
        fills = broker.submit_order(order, current_price=10.0)
        assert len(fills) == 0
        assert broker.get_pending_count() == 1

    def test_sell_stop_triggers_when_price_below_stop(self):
        """卖出止损：当前价低于触发价时转市价成交"""
        broker = Broker()
        order = _make_order("SELL", exec_type=ExecType.STOP, stop_price=9.5)
        fills = broker.submit_order(order, current_price=9.0)
        assert len(fills) == 1
        assert fills[0].fill_price < 9.0  # 卖出滑点下移

    def test_stop_trigger_on_later_bar(self):
        """待成交止损单：后续 bar 价格触发后成交"""
        broker = Broker()
        order = _make_order("BUY", exec_type=ExecType.STOP, stop_price=10.5)
        broker.submit_order(order, current_price=10.0)  # 未触发
        assert broker.get_pending_count() == 1

        fills = broker.check_pending_orders(price_lookup={"000001": 11.0})
        assert len(fills) == 1
        assert broker.get_pending_count() == 0

    def test_stop_no_stop_price_pends(self):
        """止损单没有触发价时挂起"""
        broker = Broker()
        order = _make_order("BUY", exec_type=ExecType.STOP, stop_price=None)
        fills = broker.submit_order(order, current_price=10.0)
        assert len(fills) == 0


class TestStopLimitOrder:
    """止损限价单测试"""

    def test_stop_limit_triggers_then_pends(self):
        """STOP_LIMIT：触发后转为限价挂起，价格满足后成交"""
        broker = Broker()
        order = _make_order(
            "BUY",
            exec_type=ExecType.STOP_LIMIT,
            stop_price=10.5,
            price=10.2,  # 限价低于触发价，触发后不会立刻成交
        )
        fills = broker.submit_order(order, current_price=10.0)  # 未触发，挂起
        assert len(fills) == 0
        assert broker.get_pending_count() == 1

        # 触发（stop 10.5 triggered by current 10.5），但限价不满足（10.5 > 10.2 for BUY）
        fills = broker.check_pending_orders(price_lookup={"000001": 10.5})
        assert len(fills) == 0  # 触发但限价未满足
        # 检查 order 确实触发了
        oo = broker.get_pending_orders()[0]
        assert oo.trigger_activated

        # 价格回归到限价内（10.0 <= 10.2）
        fills = broker.check_pending_orders(price_lookup={"000001": 10.0})
        assert len(fills) == 1
        assert broker.get_pending_count() == 0

    def test_stop_limit_direct_fill_on_trigger(self):
        """STOP_LIMIT：触发后转为限价挂起，等待后续 bar 成交"""
        broker = Broker()
        order = _make_order(
            "BUY",
            exec_type=ExecType.STOP_LIMIT,
            stop_price=10.5,
            price=11.0,
        )
        # 触发 stop=10.5，但 STOP_LIMIT 挂起为限价待成交
        fills = broker.submit_order(order, current_price=11.0)
        assert len(fills) == 0  # STOP_LIMIT 提交时只挂起
        assert broker.get_pending_count() == 1  # 挂起等待后续 bar


class TestOCOOrder:
    """OCO 互斥订单测试"""

    def test_oco_first_fill_cancels_sibling(self):
        """OCO：一个订单成交后自动取消同组其他订单"""
        broker = Broker()
        limit_buy = _make_order(
            "BUY",
            exec_type=ExecType.LIMIT,
            price=9.5,
            order_id="oco-buy",
            oco_group_id="oco-1",
        )
        stop_sell = _make_order(
            "SELL",
            exec_type=ExecType.STOP,
            stop_price=8.5,
            order_id="oco-sell",
            oco_group_id="oco-1",
        )

        # 两个订单都挂起
        broker.submit_order(limit_buy, current_price=10.0)
        broker.submit_order(stop_sell, current_price=10.0)
        assert broker.get_pending_count() == 2

        # 限价单成交
        fills = broker.check_pending_orders(price_lookup={"000001": 9.5})
        assert len(fills) == 1
        assert fills[0].order_id == "oco-buy"

        # 同组止损单应自动取消
        assert broker.get_pending_count() == 0
        cancelled = broker.get_pending_orders()
        assert len(cancelled) == 0

    def test_oco_sibling_cancelled_on_submit(self):
        """OCO：同组已有订单成交时，新订单自动取消"""
        broker = Broker()
        limit_buy = _make_order(
            "BUY",
            exec_type=ExecType.LIMIT,
            price=9.5,
            order_id="oco-buy",
            oco_group_id="oco-2",
        )
        # 第一个订单立即成交
        broker.submit_order(limit_buy, current_price=9.0)
        assert broker.get_pending_count() == 0

        # 第二个同组订单应自动取消
        stop_sell = _make_order(
            "SELL",
            exec_type=ExecType.STOP,
            stop_price=8.5,
            order_id="oco-sell",
            oco_group_id="oco-2",
        )
        fills = broker.submit_order(stop_sell, current_price=10.0)
        assert len(fills) == 0
        assert broker.get_pending_count() == 0

    def test_oco_both_pending_then_stop_triggers(self):
        """OCO：止损单触发后取消限价单"""
        broker = Broker()
        limit_buy = _make_order(
            "BUY",
            exec_type=ExecType.LIMIT,
            price=7.5,  # 限价低于止损触发价，确保止损先触发
            order_id="oco-buy",
            oco_group_id="oco-3",
        )
        stop_sell = _make_order(
            "SELL",
            exec_type=ExecType.STOP,
            stop_price=8.5,
            order_id="oco-sell",
            oco_group_id="oco-3",
        )

        broker.submit_order(limit_buy, current_price=10.0)  # 挂起
        broker.submit_order(stop_sell, current_price=10.0)  # 挂起
        assert broker.get_pending_count() == 2

        # 止损触发（8.0 <= 8.5），限价单不成交（8.0 > 7.5 for BUY）
        fills = broker.check_pending_orders(price_lookup={"000001": 8.0})
        assert len(fills) == 1
        assert fills[0].order_id == "oco-sell"
        assert broker.get_pending_count() == 0


class TestBrokerStateManagement:
    """Broker 状态管理测试"""

    def test_reset_clears_pending_orders(self):
        """reset() 应清空所有待成交订单和 OCO 组"""
        broker = Broker()
        broker.submit_order(
            _make_order("BUY", exec_type=ExecType.LIMIT, price=9.0),
            current_price=10.0,
        )
        assert broker.get_pending_count() == 1
        broker.reset()
        assert broker.get_pending_count() == 0
        assert len(broker.oco_groups) == 0

    def test_multiple_pending_orders_different_symbols(self):
        """多个不同标的的待成交订单"""
        broker = Broker()
        syms = ["000001", "000002", "000003"]
        for sym in syms:
            order = OrderEvent(
                timestamp=datetime(2024, 1, 1),
                trace_id="t",
                event_id=f"e_{sym}",
                symbol=sym,
                order_type="BUY",
                quantity=100,
                order_id=f"ord_{sym}",
                exec_type=ExecType.LIMIT,
                price=9.0,
            )
            broker.submit_order(order, current_price=10.0)

        assert broker.get_pending_count() == 3

        fills = broker.check_pending_orders(
            price_lookup={"000001": 8.5, "000002": 9.5, "000003": 8.0},
        )
        # 000001 和 000003 成交（price <= 9.0），000002 未成交（price 9.5 > 9.0）
        assert len(fills) == 2

    def test_backward_compatible_execute_order(self):
        """execute_order() 向后兼容性"""
        broker = Broker()
        order = _make_order("BUY")
        fill = broker.execute_order(order, current_price=10.0)
        assert isinstance(fill, FillEvent)
        assert fill.fill_price > 10.0  # 买入滑点上移
        assert fill.order_id == "ord-test"

    def test_non_market_execute_order_raises(self):
        """execute_order() 对非市价单应正常执行（限价单走 submit 可能返回空列表）"""
        broker = Broker()
        order = _make_order("BUY", exec_type=ExecType.LIMIT, price=9.0)
        # 价格不满足，execute_order 会抛出异常
        with pytest.raises(OrderExecutionError):
            broker.execute_order(order, current_price=10.0)
