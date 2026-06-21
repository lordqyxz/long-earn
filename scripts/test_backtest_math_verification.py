"""回测引擎数学正确性验证
本脚本通过构造解析解已知的场景，验证引擎计算的数学正确性。

验证的数学不变量：
1. 资金守恒：总价值 = 现金 + 持仓市值
2. Buy & Hold 策略：收益率应等于股价收益率
3. 线性价格策略：收益率可解析计算
4. 最大回撤：应等于理论最大值
5. 已实现 P&L：应等于 (卖出价 - 成本价) * 数量 - 交易成本
"""

import sys
import os
from datetime import datetime, timedelta
import uuid

import numpy as np
import polars as pl

# 添加源码路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from long_earn.backtest.engine.core import EventDrivenBacktestEngine
from long_earn.backtest.engine.strategy import BaseStrategy
from long_earn.backtest.engine.broker import Broker, TradingCostConfig
from long_earn.backtest.engine.portfolio import Portfolio
from long_earn.backtest.domain.entities import (
    FillEvent,
    OrderEvent,
    Position,
    SignalEvent,
)


# ============================================================================
# 测试数据生成器
# ============================================================================

def generate_simple_panel(
    symbols: list[str],
    start_date: str,
    end_date: str,
    price_pattern: str = "constant",
    initial_price: float = 100.0,
) -> pl.DataFrame:
    """生成简单的测试数据面板"""
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    days = (end - start).days + 1

    timestamps = [start + timedelta(days=i) for i in range(days)]

    data = []
    for sym_idx, symbol in enumerate(symbols):
        base_price = initial_price * (1 + 0.1 * sym_idx)  # 不同股票不同起始价
        for day_idx, ts in enumerate(timestamps):
            if price_pattern == "constant":
                price = base_price
            elif price_pattern == "linear_up":
                price = base_price * (1 + 0.01 * day_idx)  # 每日涨 1%
            elif price_pattern == "linear_down":
                price = base_price * (1 - 0.01 * day_idx)  # 每日跌 1%
            elif price_pattern == "volatility":
                np.random.seed(sym_idx * 1000 + day_idx)
                price = base_price * (1 + 0.02 * np.random.randn())
            else:
                price = base_price

            data.append({
                "timestamp": ts,
                "symbol": symbol,
                "open": price,
                "high": price * 1.01,
                "low": price * 0.99,
                "close": price,
                "volume": 1000000,
            })

    return pl.DataFrame(data)


class MockDataProvider:
    """模拟数据提供者"""

    def __init__(self, panel: pl.DataFrame):
        self._panel = panel

    def get_merged_panel_as_polars(
        self, symbols: list[str], start: str, end: str
    ) -> pl.DataFrame:
        return self._panel


# ============================================================================
# 策略定义
# ============================================================================

class BuyAndHoldStrategy(BaseStrategy):
    """第一天全仓买入，之后持有不动"""

    def __init__(self, symbol: str, weight: float = 1.0):
        super().__init__("buy_and_hold")
        self._symbol = symbol
        self._weight = weight
        self._bought = False

    def init(self) -> None:
        self._bought = False

    def on_bar(self, bars: pl.DataFrame, context) -> SignalEvent | None:
        if self._bought:
            return None
        self._bought = True

        return SignalEvent(
            timestamp=bars.select("timestamp").to_series()[0],
            trace_id=str(uuid.uuid4()),
            event_id=f"sig_buy_{uuid.uuid4().hex[:8]}",
            signals={self._symbol: self._weight},
            strategy_id=self.strategy_id,
        )


class BuyOnceSellOnceStrategy(BaseStrategy):
    """第一天买入，最后一天卖出"""

    def __init__(self, symbol: str, n_days: int, weight: float = 1.0):
        super().__init__("buy_sell_once")
        self._symbol = symbol
        self._weight = weight
        self._day_count = 0
        self._n_days = n_days
        self._bought = False
        self._sold = False

    def init(self) -> None:
        self._day_count = 0
        self._bought = False
        self._sold = False

    def on_bar(self, bars: pl.DataFrame, context) -> SignalEvent | None:
        self._day_count += 1
        ts = bars.select("timestamp").to_series()[0]

        if not self._bought and self._day_count == 1:
            self._bought = True
            return SignalEvent(
                timestamp=ts,
                trace_id=str(uuid.uuid4()),
                event_id=f"sig_buy_{uuid.uuid4().hex[:8]}",
                signals={self._symbol: self._weight},
                strategy_id=self.strategy_id,
            )

        if not self._sold and self._day_count == self._n_days:
            self._sold = True
            # 卖出：信号 weight=0，Portfolio.process_signal 中 weight<=0 被跳过
            # 所以我们用自定义方式：通过 on_bar 返回 None 并在引擎外部处理
            # 这里用一个技巧：直接给一个特殊信号
            return SignalEvent(
                timestamp=ts,
                trace_id=str(uuid.uuid4()),
                event_id=f"sig_sell_{uuid.uuid4().hex[:8]}",
                signals={self._symbol: 0.0},  # 会被跳过！
                strategy_id=self.strategy_id,
            )

        return None


