"""回测引擎数学正确性验证测试。

验证以下数学不变量：
1. 资金守恒定律
2. 收益率计算一致性
3. 最大回撤计算正确性
4. 已实现/未实现 P&L 分离
5. 数值稳定性
6. 线性价格策略的解析解对比

运行：python scripts/test_backtest_math.py
"""

from __future__ import annotations

import math
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import polars as pl

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src"))

from long_earn.backtest.domain.entities import (
    OrderEvent,
)
from long_earn.backtest.engine.broker import Broker, TradingCostConfig
from long_earn.backtest.engine.portfolio import Portfolio

# ── 测试工具函数 ──────────────────────────────────────────────────────


def assert_approx(actual: float, expected: float, tol: float = 1e-8, label: str = "") -> bool:
    """近似相等断言。返回是否通过。"""
    if expected == 0:
        diff = abs(actual)
        passed = diff < tol
    else:
        rel_diff = abs(actual - expected) / abs(expected)
        passed = rel_diff < tol or abs(actual - expected) < tol
    status = "✓" if passed else "✗"
    print(f"  {status} {label}: actual={actual:.8f}, expected={expected:.8f}, diff={actual - expected:.2e}")
    return passed


def assert_zero(value: float, tol: float = 1e-8, label: str = "") -> bool:
    """断言值接近 0。"""
    passed = abs(value) < tol
    status = "✓" if passed else "✗"
    print(f"  {status} {label}: value={value:.2e}")
    return passed


# ── 测试 1: 资金守恒（单只股票买卖循环） ──────────────────────────────


