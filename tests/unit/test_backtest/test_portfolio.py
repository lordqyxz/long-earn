"""Portfolio 组合管理测试

测试信号转订单、成交后持仓更新、市值更新及最大持仓限制。
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
    """信号转订单测试"""

    def test_single_weight_generates_buy_order(self):
        """单一权重信号生成买入订单"""
        portfolio = Portfolio(initial_capital=1_000_000.0)
        slab = _make_slab({"000001": 10.0})
        signal = _make_signal({"000001": 0.5})  # 50% 仓位

        orders = portfolio.process_signal(signal, slab)

        assert len(orders) == 1
        assert orders[0].symbol == "000001"
        assert orders[0].order_type == "BUY"
        # 目标金额 = 1_000_000 * 0.5 = 500_000, 数量 = 500_000 / 10 = 50_000
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
        for o in orders:
            assert o.order_type == "BUY"

    def test_zero_weight_skipped(self):
        """权重 <= 0 的信号被跳过"""
        portfolio = Portfolio(initial_capital=1_000_000.0)
        slab = _make_slab({"000001": 10.0, "000002": 20.0})
        signal = _make_signal({"000001": 0.5, "000002": 0.0})

        orders = portfolio.process_signal(signal, slab)

        assert len(orders) == 1
        assert orders[0].symbol == "000001"

    def test_negative_weight_skipped(self):
        """负权重的信号被跳过"""
        portfolio = Portfolio(initial_capital=1_000_000.0)
        slab = _make_slab({"000001": 10.0})
        signal = _make_signal({"000001": -0.2})

        orders = portfolio.process_signal(signal, slab)

        assert len(orders) == 0

    def test_symbol_not_in_slab_skipped(self):
        """当前截面数据中不存在的股票被跳过"""
        portfolio = Portfolio(initial_capital=1_000_000.0)
        slab = _make_slab({"000001": 10.0})
        signal = _make_signal({"000001": 0.3, "000002": 0.3})

        orders = portfolio.process_signal(signal, slab)

        assert len(orders) == 1
        assert orders[0].symbol == "000001"

    def test_small_diff_skipped(self):
        """差额不足 1 元时不生成订单"""
        portfolio = Portfolio(initial_capital=1_000_000.0)
        # 先建一个极小仓位
        portfolio.positions["000001"] = Position(
            symbol="000001", shares=1e-6, avg_cost=10.0
        )
        portfolio.positions["000001"].update_market_value(10.0)
        portfolio.total_value = (
            portfolio.cash + portfolio.positions["000001"].market_value
        )

        slab = _make_slab({"000001": 10.0})
        # 当前仓位已接近目标，差额极小
        signal = _make_signal({"000001": 0.0000001})

        orders = portfolio.process_signal(signal, slab)

        assert len(orders) == 0

    def test_rebalance_generates_sell_order(self):
        """减仓时生成卖出订单"""
        portfolio = Portfolio(initial_capital=1_000_000.0)
        # 先建仓：500,000 元 / 10 元 = 50,000 股
        portfolio.positions["000001"] = Position(
            symbol="000001", shares=50000.0, avg_cost=10.0
        )
        portfolio.positions["000001"].update_market_value(11.0)
        portfolio.cash = 500_000.0
        portfolio.total_value = (
            portfolio.cash + portfolio.positions["000001"].market_value
        )  # = 500k + 550k = 1,050,000

        slab = _make_slab({"000001": 11.0})
        signal = _make_signal({"000001": 0.1})  # 降低仓位到 10%

        orders = portfolio.process_signal(signal, slab)

        assert len(orders) == 1
        assert orders[0].order_type == "SELL"
        assert orders[0].symbol == "000001"

    def test_non_dict_signals_returns_empty(self):
        """非 dict 格式信号返回空列表"""
        portfolio = Portfolio()
        slab = _make_slab({"000001": 10.0})

        # 构造一个 signals 不是 dict 的 SignalEvent
        signal = SignalEvent(
            timestamp=datetime(2024, 1, 1),
            trace_id="sig-trace",
            event_id="sig-1",
            signals=pl.Series("weights", [0.5]),
            strategy_id="test-strategy",
        )

        orders = portfolio.process_signal(signal, slab)
        assert len(orders) == 0


class TestUpdateFromFill:
    """成交后持仓更新测试"""

    def test_buy_fill_updates_cash_and_position(self):
        """买入成交后现金减少、持仓增加"""
        portfolio = Portfolio(initial_capital=1_000_000.0)
        fill = _make_fill(
            "000001", "BUY", quantity=1000.0, fill_price=25.0, commission=7.5
        )

        portfolio.update_from_fill(fill)

        expected_cost = 1000 * 25.0 + 7.5  # 25,000 + 7.5 = 25,007.5
        assert portfolio.cash == pytest.approx(1_000_000.0 - expected_cost)
        assert portfolio.positions["000001"].shares == 1000.0
        assert portfolio.positions["000001"].avg_cost == pytest.approx(25.0)

    def test_buy_fill_averages_cost(self):
        """多次买入应平均成本"""
        portfolio = Portfolio(initial_capital=1_000_000.0)

        # 第一次买入 1000 股 @ 10 元
        fill1 = _make_fill("000001", "BUY", quantity=1000.0, fill_price=10.0)
        portfolio.update_from_fill(fill1)

        # 第二次买入 1000 股 @ 20 元
        fill2 = _make_fill("000001", "BUY", quantity=1000.0, fill_price=20.0)
        portfolio.update_from_fill(fill2)

        # 平均成本 = (1000*10 + 1000*20) / 2000 = 15
        assert portfolio.positions["000001"].shares == 2000.0
        assert portfolio.positions["000001"].avg_cost == pytest.approx(15.0)

    def test_sell_fill_updates_cash_and_reduces_shares(self):
        """卖出成交后现金增加、持仓减少"""
        portfolio = Portfolio(initial_capital=1_000_000.0)
        # 先建仓
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

        # 净收入 = 500*12 - 1.8 - 3.0 = 5995.2
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

        # 已实现盈亏 = (15 - 10) * 500 = 2500
        assert "000001" in portfolio.pnl_by_symbol
        assert portfolio.pnl_by_symbol["000001"] == pytest.approx(2500.0)

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

    def test_sell_fill_accumulates_pnl_by_symbol(self):
        """多次卖出同一股票的盈亏应累加"""
        portfolio = Portfolio(initial_capital=1_000_000.0)
        portfolio.positions["000001"] = Position(
            symbol="000001", shares=1000.0, avg_cost=10.0
        )
        portfolio.positions["000001"].update_market_value(10.0)

        # 第一次卖出 400 股 @ 12
        fill1 = _make_fill("000001", "SELL", quantity=400.0, fill_price=12.0)
        portfolio.update_from_fill(fill1)

        # 第二次卖出 600 股 @ 14
        fill2 = _make_fill("000001", "SELL", quantity=600.0, fill_price=14.0)
        portfolio.update_from_fill(fill2)

        # 已实现盈亏 = (12-10)*400 + (14-10)*600 = 800 + 2400 = 3200
        assert portfolio.pnl_by_symbol["000001"] == pytest.approx(3200.0)

    def test_trade_count_increments_on_fill(self):
        """每笔成交记录应增加交易计数"""
        portfolio = Portfolio(initial_capital=1_000_000.0)
        portfolio.positions["000001"] = Position(
            symbol="000001", shares=1000.0, avg_cost=10.0
        )
        portfolio.positions["000001"].update_market_value(10.0)

        assert portfolio.trade_count == 0

        fill = _make_fill("000001", "SELL", quantity=100.0, fill_price=12.0)
        portfolio.update_from_fill(fill)
        assert portfolio.trade_count == 1

        fill2 = _make_fill("000002", "BUY", quantity=200.0, fill_price=15.0)
        portfolio.update_from_fill(fill2)
        assert portfolio.trade_count == 2


class TestUpdateMarketValues:
    """市值更新测试"""

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
        assert portfolio.positions["000001"].current_price == pytest.approx(15.0)
        assert portfolio.positions["000002"].market_value == pytest.approx(12500.0)
        assert portfolio.positions["000002"].current_price == pytest.approx(25.0)

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

        # total_value = cash + sum(market_values) = 500k + 150k = 650k
        assert portfolio.total_value == pytest.approx(650_000.0)

    def test_appends_to_equity_curve(self):
        """每次更新市值后应追加权益曲线"""
        portfolio = Portfolio(initial_capital=1_000_000.0)
        assert len(portfolio.equity_curve) == 1  # 初始值

        slab = _make_slab({"000001": 10.0})
        portfolio.update_market_values(slab)

        assert len(portfolio.equity_curve) == 2
        assert portfolio.equity_curve[-1] == portfolio.total_value


class TestMaxPositions:
    """最大持仓数限制测试"""

    def test_max_positions_limits_new_entries(self):
        """超过 max_positions 时不开新仓"""
        portfolio = Portfolio(initial_capital=1_000_000.0)
        # 已有 2 个持仓
        portfolio.positions["000001"] = Position(
            symbol="000001", shares=100.0, avg_cost=10.0
        )
        portfolio.positions["000002"] = Position(
            symbol="000002", shares=100.0, avg_cost=20.0
        )

        slab = _make_slab({"000001": 10.0, "000002": 20.0, "000003": 30.0})
        signal = _make_signal({"000001": 0.1, "000002": 0.1, "000003": 0.3})

        orders = portfolio.process_signal(signal, slab, max_positions=2)

        # 只应调整已有持仓，不开新仓 000003
        symbols_in_orders = {o.symbol for o in orders}
        assert "000003" not in symbols_in_orders

    def test_max_positions_zero_is_unlimited(self):
        """max_positions=0 时不限制持仓数"""
        portfolio = Portfolio(initial_capital=1_000_000.0)
        slab = _make_slab({f"s{i:03d}": 10.0 for i in range(1, 11)})
        signal = _make_signal({f"s{i:03d}": 0.1 for i in range(1, 11)})

        orders = portfolio.process_signal(signal, slab, max_positions=0)

        assert len(orders) == 10

    def test_max_positions_within_limit_allows_new(self):
        """未满仓时仍可开新仓"""
        portfolio = Portfolio(initial_capital=1_000_000.0)
        portfolio.positions["000001"] = Position(
            symbol="000001", shares=100.0, avg_cost=10.0
        )

        slab = _make_slab({"000001": 10.0, "000002": 20.0})
        signal = _make_signal({"000001": 0.1, "000002": 0.3})

        orders = portfolio.process_signal(signal, slab, max_positions=3)

        symbols_in_orders = {o.symbol for o in orders}
        # 可以开 000002
        assert "000002" in symbols_in_orders

    def test_max_positions_keeps_highest_weight_newcomers(self):
        """开新仓时按权重降序保留前 N 个"""
        portfolio = Portfolio(initial_capital=1_000_000.0)
        portfolio.positions["000001"] = Position(
            symbol="000001", shares=100.0, avg_cost=10.0
        )

        slab = _make_slab({"000001": 10.0, "000002": 20.0, "000003": 30.0})
        signal = _make_signal({"000001": 0.1, "000002": 0.05, "000003": 0.3})

        # max_positions=2, 已有 1 个, 只能再进 1 个, 选权重最高的 000003
        orders = portfolio.process_signal(signal, slab, max_positions=2)

        new_syms = {o.symbol for o in orders if o.symbol != "000001"}
        assert "000003" in new_syms
        assert "000002" not in new_syms
