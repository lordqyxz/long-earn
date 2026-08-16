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
        """成交时计算双边佣金（金额足够大，不触发最低佣金保护）"""
        broker = Broker()
        # 成交金额 > min_commission/commission_rate = 5/0.0003 ≈ 16667，确保按费率计
        order = _make_order("BUY", quantity=1000.0)

        fill = broker.execute_order(order, 20.0)

        expected_turnover = fill.fill_quantity * fill.fill_price
        expected_commission = expected_turnover * broker.cost_config.commission_rate
        assert fill.commission == pytest.approx(expected_commission)
        assert fill.commission > 0

    def test_commission_with_custom_rate(self):
        """自定义佣金费率（金额足够大，不触发最低佣金保护）"""
        config = TradingCostConfig(commission_rate=0.0001)
        broker = Broker(cost_config=config)
        # 金额需 > 5/0.0001 = 50000 才按费率计
        order = _make_order("BUY", quantity=10000.0)

        fill = broker.execute_order(order, 10.0)

        expected_turnover = fill.fill_quantity * fill.fill_price
        expected = expected_turnover * 0.0001
        assert fill.commission == pytest.approx(expected)

    def test_min_commission_floor_applied(self):
        """小订单触发最低佣金保护（A 股 5 元/单行规）"""
        broker = Broker()
        order = _make_order("BUY", quantity=100.0)  # 金额 ~2000，费率佣金 0.6 < 5

        fill = broker.execute_order(order, 20.0)

        assert fill.commission == pytest.approx(broker.cost_config.min_commission)


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


def _make_limit_order(
    order_type: str, limit_price: float, quantity: float = 100.0
) -> OrderEvent:
    """构造测试用限价单"""
    from long_earn.backtest.domain.entities import ExecType

    return OrderEvent(
        timestamp=datetime(2024, 1, 1),
        trace_id="trace-lmt",
        event_id="evt-lmt",
        symbol="000001",
        order_type=order_type,
        quantity=quantity,
        price=limit_price,
        order_id="ord-lmt",
        exec_type=ExecType.LIMIT,
    )


def _make_stop_order(
    order_type: str, stop_price: float, quantity: float = 100.0
) -> OrderEvent:
    """构造测试用止损/止盈单（触发后转市价）"""
    from long_earn.backtest.domain.entities import ExecType

    return OrderEvent(
        timestamp=datetime(2024, 1, 1),
        trace_id="trace-stop",
        event_id="evt-stop",
        symbol="000001",
        order_type=order_type,
        quantity=quantity,
        price=None,
        stop_price=stop_price,
        order_id="ord-stop",
        exec_type=ExecType.STOP,
    )


class TestLimitOrderConservativeFill:
    """限价单保守成交价测试

    防止"用 close 直接成交、且不加滑点"导致回测系统性高估限价策略业绩。
    """

    def test_buy_limit_fill_price_not_below_current_plus_slip(self):
        """BUY LIMIT @ 10，current=8.0：成交价应 ≥ current + slippage（不能白拿 8.0）"""
        broker = Broker(TradingCostConfig(slippage_bps=10))  # 0.001
        order = _make_limit_order("BUY", limit_price=10.0)

        fill = broker.submit_order(order, current_price=8.0)
        assert fill, "应成交"
        # 不应直接拿 current_price 8.0，至少 8.0 + 滑点
        expected_min = 8.0 * (1 + 0.001)
        assert fill[0].fill_price >= expected_min - 1e-9, (
            f"BUY LIMIT 成交价过于乐观: {fill[0].fill_price} < {expected_min}"
        )
        # 仍应 ≤ limit_price（限价单的硬上限）
        assert fill[0].fill_price <= 10.0
        # 滑点字段非零
        assert fill[0].slippage > 0

    def test_sell_limit_fill_price_not_above_current_minus_slip(self):
        """SELL LIMIT @ 10，current=12.0：成交价应 ≤ current - slippage"""
        broker = Broker(TradingCostConfig(slippage_bps=10))  # 0.001
        order = _make_limit_order("SELL", limit_price=10.0)

        fill = broker.submit_order(order, current_price=12.0)
        assert fill, "应成交"
        expected_max = 12.0 * (1 - 0.001)
        assert fill[0].fill_price <= expected_max + 1e-9, (
            f"SELL LIMIT 成交价过于乐观: {fill[0].fill_price} > {expected_max}"
        )
        # 仍应 ≥ limit_price（限价单的硬下限）
        assert fill[0].fill_price >= 10.0
        assert fill[0].slippage > 0

    def test_limit_order_with_zero_slippage_falls_back_to_limit(self):
        """slippage=0 时 BUY LIMIT 成交价就是 limit（保守边界）"""
        broker = Broker(TradingCostConfig(slippage_bps=0))
        order = _make_limit_order("BUY", limit_price=10.0)

        fill = broker.submit_order(order, current_price=8.0)
        assert fill
        # 无滑点时 max(limit, current+0) = max(10, 8) = 10
        assert fill[0].fill_price == 10.0


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