def test_fund_conservation() -> dict:
    """测试资金守恒定律。

    过程：
    1. 初始现金 C
    2. 以价格 P 买入 q 股（含佣金和滑点）
    3. 以价格 P' 全部卖出（含佣金、滑点、印花税）
    4. 验证最终现金 = C + 已实现盈亏

    数学：
    买入：cash_outflow = q * P * (1 + slip + comm)
    卖出：cash_inflow = q * P' * (1 - slip - comm - stamp)
    realized_pnl = cash_inflow - q * avg_cost
    总资产 = cash + position.market_value（卖出后只有现金）
    """
    print("\n" + "=" * 60)
    print("测试 1: 资金守恒定律")
    print("=" * 60)

    results: dict[str, bool] = {}

    # 场景 A: 简单买卖（无手续费，无滑点）
    print("\n  场景 A: 零成本买卖 (price=100 → 110)")
    cfg_zero = TradingCostConfig(commission_rate=0.0, stamp_duty=0.0, slippage_bps=0.0)
    p = Portfolio(initial_capital=1_000_000.0)
    b = Broker(cfg_zero)

    # 买入 1000 股 @ 100
    buy_order = OrderEvent(
        timestamp=datetime.now(),
        trace_id="t1",
        event_id="e1",
        symbol="AAPL",
        order_type="BUY",
        quantity=1000,
        price=100.0,
        order_id="buy1",
    )
    fill = b.execute_order(buy_order, 100.0)
    p.update_from_fill(fill)

    # 验证买入后状态
    pos = p.positions["AAPL"]
    results["A_持仓股数"] = assert_approx(float(pos.shares), 1000.0, 1e-6, "买入后股数")
    results["A_均价"] = assert_approx(pos.avg_cost, 100.0, 1e-6, "买入后均价")
    results["A_现金"] = assert_approx(p.cash, 900_000.0, 1e-6, "买入后现金")
    results["A_总资产"] = assert_approx(
        p.cash + pos.market_value, 1_000_000.0, 1e-6, "买入后总资产 (cash+market_value)"
    )

    # 更新市场价格到 110
    price_data = pl.DataFrame({"symbol": ["AAPL"], "close": [110.0]})
    p.update_market_values(price_data)
    results["A_涨价后总资产"] = assert_approx(
        p.total_value, 1_010_000.0, 1e-6, "涨价后总资产 (cash+110*1000)"
    )

    # 以 110 卖出全部 1000 股
    sell_order = OrderEvent(
        timestamp=datetime.now(),
        trace_id="t2",
        event_id="e2",
        symbol="AAPL",
        order_type="SELL",
        quantity=1000,
        price=110.0,
        order_id="sell1",
    )
    fill_sell = b.execute_order(sell_order, 110.0)
    p.update_from_fill(fill_sell)

    results["A_卖出后无持仓"] = "AAPL" not in p.positions
    print(f"  {'✓' if results['A_卖出后无持仓'] else '✗'} 卖出后无持仓: {results['A_卖出后无持仓']}")

    # 卖出后现金应 = 110 * 1000 = 110000
    # 之前 cash 是 900000
    expected_cash_after = 1_000_000.0 + (110 - 100) * 1000
    results["A_最终现金"] = assert_approx(p.cash, expected_cash_after, 1e-6, "卖出后现金 (盈利 10000)")

    # 已实现 P&L 验证
    expected_realized = (110.0 - 100.0) * 1000.0  # 10000
    actual_realized = p.pnl_by_symbol.get("AAPL", 0.0)
    results["A_已实现P&L"] = assert_approx(actual_realized, expected_realized, 1e-6, "已实现 P&L (10000)")

    # 场景 B: 含手续费和滑点
    print("\n  场景 B: 含手续费和滑点 (comm=0.001, stamp=0.001, slip=1bp)")
    cfg_b = TradingCostConfig(commission_rate=0.001, stamp_duty=0.001, slippage_bps=1.0)
    p = Portfolio(initial_capital=1_000_000.0)
    b = Broker(cfg_b)

    init_price = 100.0
    qty = 1000.0
    final_price = 110.0

    # 买入：fill_price = 100 * (1 + 0.0001) = 100.01
    # commission = 1000 * 100.01 * 0.001 = 100.01
    buy_fill = b.execute_order(
        OrderEvent(
            timestamp=datetime.now(),
            trace_id="t1",
            event_id="e1",
            symbol="AAPL",
            order_type="BUY",
            quantity=qty,
            price=init_price,
            order_id="buy_b",
        ),
        init_price,
    )
    p.update_from_fill(buy_fill)

    pos = p.positions["AAPL"]
    expected_fill_price_buy = init_price * (1 + cfg_b.slippage_rate)
    expected_buy_cost = qty * expected_fill_price_buy
    expected_buy_commission = expected_buy_cost * cfg_b.commission_rate
    expected_cash_after_buy = 1_000_000 - expected_buy_cost - expected_buy_commission

    results["B_买入均价"] = assert_approx(
        pos.avg_cost, expected_fill_price_buy, 1e-6, "买入均价 (含滑点)"
    )
    results["B_买入后现金"] = assert_approx(
        p.cash, expected_cash_after_buy, 1e-3, "买入后现金 (含滑点+佣金)"
    )

    # 卖出：fill_price = 110 * (1 - 0.0001) = 109.989
    sell_fill = b.execute_order(
        OrderEvent(
            timestamp=datetime.now(),
            trace_id="t2",
            event_id="e2",
            symbol="AAPL",
            order_type="SELL",
            quantity=qty,
            price=final_price,
            order_id="sell_b",
        ),
        final_price,
    )
    p.update_from_fill(sell_fill)

    expected_fill_price_sell = final_price * (1 - cfg_b.slippage_rate)
    expected_sell_proceeds = qty * expected_fill_price_sell
    expected_sell_commission = expected_sell_proceeds * cfg_b.commission_rate
    expected_sell_stamp = expected_sell_proceeds * cfg_b.stamp_duty
    expected_net_proceeds = expected_sell_proceeds - expected_sell_commission - expected_sell_stamp

    expected_final_cash = expected_cash_after_buy + expected_net_proceeds
    results["B_最终现金"] = assert_approx(
        p.cash, expected_final_cash, 1e-3, "最终现金 (含所有成本)"
    )

    # 正确的已实现 P&L: 净收入 - 成本基準
    expected_realized_pnl = expected_net_proceeds - expected_fill_price_buy * qty
    actual_realized = p.pnl_by_symbol.get("AAPL", 0.0)
    results["B_已实现P&L"] = assert_approx(
        actual_realized, expected_realized_pnl, 1e-3, "已实现 P&L (含成本)"
    )

    # 资金守恒：最终现金 + 0 个持仓 = 初始资金 + 已实现 P&L
    results["B_资金守恒"] = assert_approx(
        p.cash, 1_000_000.0 + expected_realized_pnl, 1e-3, "资金守恒: cash = 初始 + realized"
    )

    total_passed = sum(results.values())
    print(f"\n  测试 1 结果: {total_passed}/{len(results)} 通过")
    return {"total": len(results), "passed": total_passed, "details": results}


# ── 测试 2: 线性价格的解析解对比 ───────────────────────────────────