class ManualTradeStrategy(BaseStrategy):
    """完全手动控制买卖的策略"""

    def __init__(self, buy_days: set[int], sell_days: set[int], symbol: str, weight: float = 1.0):
        super().__init__("manual_trade")
        self._symbol = symbol
        self._buy_days = buy_days
        self._sell_days = sell_days
        self._weight = weight
        self._day_count = 0

    def init(self) -> None:
        self._day_count = 0

    def on_bar(self, bars: pl.DataFrame, context) -> SignalEvent | None:
        self._day_count += 1
        ts = bars.select("timestamp").to_series()[0]

        if self._day_count in self._buy_days:
            return SignalEvent(
                timestamp=ts,
                trace_id=str(uuid.uuid4()),
                event_id=f"sig_buy_{uuid.uuid4().hex[:8]}",
                signals={self._symbol: self._weight},
                strategy_id=self.strategy_id,
            )

        if self._day_count in self._sell_days:
            # 通过 Portfolio 自定义逻辑处理卖出
            return SignalEvent(
                timestamp=ts,
                trace_id=str(uuid.uuid4()),
                event_id=f"sig_sell_{uuid.uuid4().hex[:8]}",
                signals={self._symbol: -1.0},  # 负权重表示卖出
                strategy_id=self.strategy_id,
            )

        return None


# ============================================================================
# 自定义 Portfolio（支持卖出信号）
# ============================================================================

class ExtendedPortfolio(Portfolio):
    """支持负权重表示卖出的 Portfolio"""

    def process_signal(
        self, event: SignalEvent, current_prices: pl.DataFrame, max_positions: int = 0
    ) -> list[OrderEvent]:
        target_weights = event.signals

        if not isinstance(target_weights, dict):
            return []

        orders = []
        for symbol, target_weight in target_weights.items():
            current_pos = self.positions.get(symbol)

            price_rows = current_prices.filter(pl.col("symbol") == symbol)
            if price_rows.is_empty():
                continue
            price = price_rows.select("close").to_series()[0]
            if price is None or price <= 0:
                continue

            if target_weight < 0 and current_pos and current_pos.shares > 0:
                # 卖出信号：卖出所有持仓
                qty = current_pos.shares
                if qty > 0:
                    orders.append(
                        OrderEvent(
                            timestamp=event.timestamp,
                            trace_id=str(uuid.uuid4()),
                            event_id=f"ord_sell_{symbol}",
                            symbol=symbol,
                            order_type="SELL",
                            quantity=qty,
                            price=price,
                            order_id=f"ord_{uuid.uuid4().hex[:8]}",
                        )
                    )
            elif target_weight > 0:
                # 买入信号（正常逻辑）
                target_val = self.total_value * target_weight
                current_val = current_pos.market_value if current_pos else 0.0
                diff_val = target_val - current_val

                if abs(diff_val) < 1.0:
                    continue

                order_type = "BUY" if diff_val > 0 else "SELL"
                qty = abs(diff_val) / price

                if qty > 0:
                    orders.append(
                        OrderEvent(
                            timestamp=event.timestamp,
                            trace_id=str(uuid.uuid4()),
                            event_id=f"ord_{order_type.lower()}_{symbol}",
                            symbol=symbol,
                            order_type=order_type,
                            quantity=qty,
                            price=price,
                            order_id=f"ord_{uuid.uuid4().hex[:8]}",
                        )
                    )

        return orders


