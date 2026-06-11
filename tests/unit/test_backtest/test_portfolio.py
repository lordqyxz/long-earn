"""Portfolio 组合管理接口测试

测试信号转订单、成交后持仓更新、市值更新及最大持仓限制。
不测试内部辅助方法（trade_count, equity_curve）。
"""

from datetime import datetime

import polars as pl
import pytest

from long_earn.backtest.domain.entities import FillEvent, Position, SignalEvent
from long_earn.backtest.engine.portfolio import Portfolio


def _make_slab(symbols_and_prices: dict[str, float]) -> pl.DataFrame:
    """构造当前时刻截面数据"""
    rows = []
    for sym, price in symbols_and_prices.items():
        rows.append({"symbol": sym, "close": price})
    return pl.DataFrame(rows)


def _make_signal(weights: dict[str, float], ts: datetime | None = None) -> SignalEvent:
    """构造信号事件"""
    if ts is None:
        ts = datetime(2024, 1, 1)
    return SignalEvent(
        timestamp=ts,
        trace_id="sig-trace",
        event_id="sig-1",
        signals=weights,
        strategy_id="test-strategy",
    )


def _make_fill(
    symbol: str,
    order_type: str,
    quantity: float,
    fill_price: float,
    commission: float = 0.0,
    stamp_duty: float = 0.0,
    ts: datetime | None = None,
) -> FillEvent:
    """构造成交事件"""
    if ts is None:
        ts = datetime(2024, 1, 1)
    return FillEvent(
        timestamp=ts,
        trace_id="fill-trace",
        event_id=f"fill_{symbol}",
        order_id=f"ord_{symbol}",
        symbol=symbol,
        order_type=order_type,
        fill_price=fill_price,
        fill_quantity=quantity,
        commission=commission,
        slippage=0.0,
        stamp_duty=stamp_duty,
    )


class TestSignalToOrder:
    """信号转订单接口测试"""

    def test_single_weight_generates_buy_order(self):
        """单一权重信号生成买入订单"""
        portfolio = Portfolio(initial_capital=1_000_000.0)
        slab = _make_slab({"000001": 10.0})
        signal = _make_signal({"000001": 0.5})

        orders = portfolio.process_signal(signal, slab)

        assert len(orders) == 1
        assert orders[0].symbol == "000001"
        assert orders[0].order_type == "BUY"
        assert orders[0].quantity == pytest.approx(50000.0, rel=1e-6)

    def test_multi_weight_generates_multiple_orders(self):
        """多个权重信号生成多个订单"""
        portfolio = Portfolio(initial_capital=1_000_000.0)
        slab = _make_slab({"000001": 10.0, "000002": 20.0})
        signal = _make_signal({"000001": 0.3, "000002": 0.2})

        orders = portfolio.process_signal(signal, slab)

        assert len(orders) == 2
        symbols = {o.symbol for o in orders}
        assert symbols == {"000001", "000002"}

    def test_zero_weight_skipped(self):
        """权重 <= 0 的信号被跳过"""
        portfolio = Portfolio(initial_capital=1_000_000.0)
        slab = _make_slab({"000001": 10.0, "000002": 20.0})
        signal = _make_signal({"000001": 0.5, "000002": 0.0})

        orders = portfolio.process_signal(signal, slab)

        assert len(orders) == 1
        assert orders[0].symbol == "000001"

    def test_rebalance_generates_sell_order(self):
        """减仓时生成卖出订单"""
        portfolio = Portfolio(initial_capital=1_000_000.0)
        portfolio.positions["000001"] = Position(
            symbol="000001", shares=50000.0, avg_cost=10.0
        )
        portfolio.positions["000001"].update_market_value(11.0)
        portfolio.cash = 500_000.0
        portfolio.total_value = portfolio.cash + portfolio.positions["000001"].market_value

        slab = _make_slab({"000001": 11.0})
        signal = _make_signal({"000001": 0.1})

        orders = portfolio.process_signal(signal, slab)

        assert len(orders) == 1
        assert orders[0].order_type == "SELL"