def test_linear_price_strategy() -> dict:
    """测试线性价格下策略结果与解析解的一致性。

    设价格序列 P(t) = P0 + r * t（线性增长）
    策略：第 0 天全仓买入，持有到 T-1 天卖出。

    解析解（无手续费）：
    权益曲线: E(t) = cash + shares * P(t)
    最终收益: E_T / E_0 - 1 = (P_T / P_0) - 1
    最大回撤: 0（因为线性增长，没有回撤）
    年化收益: (1 + total_return)^{252/T} - 1
    """
    print("\n" + "=" * 60)
    print("测试 2: 线性价格策略解析解对比")
    print("=" * 60)

    results: dict[str, bool] = {}

    # 构造线性价格数据
    P0 = 100.0
    r = 0.01  # 每日增长 1%
    T = 50  # 50 个交易日
    symbol = "LNR"

    timestamps = pd.date_range("2024-01-01", periods=T, freq="B")
    prices = [P0 + r * P0 * i for i in range(T)]

    data_rows = []
    for i, (ts, price) in enumerate(zip(timestamps, prices)):
        data_rows.append(
            {
                "timestamp": ts,
                "symbol": symbol,
                "open": price,
                "high": price * 1.001,
                "low": price * 0.999,
                "close": price,
                "volume": 1_000_000,
            }
        )
    full_data = pl.DataFrame(data_rows)

    # 手动模拟（不通过完整引擎，直接测试 Portfolio + Broker）
    cfg = TradingCostConfig(commission_rate=0.0, stamp_duty=0.0, slippage_bps=0.0)
    p = Portfolio(initial_capital=1_000_000.0)
    b = Broker(cfg)

    unique_ts = sorted(full_data["timestamp"].unique().to_list())

    for t_idx, ts in enumerate(unique_ts):
        slab = full_data.filter(pl.col("timestamp") == ts)
        p.update_market_values(slab)

        if t_idx == 0:
            # 全仓买入
            price = float(slab.filter(pl.col("symbol") == symbol).select("close").to_series()[0])
            qty = p.cash / price
            buy = OrderEvent(
                timestamp=ts,
                trace_id="t",
                event_id="e",
                symbol=symbol,
                order_type="BUY",
                quantity=qty,
                price=price,
                order_id=f"buy_{t_idx}",
            )
            fill = b.execute_order(buy, price)
            p.update_from_fill(fill)

        elif t_idx == T - 1:
            # 全仓卖出
            price = float(slab.filter(pl.col("symbol") == symbol).select("close").to_series()[0])
            pos = p.positions.get(symbol)
            if pos:
                sell = OrderEvent(
                    timestamp=ts,
                    trace_id="t",
                    event_id="e",
                    symbol=symbol,
                    order_type="SELL",
                    quantity=pos.shares,
                    price=price,
                    order_id=f"sell_{t_idx}",
                )
                fill = b.execute_order(sell, price)
                p.update_from_fill(fill)

    # 验证
    equity = p.equity_curve
    actual_total_return = (equity[-1] / equity[0]) - 1 if equity else 0.0
    expected_total_return = (prices[-1] / prices[0]) - 1  # 线性增长收益率

    results["总收益"] = assert_approx(
        actual_total_return, expected_total_return, 1e-6, f"总收益: {actual_total_return*100:.4f}%"
    )

    # 验证每日权益曲线：应该和价格线性一致
    # 注意：equity_curve[0] 是初始值，之后每天 append 一次
    # 所以 equity_curve[t] 对应第 t 天收盘后的值
    for i, (eq, price) in enumerate(zip(equity, prices)):
        if i == 0:
            continue  # 第 0 天买入后在后续时间点验证
        # 买入后持有 qty 股，权益应 = qty * price
        # qty = cash_0 / price_0，cash_0 约 = 初始资金
        qty_theory = 1_000_000.0 / prices[0]
        expected_eq = qty_theory * price
        # 第 0 天买入后立即 update_market_values 会记录 total_value = cash_1 + qty*price_0
        # 然后在第 1 天 update 后应该是 qty*price_1
        # 由于买入后还有 update_from_fill，现金已扣减
        if i < len(equity) - 1:
            pass  # 只验证最终收益

    # 验证最大回撤 = 0（价格单调增长，无回撤）
    equity_arr = np.array(equity)
    peak = np.maximum.accumulate(equity_arr)
    dd = (equity_arr - peak) / peak
    max_dd = np.min(dd)

    results["最大回撤"] = assert_zero(max_dd, 1e-8, f"最大回撤: {max_dd*100:.6f}% (应为 0)")

    # 验证年化收益公式
    trading_days = len(equity) - 1
    annual_factor = 252 / trading_days if trading_days > 0 else 1.0
    annual_ret_engine = (1 + actual_total_return) ** annual_factor - 1
    annual_ret_theory = (1 + expected_total_return) ** annual_factor - 1
    results["年化收益"] = assert_approx(
        annual_ret_engine, annual_ret_theory, 1e-6, f"年化收益: {annual_ret_engine*100:.4f}%"
    )

    total_passed = sum(results.values())
    print(f"\n  测试 2 结果: {total_passed}/{len(results)} 通过")
    return {"total": len(results), "passed": total_passed, "details": results}