class ExtendedBacktestEngine(EventDrivenBacktestEngine):
    """支持 ExtendedPortfolio 的回测引擎"""

    def run(
        self,
        strategy: BaseStrategy,
        start_date: str,
        end_date: str,
        symbols: list[str],
        benchmark_symbol: str = "",
    ):
        try:
            full_data = self._prepare_data(symbols, start_date, end_date)
            if full_data.is_empty():
                from long_earn.backtest.models import BacktestResult
                return BacktestResult(success=False, message="加载数据为空")

            from long_earn.backtest.engine.visibility import VisibilityGuard
            guard = VisibilityGuard(full_data)
            portfolio = ExtendedPortfolio()
            broker = Broker(self.cost_config)
            broker.reset()
            strategy.init()

            timestamps = self._get_timestamps(full_data)

            for bar_idx, ts in enumerate(timestamps):
                self._process_timestamp_extended(
                    ts, guard, portfolio, broker, strategy, bar_idx
                )

            self._finalize_market_value(portfolio, full_data, timestamps[-1])
            return self._build_result(portfolio, len(timestamps))
        except Exception as e:
            print(f"回测执行失败: {e}")
            import traceback
            traceback.print_exc()
            from long_earn.backtest.models import BacktestResult
            return BacktestResult(success=False, message=str(e))

    def _process_timestamp_extended(
        self, ts, guard, portfolio, broker, strategy, bar_idx
    ) -> None:
        guard.set_time(ts)
        slab = guard.read_current_slab()

        # 先更新市值
        portfolio.update_market_values(slab)

        # 检查待成交订单
        price_lookup = {
            sym: float(price)
            for sym, price in zip(
                slab.select("symbol").to_series().to_list(),
                slab.select("close").to_series().to_list(),
            )
        }
        pending_fills = broker.check_pending_orders(
            bar_idx=bar_idx, price_lookup=price_lookup
        )
        for pf in pending_fills:
            portfolio.update_from_fill(pf)

        # 策略生成信号
        signal_event = strategy.on_bar(slab, guard.get_context())
        if signal_event is not None:
            orders = portfolio.process_signal(signal_event, slab)
            for order in orders:
                price = self._lookup_price_static(slab, order.symbol)
                if price is None:
                    continue
                fill = broker.execute_order(order, price)
                portfolio.update_from_fill(fill)

    @staticmethod
    def _lookup_price_static(slab: pl.DataFrame, symbol: str) -> float | None:
        price_rows = slab.filter(pl.col("symbol") == symbol).select("close").to_series()
        if price_rows.is_empty():
            return None
        return price_rows[0]

    @staticmethod
    def _finalize_market_value(portfolio, full_data, last_ts) -> None:
        portfolio.update_market_values(full_data.filter(pl.col("timestamp") == last_ts))

    def _build_result(self, portfolio, trading_days):
        from long_earn.backtest.domain.entities import PerformanceMetrics
        metrics = self._calculate_metrics(portfolio)

        from long_earn.backtest.models import BacktestResult
        return BacktestResult(
            success=True,
            message="回测成功",
            total_return=metrics.total_return,
            annual_return=metrics.annual_return,
            sharpe_ratio=metrics.sharpe_ratio,
            max_drawdown=metrics.max_drawdown,
            win_rate=metrics.win_rate,
            trading_days=trading_days,
            volatility=metrics.volatility,
            calmar_ratio=metrics.calmar_ratio,
            sortino_ratio=metrics.sortino_ratio,
            daily_returns=[
                {"day": i, "value": v} for i, v in enumerate(portfolio.equity_curve)
            ],
            trade_count=portfolio.trade_count,
            attribution=dict(portfolio.pnl_by_symbol),
        )

    def _calculate_metrics(self, portfolio):
        from long_earn.backtest.domain.entities import PerformanceMetrics
        equity = portfolio.equity_curve
        if len(equity) < 2:
            return PerformanceMetrics()

        equity_arr = np.array(equity, dtype=float)
        returns = np.diff(equity_arr) / equity_arr[:-1]
        if len(returns) == 0:
            return PerformanceMetrics()

        total_return = (equity_arr[-1] / equity_arr[0]) - 1
        trading_days = len(returns)
        annual_factor = 252 / trading_days if trading_days > 0 else 1.0
        annual_return = (1 + total_return) ** annual_factor - 1
        volatility = float(np.std(returns, ddof=1)) * np.sqrt(252)
        sharpe = annual_return / volatility if volatility > 0 else 0.0

        peak = np.maximum.accumulate(equity_arr)
        drawdown = (equity_arr - peak) / peak
        max_dd = float(np.min(drawdown))

        win_rate = (
            float(np.sum(returns > 0) / len(returns)) if len(returns) > 0 else 0.0
        )
        calmar = annual_return / abs(max_dd) if max_dd != 0 else 0.0

        downside = returns[returns < 0]
        downside_std = (
            float(np.std(downside, ddof=1)) * np.sqrt(252) if len(downside) > 0 else 0.0
        )
        sortino = annual_return / downside_std if downside_std > 0 else 0.0

        return PerformanceMetrics(
            total_return=total_return,
            annual_return=annual_return,
            sharpe_ratio=sharpe,
            max_drawdown=max_dd,
            win_rate=win_rate,
            trading_days=trading_days,
            volatility=volatility,
            calmar_ratio=calmar,
            sortino_ratio=sortino,
        )