class TestUpdateFromFill:
    """成交后持仓更新接口测试"""

    def test_buy_fill_updates_cash_and_position(self):
        """买入成交后现金减少、持仓增加"""
        portfolio = Portfolio(initial_capital=1_000_000.0)
        fill = _make_fill(
            "000001", "BUY", quantity=1000.0, fill_price=25.0, commission=7.5
        )

        portfolio.update_from_fill(fill)

        expected_cost = 1000 * 25.0 + 7.5
        assert portfolio.cash == pytest.approx(1_000_000.0 - expected_cost)
        assert portfolio.positions["000001"].shares == 1000.0

    def test_buy_fill_averages_cost(self):
        """多次买入应平均成本"""
        portfolio = Portfolio(initial_capital=1_000_000.0)

        fill1 = _make_fill("000001", "BUY", quantity=1000.0, fill_price=10.0)
        portfolio.update_from_fill(fill1)

        fill2 = _make_fill("000001", "BUY", quantity=1000.0, fill_price=20.0)
        portfolio.update_from_fill(fill2)

        assert portfolio.positions["000001"].shares == 2000.0
        assert portfolio.positions["000001"].avg_cost == pytest.approx(15.0)

    def test_sell_fill_updates_cash_and_reduces_shares(self):
        """卖出成交后现金增加、持仓减少"""
        portfolio = Portfolio(initial_capital=1_000_000.0)
        portfolio.positions["000001"] = Position(
            symbol="000001", shares=1000.0, avg_cost=10.0
        )
        portfolio.positions["000001"].update_market_value(10.0)

        fill = _make_fill(
            "000001",
            "SELL",
            quantity=500.0,
            fill_price=12.0,
            commission=1.8,
            stamp_duty=3.0,
        )

        portfolio.update_from_fill(fill)

        expected_net = 500 * 12.0 - 1.8 - 3.0
        assert portfolio.cash == pytest.approx(1_000_000.0 + expected_net)
        assert portfolio.positions["000001"].shares == 500.0

    def test_sell_fill_calculates_realized_pnl(self):
        """卖出成交后应计算已实现盈亏"""
        portfolio = Portfolio(initial_capital=1_000_000.0)
        portfolio.positions["000001"] = Position(
            symbol="000001", shares=1000.0, avg_cost=10.0
        )
        portfolio.positions["000001"].update_market_value(10.0)

        fill = _make_fill("000001", "SELL", quantity=500.0, fill_price=15.0)

        portfolio.update_from_fill(fill)

        assert "000001" in portfolio.realized_pnl
        assert portfolio.realized_pnl["000001"] == pytest.approx(2500.0)

    def test_sell_fill_removes_position_when_zero_shares(self):
        """卖完所有持仓后应删除仓位记录"""
        portfolio = Portfolio(initial_capital=1_000_000.0)
        portfolio.positions["000001"] = Position(
            symbol="000001", shares=1000.0, avg_cost=10.0
        )
        portfolio.positions["000001"].update_market_value(10.0)

        fill = _make_fill("000001", "SELL", quantity=1000.0, fill_price=12.0)

        portfolio.update_from_fill(fill)

        assert "000001" not in portfolio.positions


class TestUpdateMarketValues:
    """市值更新接口测试"""

    def test_updates_all_position_market_values(self):
        """更新所有持仓的市值"""
        portfolio = Portfolio(initial_capital=1_000_000.0)
        portfolio.positions["000001"] = Position(
            symbol="000001", shares=1000.0, avg_cost=10.0
        )
        portfolio.positions["000002"] = Position(
            symbol="000002", shares=500.0, avg_cost=20.0
        )

        slab = _make_slab({"000001": 15.0, "000002": 25.0})
        portfolio.update_market_values(slab)

        assert portfolio.positions["000001"].market_value == pytest.approx(15000.0)
        assert portfolio.positions["000002"].market_value == pytest.approx(12500.0)

    def test_updates_total_value(self):
        """更新市值后 total_value 应反映最新估值"""
        portfolio = Portfolio(initial_capital=1_000_000.0)
        portfolio.cash = 500_000.0
        portfolio.positions["000001"] = Position(
            symbol="000001", shares=10000.0, avg_cost=10.0
        )
        portfolio.positions["000001"].update_market_value(10.0)

        slab = _make_slab({"000001": 15.0})
        portfolio.update_market_values(slab)

        assert portfolio.total_value == pytest.approx(650_000.0)


class TestMaxPositions:
    """最大持仓数限制接口测试"""

    def test_max_positions_limits_new_entries(self):
        """超过 max_positions 时不开新仓"""
        portfolio = Portfolio(initial_capital=1_000_000.0)
        portfolio.positions["000001"] = Position(
            symbol="000001", shares=100.0, avg_cost=10.0
        )
        portfolio.positions["000002"] = Position(
            symbol="000002", shares=100.0, avg_cost=20.0
        )

        slab = _make_slab({"000001": 10.0, "000002": 20.0, "000003": 30.0})
        signal = _make_signal({"000001": 0.1, "000002": 0.1, "000003": 0.3})

        orders = portfolio.process_signal(signal, slab, max_positions=2)

        symbols_in_orders = {o.symbol for o in orders}
        assert "000003" not in symbols_in_orders

    def test_max_positions_zero_is_unlimited(self):
        """max_positions=0 时不限制持仓数"""
        portfolio = Portfolio(initial_capital=1_000_000.0)
        slab = _make_slab({f"s{i:03d}": 10.0 for i in range(1, 11)})
        signal = _make_signal({f"s{i:03d}": 0.1 for i in range(1, 11)})

        orders = portfolio.process_signal(signal, slab, max_positions=0)

        assert len(orders) == 10