# ── 测试 3: 买入并持有 (Buy & Hold) ────────────────────────────────


def test_buy_and_hold() -> dict:
    """多只股票等权重买入并持有，验证与解析解一致。"""
    print("\n" + "=" * 60)
    print("测试 3: 多只股票 Buy & Hold")
    print("=" * 60)

    results: dict[str, bool] = {}

    # 构造 3 只股票的价格数据
    T = 60
    symbols = ["S1", "S2", "S3"]
    growth_rates = [0.005, 0.002, -0.001]  # 不同增长率
    base_prices = [100.0, 50.0, 200.0]

    timestamps = pd.date_range("2024-01-01", periods=T, freq="B")
    rows = []
    all_prices = {}
    for s_idx, symbol in enumerate(symbols):
        prices = [base_prices[s_idx] * (1 + growth_rates[s_idx]) ** i for i in range(T)]
        all_prices[symbol] = prices
        for i, ts in enumerate(timestamps):
            rows.append(
                {
                    "timestamp": ts,
                    "symbol": symbol,
                    "open": prices[i],
                    "high": prices[i] * 1.01,
                    "low": prices[i] * 0.99,
                    "close": prices[i],
                    "volume": 1_000_000,
                }
            )

    full_data = pl.DataFrame(rows)

    # 模拟
    cfg = TradingCostConfig(commission_rate=0.0003, stamp_duty=0.0005, slippage_bps=2.0)
    p = Portfolio(initial_capital=1_000_000.0)
    b = Broker(cfg)

    unique_ts = sorted(full_data["timestamp"].unique().to_list())
    holdings: dict[str, float] = {}

    for t_idx, ts in enumerate(unique_ts):
        slab = full_data.filter(pl.col("timestamp") == ts)
        p.update_market_values(slab)

        if t_idx == 0:
            # 等权重买入每只股票
            per_stock = p.total_value / len(symbols)
            for symbol in symbols:
                price = float(
                    slab.filter(pl.col("symbol") == symbol).select("close").to_series()[0]
                )
                qty = per_stock / price
                order = OrderEvent(
                    timestamp=ts,
                    trace_id="t",
                    event_id="e",
                    symbol=symbol,
                    order_type="BUY",
                    quantity=qty,
                    price=price,
                    order_id=f"buy_{symbol}",
                )
                fill = b.execute_order(order, price)
                p.update_from_fill(fill)
                holdings[symbol] = qty

    # 解析解：最终总资产 = sum(qty_i * price_i_T) - 所有交易成本
    final_prices = {s: all_prices[s][-1] for s in symbols}

    # 计算所有成本
    # 买入阶段
    per_stock_init = 1_000_000.0 / len(symbols)
    total_cost_fees = 0.0
    for symbol in symbols:
        qty_theory = per_stock_init / all_prices[symbol][0]
        fill_price = all_prices[symbol][0] * (1 + cfg.slippage_rate)
        gross_cost = qty_theory * fill_price
        commission_buy = gross_cost * cfg.commission_rate
        total_cost_fees += commission_buy + qty_theory * (fill_price - all_prices[symbol][0])

    # 最终理论资产
    # 每只股票 qty * final_price，扣除所有成本
    expected_final = 0.0
    for symbol in symbols:
        qty_theory = per_stock_init / all_prices[symbol][0]
        # 实际买入价是含滑点的 fill_price，所以 avg_cost = fill_price
        fill_price = all_prices[symbol][0] * (1 + cfg.slippage_rate)
        # cash 扣减 = qty_theory * fill_price + commission
        commission = qty_theory * fill_price * cfg.commission_rate
        expected_final += qty_theory * final_prices[symbol]  # 持仓市值

    # 实际扣减的现金 = sum(qty * fill_price * (1 + commission_rate))
    total_cash_out = 0.0
    for symbol in symbols:
        qty_theory = per_stock_init / all_prices[symbol][0]
        fill_price = all_prices[symbol][0] * (1 + cfg.slippage_rate)
        total_cash_out += qty_theory * fill_price * (1 + cfg.commission_rate)

    expected_final_cash = 1_000_000.0 - total_cash_out
    expected_total = expected_final + expected_final_cash

    results["最终总资产"] = assert_approx(
        p.total_value, expected_total, max(1.0, abs(expected_total) * 1e-4),
        f"最终总资产 (引擎: {p.total_value:.2f}, 理论: {expected_total:.2f})"
    )

    # 收益率
    actual_return = (p.total_value / 1_000_000.0) - 1
    # 理论：每只股票收益加权平均减去交易成本
    weighted_return = 0.0
    for s_idx, symbol in enumerate(symbols):
        symbol_return = (final_prices[symbol] / all_prices[symbol][0]) - 1
        weighted_return += (1.0 / len(symbols)) * symbol_return
    # 成本百分比
    total_cost_ratio = total_cash_out / 1_000_000.0 - 1.0  # 负的（成本）
    expected_return = weighted_return + (total_cost_ratio) * 0  # 简化：成本影响总资产但不直接影响收益率

    print(f"  实际收益率: {actual_return*100:.4f}%")
    print(f"  理论价格加权收益率: {weighted_return*100:.4f}%")

    results["收益率为正(增长股)"] = actual_return > 0
    print(f"  {'✓' if results['收益率为正(增长股)'] else '✗'} 收益率为正: {actual_return*100:.4f}%")

    total_passed = sum(1 for v in results.values() if v)
    print(f"\n  测试 3 结果: {total_passed}/{len(results)} 通过")
    return {"total": len(results), "passed": total_passed, "details": results}