class TestBrokerInvalidInputFailClosed:
    """AUDIT-P3-02：撮合/下单路径对 NaN/Inf/负数/0 价格与数量 fail closed。

    非法数值必须被显式拒绝（结构化原因 / 抛异常），绝不静默吞掉或流入撮合计算。
    拒绝原因走 OrderSkipReason 枚举，保证审计日志 ORDER_SKIPPED 的 reason 结构化、
    可查询、可聚合。
    """

    # ── validate_order_fields：订单自身数值字段 ─────────────────

    @pytest.mark.parametrize(
        "bad_qty", [float("nan"), float("inf"), float("-inf"), 0.0, -100.0]
    )
    def test_invalid_quantity_rejected(self, bad_qty: float):
        """数量 NaN/Inf/0/负数 → INVALID_QUANTITY 结构化原因。"""
        from long_earn.backtest.engine.audit import OrderSkipReason
        from long_earn.backtest.engine.broker import validate_order_fields

        result = validate_order_fields(_make_order("BUY", quantity=bad_qty))
        assert result is not None
        reason, detail = result
        assert reason == OrderSkipReason.INVALID_QUANTITY
        assert str(bad_qty) in detail, "detail 应含具体数值，可追溯"

    @pytest.mark.parametrize(
        "bad_price", [float("nan"), float("inf"), float("-inf"), 0.0, -1.0]
    )
    def test_invalid_limit_price_rejected(self, bad_price: float):
        """限价 NaN/Inf/0/负数 → INVALID_PRICE 结构化原因。"""
        from long_earn.backtest.engine.audit import OrderSkipReason
        from long_earn.backtest.engine.broker import validate_order_fields

        result = validate_order_fields(_make_limit_order("BUY", bad_price))
        assert result is not None
        reason, detail = result
        assert reason == OrderSkipReason.INVALID_PRICE
        assert str(bad_price) in detail, "detail 应含具体数值，可追溯"

    @pytest.mark.parametrize(
        "bad_stop", [float("nan"), float("inf"), float("-inf"), 0.0, -1.0]
    )
    def test_invalid_stop_price_rejected(self, bad_stop: float):
        """止损价 NaN/Inf/0/负数 → INVALID_PRICE 结构化原因。"""
        from long_earn.backtest.engine.audit import OrderSkipReason
        from long_earn.backtest.engine.broker import validate_order_fields

        result = validate_order_fields(_make_stop_order("SELL", bad_stop))
        assert result is not None
        reason, detail = result
        assert reason == OrderSkipReason.INVALID_PRICE
        assert str(bad_stop) in detail, "detail 应含具体数值，可追溯"

    def test_valid_orders_pass_validation(self):
        """正常订单（正数量 + 限价/止损价）通过校验。"""
        from long_earn.backtest.engine.broker import validate_order_fields

        assert validate_order_fields(_make_order("BUY")) is None
        assert validate_order_fields(_make_limit_order("BUY", 10.0)) is None
        assert validate_order_fields(_make_stop_order("SELL", 9.0)) is None

    # ── validate_order_numeric：订单 + 市场价全量校验 ────────────

    @pytest.mark.parametrize(
        "bad_price", [float("nan"), float("inf"), float("-inf"), 0.0, -1.0]
    )
    def test_invalid_current_price_rejected(self, bad_price: float):
        """市场价 NaN/Inf/0/负数 → PRICE_INVALID 结构化原因。"""
        from long_earn.backtest.engine.audit import OrderSkipReason
        from long_earn.backtest.engine.broker import validate_order_numeric

        result = validate_order_numeric(_make_order("BUY"), bad_price)
        assert result is not None
        reason, detail = result
        assert reason == OrderSkipReason.PRICE_INVALID
        assert str(bad_price) in detail, "detail 应含具体数值，可追溯"

    def test_valid_order_with_current_price_passes(self):
        """正常订单 + 有效市场价通过全量校验。"""
        from long_earn.backtest.engine.broker import validate_order_numeric

        assert validate_order_numeric(_make_order("BUY"), 10.0) is None

    # ── Broker 入口 fail closed ────────────────────────────────

    @pytest.mark.parametrize("bad_qty", [float("nan"), float("inf"), 0.0, -100.0])
    def test_submit_order_raises_on_invalid_quantity(self, bad_qty: float):
        """Broker 收到非法数量必须抛异常（fail closed），不得静默成交。"""
        from long_earn.backtest.domain.exceptions import OrderExecutionError

        broker = Broker()
        with pytest.raises(OrderExecutionError):
            broker.execute_order(_make_order("BUY", quantity=bad_qty), 10.0)

    @pytest.mark.parametrize("bad_price", [float("nan"), float("inf"), 0.0, -1.0])
    def test_submit_order_raises_on_invalid_current_price(self, bad_price: float):
        """Broker 收到非法市场价必须抛异常（fail closed），不得让 NaN 流入撮合。"""
        from long_earn.backtest.domain.exceptions import OrderExecutionError

        broker = Broker()
        with pytest.raises(OrderExecutionError):
            broker.execute_order(_make_order("BUY"), bad_price)

    def test_submit_order_raises_on_invalid_limit_price(self):
        """限价单非法限价必须抛异常。"""
        from long_earn.backtest.domain.exceptions import OrderExecutionError

        broker = Broker()
        with pytest.raises(OrderExecutionError):
            broker.submit_order(_make_limit_order("BUY", float("nan")), 10.0)

    # ── 待成交订单：非法行情价仅跳过本 bar，订单保留 ─────────────

    def test_pending_order_skips_bar_with_invalid_price(self):
        """check_pending_orders：NaN 行情价本 bar 跳过撮合，订单仍待成交（非拒单）。"""
        from long_earn.backtest.domain.entities import ExecType

        broker = Broker()
        order = OrderEvent(
            timestamp=datetime(2024, 1, 1),
            trace_id="trace-pend",
            event_id="evt-pend",
            symbol="000001",
            order_type="BUY",
            quantity=100.0,
            price=10.0,
            order_id="ord-pend",
            exec_type=ExecType.LIMIT,
        )
        # BUY LIMIT @10、current=12：未触发，进入待成交
        assert broker.submit_order(order, current_price=12.0) == []
        assert broker.get_pending_count() == 1

        # NaN 行情价：本 bar 跳过撮合，订单保留（延迟而非拒单，杜绝 NaN 成交价）
        fills = broker.check_pending_orders(price_lookup={"000001": float("nan")})
        assert fills == []
        assert broker.get_pending_count() == 1