# ============================================================================
# 测试用例
# ============================================================================

def test_1_buy_and_hold_constant_price():
    """测试 1: 恒定价格的 Buy & Hold 策略
    数学期望：总收益率 ≈ 0（考虑交易成本后略负）
    """
    print("=" * 70)
    print("测试 1: Buy & Hold 恒定价格策略")
    print("=" * 70)

    panel = generate_simple_panel(
        ["STOCK001"], "2024-01-01", "2024-01-10", price_pattern="constant", initial_price=100.0
    )
    provider = MockDataProvider(panel)

    # 零交易成本配置
    zero_cost = TradingCostConfig(commission_rate=0.0, stamp_duty=0.0, slippage_bps=0.0)

    engine = ExtendedBacktestEngine(data_provider=provider, cost_config=zero_cost)
    strategy = BuyAndHoldStrategy("STOCK001", weight=1.0)

    result = engine.run(strategy, "2024-01-01", "2024-01-10", ["STOCK001"])

    print(f"总收益率: {result.total_return:.6%}")
    print(f"年化收益率: {result.annual_return:.6%}")

    # 零成本下 Buy & Hold 恒定价格，收益率应为 0
    expected_return = 0.0
    tolerance = 1e-6
    is_pass = abs(result.total_return - expected_return) < tolerance

    print(f"期望收益率: {expected_return:.6%}")
    print(f"通过测试: {is_pass}")
    return is_pass


def test_2_buy_and_hold_linear_price():
    """测试 2: 线性上涨价格的 Buy & Hold 策略
    数学期望：总收益率 = (最终价格 / 初始价格) - 1 = (最后一天价格 / 第一天价格) - 1
    注意：交易成本的存在会轻微降低收益率
    """
    print("\n" + "=" * 70)
    print("测试 2: Buy & Hold 线性上涨价格策略")
    print("=" * 70)

    n_days = 10
    panel = generate_simple_panel(
        ["STOCK001"], "2024-01-01", "2024-01-10",
        price_pattern="linear_up", initial_price=100.0
    )

    # 验证价格序列
    prices = panel.filter(pl.col("symbol") == "STOCK001").sort("timestamp").select("close").to_series().to_list()
    print(f"价格序列: {[f'{p:.2f}' for p in prices]}")

    # 理论收益率（零成本）
    theoretical_return = (prices[-1] / prices[0]) - 1
    print(f"理论收益率 (零成本): {theoretical_return:.6%}")

    provider = MockDataProvider(panel)

    # 零交易成本
    zero_cost = TradingCostConfig(commission_rate=0.0, stamp_duty=0.0, slippage_bps=0.0)

    engine = ExtendedBacktestEngine(data_provider=provider, cost_config=zero_cost)
    strategy = BuyAndHoldStrategy("STOCK001", weight=1.0)

    result = engine.run(strategy, "2024-01-01", "2024-01-10", ["STOCK001"])

    print(f"引擎计算总收益率: {result.total_return:.6%}")

    tolerance = 1e-4  # 0.01% 容差
    is_pass = abs(result.total_return - theoretical_return) < tolerance

    print(f"差值: {abs(result.total_return - theoretical_return):.6%}")
    print(f"通过测试: {is_pass}")
    return is_pass