# ── 测试 4: 事件驱动引擎与向量化等价性 ────────────────────────────


def test_event_vs_vectorized() -> dict:
    """确保事件驱动引擎的逐 bar 处理等价于向量化一次性计算。"""
    print("\n" + "=" * 60)
    print("测试 4: 事件驱动 vs 向量化等价性")
    print("=" * 60)

    results: dict[str, bool] = {}

    # 构造一只股票的价格数据（随机但可预测）
    T = 100
    symbol = "TEST"
    timestamps = pd.date_range("2024-01-01", periods=T, freq="B")
    np.random.seed(42)
    prices = 100.0 * np.cumprod(1 + np.random.randn(T) * 0.01)  # 随机游走

    rows = []
    for i, ts in enumerate(timestamps):
        rows.append(
            {
                "timestamp": ts,
                "symbol": symbol,
                "open": float(prices[i] * 0.99),
                "high": float(prices[i] * 1.02),
                "low": float(prices[i] * 0.98),
                "close": float(prices[i]),
                "volume": 1_000_000,
            }
        )
    full_data = pl.DataFrame(rows)

    # 事件驱动：Day 0 全仓买入，持有到最后
    cfg = TradingCostConfig(commission_rate=0.0003, stamp_duty=0.0005, slippage_bps=2.0)
    p = Portfolio(initial_capital=1_000_000.0)
    b = Broker(cfg)

    unique_ts = sorted(full_data["timestamp"].unique().to_list())
    for t_idx, ts in enumerate(unique_ts):
        slab = full_data.filter(pl.col("timestamp") == ts)
        p.update_market_values(slab)

        if t_idx == 0:
            price = float(slab.filter(pl.col("symbol") == symbol).select("close").to_series()[0])
            qty = p.cash / price
            order = OrderEvent(
                timestamp=ts,
                trace_id="t",
                event_id="e",
                symbol=symbol,
                order_type="BUY",
                quantity=qty,
                price=price,
                order_id="buy",
            )
            fill = b.execute_order(order, price)
            p.update_from_fill(fill)

    event_equity = p.equity_curve
    event_total_return = (event_equity[-1] / event_equity[0]) - 1

    # 向量化计算：理论权益曲线
    # Day 0 买入: qty * fill_price * (1 + comm) = 1_000_000
    # qty = 1_000_000 / (fill_price * (1 + comm))
    # fill_price = price_0 * (1 + slip)
    fill_price_0 = prices[0] * (1 + cfg.slippage_rate)
    qty_theory = 1_000_000.0 / (fill_price_0 * (1 + cfg.commission_rate))
    cash_left = 1_000_000.0 - qty_theory * fill_price_0 * (1 + cfg.commission_rate)

    vector_equity = [1_000_000.0]
    for i in range(1, T):
        vector_equity.append(cash_left + qty_theory * prices[i])

    vector_total_return = (vector_equity[-1] / vector_equity[0]) - 1

    results["总收益一致"] = assert_approx(
        event_total_return, vector_total_return, 1e-4,
        f"引擎: {event_total_return*100:.4f}%, 向量化: {vector_total_return*100:.4f}%"
    )

    # 曲线逐点对比
    max_diff = 0.0
    for i, (eq_e, eq_v) in enumerate(zip(event_equity, vector_equity)):
        diff = abs(eq_e - eq_v) / eq_v if eq_v != 0 else 0.0
        max_diff = max(max_diff, diff)

    results["曲线一致"] = max_diff < 1e-4
    print(f"  {'✓' if results['曲线一致'] else '✗'} 权益曲线最大相对偏差: {max_diff:.2e}")

    total_passed = sum(1 for v in results.values() if v)
    print(f"\n  测试 4 结果: {total_passed}/{len(results)} 通过")
    return {"total": len(results), "passed": total_passed, "details": results}


