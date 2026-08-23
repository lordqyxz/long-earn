"""确定性事件 ID 与 trace_id 因果链贯穿的契约测试。

trace_id 语义（entities.py Event 注释）：贯穿 信号 -> 订单 -> 成交 的唯一 ID。
日线级回测每个交易日一条 bar，时间戳即天然唯一键——bar_trace_id 确定性派生，
保证同数据同策略两次回测审计轨迹一致。
"""

from datetime import datetime

from long_earn.backtest.domain.entities import OrderEvent, SignalEvent
from long_earn.backtest.engine.broker import Broker
from long_earn.backtest.engine.portfolio import Portfolio


def _make_slab(symbols_and_prices: dict[str, float]):
    import polars as pl

    rows = [
        {"symbol": sym, "close": price} for sym, price in symbols_and_prices.items()
    ]
    return pl.DataFrame(rows)


def test_bar_trace_id_deterministic() -> None:
    """同一天的时间戳派生同一 trace_id，跨天不撞。"""
    from long_earn.backtest.domain.entities import bar_trace_id

    assert bar_trace_id(datetime(2024, 1, 5)) == "trace_20240105"
    assert bar_trace_id(datetime(2024, 1, 5, 15, 0, 0)) == "trace_20240105"
    assert bar_trace_id(datetime(2024, 1, 8)) == "trace_20240108"


def test_order_inherits_signal_trace_id() -> None:
    """Portfolio 生成的订单继承信号 trace_id，order_id 确定性派生。"""
    portfolio = Portfolio(initial_capital=1_000_000.0)
    signal = SignalEvent(
        timestamp=datetime(2024, 1, 5),
        trace_id="trace_20240105",
        event_id="op_2024-01-05T00:00:00",
        signals={"000001": 0.5},
        strategy_id="test-strategy",
    )

    orders = portfolio.process_signal(signal, _make_slab({"000001": 10.0}))

    assert orders, "测试前提：至少生成一笔订单"
    for order in orders:
        assert isinstance(order, OrderEvent)
        assert order.trace_id == signal.trace_id, "订单必须继承信号 trace_id"
        assert order.order_id == f"ord_{signal.event_id}_{order.symbol}"


def test_fill_inherits_order_trace_id() -> None:
    """Broker 成交继承订单 trace_id；event_id 带日期防跨日部分成交撞名。"""
    broker = Broker()
    order = OrderEvent(
        timestamp=datetime(2024, 1, 8),
        trace_id="trace_20240105",
        event_id="ord_op_2024-01-05T00:00:00_000001",
        symbol="000001",
        order_type="BUY",
        quantity=1000.0,
        price=None,
        order_id="ord_op_2024-01-05T00:00:00_000001",
    )

    fill = broker.execute_order(order, 10.0)

    assert fill.trace_id == order.trace_id, "成交必须继承订单 trace_id"
    assert fill.event_id == (f"fill_{order.order_id}_{order.timestamp:%Y%m%d}"), (
        "成交 event_id 应带成交日期，防跨日部分成交撞名"
    )