def test_3_capital_conservation():
    """测试 3: 资金守恒验证
    数学不变量：total_value = cash + sum(market_value)
    这个不变量应该在任何时刻成立
    """
    print("\n" + "=" * 70)
    print("测试 3: 资金守恒不变量验证")
    print("=" * 70)

    panel = generate_simple_panel(
        ["STOCK001", "STOCK002"], "2024-01-01", "2024-01-05",
        price_pattern="linear_up", initial_price=100.0
    )
    provider = MockDataProvider(panel)

    zero_cost = TradingCostConfig(commission_rate=0.0, stamp_duty=0.0, slippage_bps=0.0)

    portfolio = ExtendedPortfolio(initial_capital=1_000_000.0)

    # 手动执行一系列操作，每次操作后验证资金守恒
    timestamps = panel.select("timestamp").unique().sort("timestamp").to_series().to_list()

    all_pass = True
    for idx, ts in enumerate(timestamps):
        slab = panel.filter(pl.col("timestamp") == ts)
        portfolio.update_market_values(slab)

        # 验证资金守恒
        computed_total = portfolio.cash + sum(p.market_value for p in portfolio.positions.values())
        diff = abs(portfolio.total_value - computed_total)

        is_valid = diff < 1e-6
        all_pass = all_pass and is_valid

        print(f"Day {idx + 1}: cash={portfolio.cash:.2f}, positions_value={sum(p.market_value for p in portfolio.positions.values()):.2f}, "
              f"total={portfolio.total_value:.2f}, diff={diff:.2e}, valid={is_valid}")

        # 第二天买入 STOCK001
        if idx == 1:
            price = float(slab.filter(pl.col("symbol") == "STOCK001").select("close").to_series()[0])
            order = OrderEvent(
                timestamp=ts,
                trace_id=str(uuid.uuid4()),
                event_id=f"ord_buy",
                symbol="STOCK001",
                order_type="BUY",
                quantity=1000,
                price=price,
                order_id="order_buy_1",
            )
            broker = Broker(zero_cost)
            fill = broker.execute_order(order, price)
            portfolio.update_from_fill(fill)
            print(f"  -> 买入 1000 股 STOCK001 @ {price:.2f}")

            # 验证交易后资金守恒
            computed_total = portfolio.cash + sum(p.market_value for p in portfolio.positions.values())
            # 注意：update_from_fill 后 total_value 不会自动更新，需要重新计算
            manual_total = portfolio.cash + sum(p.shares * p.current_price for p in portfolio.positions.values())
            print(f"  -> 交易后: cash={portfolio.cash:.2f}, manual_total={manual_total:.2f}")

    print(f"资金守恒测试: {all_pass}")
    return all_pass