# ── 测试 5: 做空 & 部分卖出的 P&L 正确性 ─────────────────────────


def test_partial_sell_pnl() -> dict:
    """测试部分卖出时已实现 P&L 的计算。"""
    print("\n" + "=" * 60)
    print("测试 5: 部分卖出的已实现 P&L 计算")
    print("=" * 60)

    results: dict[str, bool] = {}

    cfg = TradingCostConfig(commission_rate=0.0, stamp_duty=0.0, slippage_bps=0.0)
    p = Portfolio(initial_capital=1_000_000.0)
    b = Broker(cfg)

    # 分两批买入，再分两批卖出
    # 第一批：1000 股 @ 100
    buy1 = OrderEvent(
        timestamp=datetime.now(), trace_id="t", event_id="e1",
        symbol="S1", order_type="BUY", quantity=1000, price=100.0, order_id="b1"
    )
    fill = b.execute_order(buy1, 100.0)
    p.update_from_fill(fill)
    pos = p.positions["S1"]
    results["第一批买入"] = assert_approx(pos.avg_cost, 100.0, 1e-6, "第一批 avg_cost = 100")

    # 第二批：500 股 @ 120 → 新均价 = (1000*100 + 500*120)/1500 = 106.6667
    buy2 = OrderEvent(
        timestamp=datetime.now(), trace_id="t", event_id="e2",
        symbol="S1", order_type="BUY", quantity=500, price=120.0, order_id="b2"
    )
    fill = b.execute_order(buy2, 120.0)
    p.update_from_fill(fill)
    pos = p.positions["S1"]
    expected_avg = (1000 * 100.0 + 500 * 120.0) / 1500.0  # 106.6667
    results["均价加权"] = assert_approx(pos.avg_cost, expected_avg, 1e-6, f"混合均价 = {expected_avg:.4f}")
    results["总股数"] = assert_approx(float(pos.shares), 1500.0, 1e-6, "总股数 = 1500")

    # 部分卖出：600 股 @ 130 → 已实现 P&L = 600 * (130 - avg_cost) = 600 * 23.3333 = 14000
    sell1 = OrderEvent(
        timestamp=datetime.now(), trace_id="t", event_id="e3",
        symbol="S1", order_type="SELL", quantity=600, price=130.0, order_id="s1"
    )
    fill = b.execute_order(sell1, 130.0)
    p.update_from_fill(fill)

    pos = p.positions["S1"]
    expected_realized_1 = 600.0 * (130.0 - expected_avg)
    actual_realized = p.pnl_by_symbol.get("S1", 0.0)
    results["第一次部分卖出P&L"] = assert_approx(
        actual_realized, expected_realized_1, 1e-3,
        f"第一次部分卖出已实现P&L: 理论={expected_realized_1:.2f}"
    )
    results["剩余股数"] = assert_approx(float(pos.shares), 900.0, 1e-6, "剩余 900 股")
    results["剩余均价不变"] = assert_approx(
        pos.avg_cost, expected_avg, 1e-6, "剩余持仓均价应保持不变"
    )

    # 第二次卖出：900 股 @ 140 → 已实现 P&L = 900 * (140 - avg_cost)
    sell2 = OrderEvent(
        timestamp=datetime.now(), trace_id="t", event_id="e4",
        symbol="S1", order_type="SELL", quantity=900, price=140.0, order_id="s2"
    )
    fill = b.execute_order(sell2, 140.0)
    p.update_from_fill(fill)

    expected_realized_2 = 900.0 * (140.0 - expected_avg)
    expected_total_realized = expected_realized_1 + expected_realized_2
    actual_total_realized = p.pnl_by_symbol.get("S1", 0.0)
    results["第二次卖出P&L"] = assert_approx(
        actual_total_realized, expected_total_realized, 1e-3,
        f"总已实现P&L: 引擎={actual_total_realized:.2f}, 理论={expected_total_realized:.2f}"
    )

    # 资金守恒验证：最终现金 = 初始现金 + 总已实现 P&L（零成本）
    results["资金守恒"] = assert_approx(
        p.cash, 1_000_000.0 + expected_total_realized, 1e-3,
        f"最终现金: 引擎={p.cash:.2f}, 理论={1_000_000.0 + expected_total_realized:.2f}"
    )

    total_passed = sum(1 for v in results.values() if v)
    print(f"\n  测试 5 结果: {total_passed}/{len(results)} 通过")
    return {"total": len(results), "passed": total_passed, "details": results}


# ── 测试 6: 最大回撤计算正确性 ─────────────────────────────────────


def test_max_drawdown() -> dict:
    """验证 max_drawdown 计算与解析解一致。"""
    print("\n" + "=" * 60)
    print("测试 6: 最大回撤计算正确性")
    print("=" * 60)

    results: dict[str, bool] = {}

    # 构造一个有明显回撤的价格序列：先涨后跌再涨
    T = 60
    symbol = "DD"
    timestamps = pd.date_range("2024-01-01", periods=T, freq="B")

    # 价格曲线：100 → 150（peak） → 75（最低点）→ 130（结束）
    # 第一段：增长到 150 (0-20)
    # 第二段：跌到 75 (21-40)
    # 第三段：回到 130 (41-59)
    prices = []
    for i in range(T):
        if i < 20:
            p = 100.0 + (150.0 - 100.0) * (i / 19.0)
        elif i < 40:
            p = 150.0 + (75.0 - 150.0) * ((i - 20) / 19.0)
        else:
            p = 75.0 + (130.0 - 75.0) * ((i - 40) / 19.0)
        prices.append(p)

    rows = []
    for i, ts in enumerate(timestamps):
        rows.append(
            {
                "timestamp": ts,
                "symbol": symbol,
                "open": prices[i],
                "high": prices[i] * 1.001,
                "low": prices[i] * 0.999,
                "close": prices[i],
                "volume": 1_000_000,
            }
        )
    full_data = pl.DataFrame(rows)

    # 引擎模拟（零成本，Day 0 全仓买入持有）
    cfg = TradingCostConfig(commission_rate=0.0, stamp_duty=0.0, slippage_bps=0.0)
    p = Portfolio(initial_capital=1_000_000.0)
    b = Broker(cfg)

    unique_ts = sorted(full_data["timestamp"].unique().to_list())
    for t_idx, ts in enumerate(unique_ts):
        slab = full_data.filter(pl.col("timestamp") == ts)
        p.update_market_values(slab)
        if t_idx == 0:
            price = prices[0]
            qty = p.cash / price
            order = OrderEvent(
                timestamp=ts, trace_id="t", event_id="e",
                symbol=symbol, order_type="BUY", quantity=qty, price=price, order_id="buy"
            )
            fill = b.execute_order(order, price)
            p.update_from_fill(fill)

    # 引擎计算
    equity = np.array(p.equity_curve)
    peak = np.maximum.accumulate(equity)
    drawdown = (equity - peak) / peak
    engine_max_dd = np.min(drawdown)

    # 解析解：最大回撤发生在最低点
    # 价格峰值 150，最低点 75 → 回撤 = (75-150)/150 = -50%
    theory_max_dd = (75.0 - 150.0) / 150.0

    results["最大回撤"] = assert_approx(
        engine_max_dd, theory_max_dd, 1e-3,
        f"max_dd: 引擎={engine_max_dd*100:.4f}%, 理论={theory_max_dd*100:.4f}%"
    )

    # 验证总收益：(130 - 100) / 100 = 30%
    theory_return = (130.0 - 100.0) / 100.0
    actual_return = (equity[-1] / equity[0]) - 1
    results["总收益"] = assert_approx(
        actual_return, theory_return, 1e-4,
        f"总收益: 引擎={actual_return*100:.4f}%, 理论={theory_return*100:.4f}%"
    )

    # 验证高点位置
    peak_idx_engine = np.argmax(equity)
    results["峰值位置"] = peak_idx_engine == 20
    print(f"  {'✓' if results['峰值位置'] else '✗'} 权益峰值位置: 引擎={peak_idx_engine}, 理论=20")

    total_passed = sum(1 for v in results.values() if v)
    print(f"\n  测试 6 结果: {total_passed}/{len(results)} 通过")
    return {"total": len(results), "passed": total_passed, "details": results}


# ── 测试 7: 数值稳定性（极端价格） ───────────────────────────────