def test_4_realized_pnl_calculation():
    """测试 4: 已实现 P&L 计算验证
    买入价 P_buy，卖出价 P_sell，数量 Q
    理论已实现 P&L = (P_sell - P_buy) * Q - 交易成本
    """
    print("\n" + "=" * 70)
    print("测试 4: 已实现 P&L 计算验证")
    print("=" * 70)

    initial_price = 100.0
    buy_price = initial_price
    sell_price = initial_price * 1.1  # 涨 10% 后卖出
    quantity = 1000

    # 构造面板：前 2 天 100，后 3 天 110
    panel_data = []
    for day in range(5):
        price = buy_price if day < 2 else sell_price
        ts = datetime(2024, 1, day + 1)
        panel_data.append({
            "timestamp": ts,
            "symbol": "STOCK001",
            "open": price,
            "high": price * 1.01,
            "low": price * 0.99,
            "close": price,
            "volume": 1000000,
        })
    panel = pl.DataFrame(panel_data)

    zero_cost = TradingCostConfig(commission_rate=0.0, stamp_duty=0.0, slippage_bps=0.0)
    portfolio = ExtendedPortfolio(initial_capital=1_000_000.0)
    broker = Broker(zero_cost)

    # Day 1: 更新市值
    ts1 = panel.select("timestamp").unique().sort("timestamp").to_series()[0]
    slab1 = panel.filter(pl.col("timestamp") == ts1)
    portfolio.update_market_values(slab1)

    # Day 2: 买入
    ts2 = panel.select("timestamp").unique().sort("timestamp").to_series()[1]
    slab2 = panel.filter(pl.col("timestamp") == ts2)
    portfolio.update_market_values(slab2)

    order_buy = OrderEvent(
        timestamp=ts2,
        trace_id=str(uuid.uuid4()),
        event_id="ord_buy",
        symbol="STOCK001",
        order_type="BUY",
        quantity=quantity,
        price=buy_price,
        order_id="order_buy",
    )
    fill_buy = broker.execute_order(order_buy, buy_price)
    portfolio.update_from_fill(fill_buy)

    print(f"买入: {quantity} 股 @ {buy_price:.2f}")
    print(f"买入后 cash: {portfolio.cash:.2f}, shares: {portfolio.positions['STOCK001'].shares:.2f}, avg_cost: {portfolio.positions['STOCK001'].avg_cost:.2f}")

    # Day 3-4: 更新市值
    for day_idx in range(2, 4):
        ts = panel.select("timestamp").unique().sort("timestamp").to_series()[day_idx]
        slab = panel.filter(pl.col("timestamp") == ts)
        portfolio.update_market_values(slab)
        pos = portfolio.positions.get("STOCK001")
        if pos:
            print(f"Day {day_idx + 1}: 市值更新后 price={pos.current_price:.2f}, market_value={pos.market_value:.2f}")

    # Day 5: 卖出
    ts5 = panel.select("timestamp").unique().sort("timestamp").to_series()[4]
    slab5 = panel.filter(pl.col("timestamp") == ts5)
    portfolio.update_market_values(slab5)

    pos_before_sell = portfolio.positions["STOCK001"]
    print(f"卖出前: shares={pos_before_sell.shares:.2f}, avg_cost={pos_before_sell.avg_cost:.2f}, current_price={pos_before_sell.current_price:.2f}")

    order_sell = OrderEvent(
        timestamp=ts5,
        trace_id=str(uuid.uuid4()),
        event_id="ord_sell",
        symbol="STOCK001",
        order_type="SELL",
        quantity=quantity,
        price=sell_price,
        order_id="order_sell",
    )
    fill_sell = broker.execute_order(order_sell, sell_price)
    portfolio.update_from_fill(fill_sell)

    print(f"卖出: {quantity} 股 @ {sell_price:.2f}")
    print(f"卖出后 cash: {portfolio.cash:.2f}, positions: {list(portfolio.positions.keys())}")

    # 理论已实现 P&L（零成本）
    theoretical_pnl = (sell_price - buy_price) * quantity
    actual_pnl = portfolio.pnl_by_symbol.get("STOCK001", 0.0)

    print(f"\n理论已实现 P&L: {theoretical_pnl:.2f}")
    print(f"引擎计算已实现 P&L: {actual_pnl:.2f}")

    is_pass = abs(actual_pnl - theoretical_pnl) < 1.0
    print(f"差值: {abs(actual_pnl - theoretical_pnl):.2f}")
    print(f"通过测试: {is_pass}")

    return is_pass


def test_5_max_drawdown_calculation():
    """测试 5: 最大回撤计算验证
    构造先涨后跌的价格序列，验证最大回撤计算
    """
    print("\n" + "=" * 70)
    print("测试 5: 最大回撤计算验证")
    print("=" * 70)

    # 构造价格序列：100 -> 120 (peak) -> 84 (30% drawdown from peak)
    prices_up = [100.0, 105.0, 110.0, 115.0, 120.0]  # 上涨
    prices_down = [114.0, 108.0, 100.0, 92.0, 84.0]   # 下跌 30%
    all_prices = prices_up + prices_down

    panel_data = []
    for day, price in enumerate(all_prices):
        ts = datetime(2024, 1, day + 1)
        panel_data.append({
            "timestamp": ts,
            "symbol": "STOCK001",
            "open": price,
            "high": price * 1.01,
            "low": price * 0.99,
            "close": price,
            "volume": 1000000,
        })
    panel = pl.DataFrame(panel_data)

    provider = MockDataProvider(panel)
    zero_cost = TradingCostConfig(commission_rate=0.0, stamp_duty=0.0, slippage_bps=0.0)

    engine = ExtendedBacktestEngine(data_provider=provider, cost_config=zero_cost)
    strategy = BuyAndHoldStrategy("STOCK001", weight=1.0)

    result = engine.run(strategy, "2024-01-01", "2024-01-10", ["STOCK001"])

    # 理论最大回撤
    # 价格峰值在第 5 天 = 120
    # 最低点在第 10 天 = 84
    # 最大回撤 = (84 - 120) / 120 = -0.30 = -30%
    theoretical_max_dd = (84.0 - 120.0) / 120.0

    print(f"价格序列: {all_prices}")
    print(f"理论最大回撤: {theoretical_max_dd:.6%}")
    print(f"引擎计算最大回撤: {result.max_drawdown:.6%}")

    # 注意：由于买入是在第一天执行，equity_curve 的第一个值是 initial_capital
    # 买入后市值会跟随价格波动
    # 我们需要检查 equity_curve 来理解引擎的行为

    # 打印 equity curve（如果有的话）
    if hasattr(result, 'daily_returns') and result.daily_returns:
        eq_values = [d['value'] for d in result.daily_returns]
        print(f"Equity Curve: {[f'{v:.0f}' for v in eq_values]}")

    tolerance = 0.01  # 1% 容差
    is_pass = abs(result.max_drawdown - theoretical_max_dd) < tolerance

    print(f"差值: {abs(result.max_drawdown - theoretical_max_dd):.6%}")
    print(f"通过测试: {is_pass}")
    return is_pass