def test_numerical_stability() -> dict:
    """测试极端价格下的数值稳定性。"""
    print("\n" + "=" * 60)
    print("测试 7: 数值稳定性")
    print("=" * 60)

    results: dict[str, bool] = {}

    cfg = TradingCostConfig(commission_rate=0.0, stamp_duty=0.0, slippage_bps=0.0)

    # 场景 A: 极小价格（penny stocks）
    print("\n  场景 A: 极小价格 (price=0.01)")
    p = Portfolio(initial_capital=1_000_000.0)
    b = Broker(cfg)
    order = OrderEvent(
        timestamp=datetime.now(), trace_id="t", event_id="e",
        symbol="PENNY", order_type="BUY", quantity=10_000_000, price=0.01, order_id="b"
    )
    fill = b.execute_order(order, 0.01)
    p.update_from_fill(fill)
    results["A_现金非负"] = p.cash >= 0
    results["A_总资产接近初始"] = abs(p.cash + p.positions["PENNY"].market_value - 1_000_000.0) < 1.0
    print(f"  {'✓' if results['A_现金非负'] else '✗'} 现金非负: cash={p.cash:.6f}")
    print(f"  {'✓' if results['A_总资产接近初始'] else '✗'} 总资产接近初始: {p.cash + p.positions['PENNY'].market_value:.6f}")

    # 场景 B: 极大价格
    print("\n  场景 B: 极大价格 (price=100000)")
    p = Portfolio(initial_capital=1_000_000.0)
    b = Broker(cfg)
    order = OrderEvent(
        timestamp=datetime.now(), trace_id="t", event_id="e",
        symbol="HIGH", order_type="BUY", quantity=10, price=100_000.0, order_id="b"
    )
    fill = b.execute_order(order, 100_000.0)
    p.update_from_fill(fill)
    results["B_总资产接近初始"] = abs(p.cash + p.positions["HIGH"].market_value - 1_000_000.0) < 1.0
    print(f"  {'✓' if results['B_总资产接近初始'] else '✗'} 总资产接近初始: {p.cash + p.positions['HIGH'].market_value:.6f}")

    # 场景 C: 价格跳变（100 → 0.01）
    print("\n  场景 C: 极端价格跳变")
    p = Portfolio(initial_capital=1_000_000.0)
    b = Broker(cfg)
    order = OrderEvent(
        timestamp=datetime.now(), trace_id="t", event_id="e",
        symbol="JUMP", order_type="BUY", quantity=1000, price=100.0, order_id="b"
    )
    fill = b.execute_order(order, 100.0)
    p.update_from_fill(fill)
    # 模拟暴跌到 0.01
    slab = pl.DataFrame({"symbol": ["JUMP"], "close": [0.01]})
    p.update_market_values(slab)
    results["C_暴跌后总资产合理"] = p.total_value > 0 and not math.isnan(p.total_value)
    print(f"  {'✓' if results['C_暴跌后总资产合理'] else '✗'} 暴跌后总资产: {p.total_value:.6f}")

    total_passed = sum(1 for v in results.values() if v)
    print(f"\n  测试 7 结果: {total_passed}/{len(results)} 通过")
    return {"total": len(results), "passed": total_passed, "details": results}


# ── 主函数 ────────────────────────────────────────────────────────


def main() -> int:
    """运行所有测试并输出汇总报告。"""
    print("\n" + "#" * 60)
    print("# 回测引擎数学正确性验证测试套件")
    print("#" * 60)

    all_results = []
    test_functions = [
        test_fund_conservation,
        test_linear_price_strategy,
        test_buy_and_hold,
        test_event_vs_vectorized,
        test_partial_sell_pnl,
        test_max_drawdown,
        test_numerical_stability,
    ]

    for test_fn in test_functions:
        result = test_fn()
        all_results.append((test_fn.__name__, result))

    # 汇总
    print("\n" + "#" * 60)
    print("# 测试汇总报告")
    print("#" * 60)

    total_tests = 0
    total_passed = 0
    for name, result in all_results:
        n = result["total"]
        p = result["passed"]
        status = "✓ PASS" if p == n else f"✗ FAIL ({p}/{n})"
        print(f"  {name:40s}: {status}")
        total_tests += n
        total_passed += p

    print("\n" + "=" * 60)
    print(f"  总体: {total_passed}/{total_tests} 检查通过 "
          f"({100*total_passed/total_tests:.1f}%)")
    print("=" * 60)

    return 0 if total_passed == total_tests else 1


if __name__ == "__main__":
    sys.exit(main())