def test_6_portfolio_invariant_detailed():
    """测试 6: Portfolio 状态不变量的详细检查"""
    print("\n" + "=" * 70)
    print("测试 6: Portfolio 状态不变量详细检查")
    print("=" * 70)

    # 构造价格：100, 100, 110, 110, 110 (Day1-5)
    prices = [100.0, 100.0, 110.0, 110.0, 110.0]
    panel_data = []
    for day, price in enumerate(prices):
        ts = datetime(2024, 1, day + 1)
        panel_data.append({
            "timestamp": ts,
            "symbol": "STOCK001",
            "open": price,
            "high": price * 1.01,
            "low": price * 0.99,
            "close": price,
            "volume": 1000000,
        })
    panel = pl.DataFrame(panel_data)

    zero_cost = TradingCostConfig(commission_rate=0.0, stamp_duty=0.0, slippage_bps=0.0)
    portfolio = ExtendedPortfolio(initial_capital=1_000_000.0)
    broker = Broker(zero_cost)

    timestamps = panel.select("timestamp").unique().sort("timestamp").to_series().to_list()

    print(f"初始状态: cash={portfolio.cash:.0f}, total_value={portfolio.total_value:.0f}")
    print(f"初始 equity_curve: {portfolio.equity_curve}")

    all_pass = True

    for day_idx, ts in enumerate(timestamps):
        slab = panel.filter(pl.col("timestamp") == ts)
        price = float(slab.filter(pl.col("symbol") == "STOCK001").select("close").to_series()[0])

        # 更新市值
        portfolio.update_market_values(slab)

        computed_total = portfolio.cash + sum(p.market_value for p in portfolio.positions.values())
        diff = abs(portfolio.total_value - computed_total)
        invariant_ok = diff < 1e-6

        pos_info = ""
        if "STOCK001" in portfolio.positions:
            pos = portfolio.positions["STOCK001"]
            pos_info = f", shares={pos.shares:.0f}, avg_cost={pos.avg_cost:.2f}, mv={pos.market_value:.0f}"

        print(f"\nDay {day_idx + 1} (price={price:.0f}):")
        print(f"  市值更新后: cash={portfolio.cash:.0f}, total={portfolio.total_value:.0f}{pos_info}")
        print(f"  资金守恒: {'OK' if invariant_ok else 'FAIL'} (diff={diff:.2e})")
        print(f"  equity_curve 长度: {len(portfolio.equity_curve)}, 最后值: {portfolio.equity_curve[-1]:.0f}")

        if not invariant_ok:
            all_pass = False

        # Day 2: 买入 5000 股
        if day_idx == 1:
            order = OrderEvent(
                timestamp=ts, trace_id=str(uuid.uuid4()), event_id="ord_buy",
                symbol="STOCK001", order_type="BUY", quantity=5000,
                price=price, order_id="order_buy",
            )
            fill = broker.execute_order(order, price)
            old_cash = portfolio.cash
            old_pos_shares = portfolio.positions.get("STOCK001", Position(symbol="STOCK001")).shares if "STOCK001" in portfolio.positions else 0

            portfolio.update_from_fill(fill)

            # 验证买入的数学正确性
            expected_cash = old_cash - price * 5000  # 零成本
            expected_shares = old_pos_shares + 5000

            cash_ok = abs(portfolio.cash - expected_cash) < 1e-6
            shares_ok = abs(portfolio.positions["STOCK001"].shares - expected_shares) < 1e-6

            print(f"  -> 买入 5000 股 @ {price:.0f}")
            print(f"     期望 cash: {expected_cash:.0f}, 实际: {portfolio.cash:.0f}, {'OK' if cash_ok else 'FAIL'}")
            print(f"     期望 shares: {expected_shares:.0f}, 实际: {portfolio.positions['STOCK001'].shares:.0f}, {'OK' if shares_ok else 'FAIL'}")

            all_pass = all_pass and cash_ok and shares_ok

            # 注意：update_from_fill 后 total_value 不变，因为 cash 减少 = 持仓市值增加
            # 但需要手动检查 cash + 持仓市值是否等于原 total_value
            pos = portfolio.positions["STOCK001"]
            pos.update_market_value(price)  # 更新市值
            recomputed_total = portfolio.cash + pos.market_value
            print(f"     重新计算 total = cash({portfolio.cash:.0f}) + mv({pos.market_value:.0f}) = {recomputed_total:.0f}")
            print(f"     交易后 total_value 属性仍为: {portfolio.total_value:.0f} (这是正确的，因为市值不变)")

        # Day 4: 卖出所有持仓
        if day_idx == 3 and "STOCK001" in portfolio.positions:
            pos = portfolio.positions["STOCK001"]
            qty = pos.shares
            avg_cost = pos.avg_cost

            order = OrderEvent(
                timestamp=ts, trace_id=str(uuid.uuid4()), event_id="ord_sell",
                symbol="STOCK001", order_type="SELL", quantity=qty,
                price=price, order_id="order_sell",
            )
            fill = broker.execute_order(order, price)

            old_cash = portfolio.cash
            portfolio.update_from_fill(fill)

            # 验证卖出的数学正确性
            expected_cash = old_cash + price * qty  # 零成本下无佣金和印花税
            # 已实现 P&L = (卖出价 - 成本价) * 数量
            expected_realized_pnl = (price - avg_cost) * qty

            cash_ok = abs(portfolio.cash - expected_cash) < 1e-6
            actual_pnl = portfolio.pnl_by_symbol.get("STOCK001", 0.0)
            pnl_ok = abs(actual_pnl - expected_realized_pnl) < 1.0

            print(f"  -> 卖出 {qty:.0f} 股 @ {price:.0f} (成本 {avg_cost:.2f})")
            print(f"     期望 cash: {expected_cash:.0f}, 实际: {portfolio.cash:.0f}, {'OK' if cash_ok else 'FAIL'}")
            print(f"     期望已实现 P&L: {expected_realized_pnl:.0f}, 实际: {actual_pnl:.0f}, {'OK' if pnl_ok else 'FAIL'}")

            all_pass = all_pass and cash_ok and pnl_ok

            # 检查卖出后是否还有持仓
            if "STOCK001" in portfolio.positions:
                print(f"     警告: 卖出后仍有持仓记录! shares={portfolio.positions['STOCK001'].shares}")
            else:
                print(f"     卖出后无持仓记录 (正确)")

    # 最终总结
    print(f"\n最终 equity_curve: {[f'{v:.0f}' for v in portfolio.equity_curve]}")
    print(f"最终 cash: {portfolio.cash:.0f}")
    print(f"最终 total_value: {portfolio.total_value:.0f}")

    # 计算最终收益率
    final_return = (portfolio.equity_curve[-1] - portfolio.equity_curve[0]) / portfolio.equity_curve[0]
    # 理论收益率：(110 - 100) / 100 * (5000 * 100 / 1_000_000) = 10% * 50% = 5%
    theoretical_return = (110.0 - 100.0) / 100.0 * (5000.0 * 100.0 / 1_000_000.0)

    print(f"\n最终收益率 (引擎): {final_return:.6%}")
    print(f"理论收益率: {theoretical_return:.6%}")
    print(f"Portfolio 状态不变量测试: {all_pass}")

    return all_pass


# ============================================================================
# 主程序
# ============================================================================

def main():
    print("\n" + "=" * 70)
    print("回测引擎数学正确性验证测试套件")
    print("=" * 70)

    results = {}

    results["test_1"] = test_1_buy_and_hold_constant_price()
    results["test_2"] = test_2_buy_and_hold_linear_price()
    results["test_3"] = test_3_capital_conservation()
    results["test_4"] = test_4_realized_pnl_calculation()
    results["test_5"] = test_5_max_drawdown_calculation()
    results["test_6"] = test_6_portfolio_invariant_detailed()

    print("\n" + "=" * 70)
    print("测试总结")
    print("=" * 70)

    for name, passed in results.items():
        status = "✓ 通过" if passed else "✗ 失败"
        print(f"  {name}: {status}")

    all_passed = all(results.values())
    print(f"\n整体结果: {'全部通过 ✓' if all_passed else '存在失败 ✗'}")

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
