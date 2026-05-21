"""投资组合管理器

负责将策略生成的信号转换为具体订单，并管理实时持仓与资金。
"""

import logging
import uuid
from dataclasses import dataclass, field

import polars as pl

from long_earn.backtest.domain.entities import (
    FillEvent,
    OrderEvent,
    Position,
    SignalEvent,
)

logger = logging.getLogger(__name__)


@dataclass
class PortfolioState:
    """账户当前状态快照"""

    cash: float
    positions: dict[str, Position]
    total_value: float
    equity_curve: list[float] = field(default_factory=list)


class Portfolio:
    """
    投资组合实体

    职责：
    1. 接收 SignalEvent → 计算目标持仓 → 生成 OrderEvent。
    2. 接收 FillEvent → 更新持仓与现金。
    3. 每日更新持仓市值。
    """

    def __init__(self, initial_capital: float = 1_000_000.0):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.positions: dict[str, Position] = {}
        self.total_value = initial_capital
        self.equity_curve: list[float] = [initial_capital]
        self.pnl_by_symbol: dict[str, float] = {}
        self.trade_count: int = 0

    def process_signal(
        self,
        event: SignalEvent,
        current_prices: pl.DataFrame,
        max_positions: int = 0,
    ) -> list[OrderEvent]:
        """
        将信号转换为订单

        Args:
            event: 信号事件 (包含目标权重)
            current_prices: 当前时刻所有股票的价格 Slab
            max_positions: 最大持仓数（0 表示不限制）
        """
        target_weights = event.signals

        if not isinstance(target_weights, dict):
            logger.error("Portfolio 目前仅支持 dict 格式的信号权重")
            return []

        # 若超出最大持仓限制，按权重降序保留前 N 个
        if max_positions > 0:
            current_count = len(self.positions)
            new_symbols = [s for s in target_weights if s not in self.positions]
            available = max_positions - current_count
            if available <= 0:
                return []  # 已满仓，不开新仓
            if len(new_symbols) > available:
                new_sorted = sorted(
                    new_symbols, key=lambda s: target_weights.get(s, 0), reverse=True
                )
                for s in new_sorted[available:]:
                    target_weights.pop(s, None)

        orders = []
        for symbol, target_weight in target_weights.items():
            if target_weight <= 0:
                continue

            current_pos = self.positions.get(symbol)

            price_rows = current_prices.filter(pl.col("symbol") == symbol)
            if price_rows.is_empty():
                continue
            price = price_rows.select("close").to_series()[0]
            if price is None or price <= 0:
                continue

            target_val = self.total_value * target_weight
            current_val = current_pos.market_value if current_pos else 0.0
            diff_val = target_val - current_val

            if abs(diff_val) < 1.0:
                continue

            order_type = "BUY" if diff_val > 0 else "SELL"
            qty = abs(diff_val) / price

            orders.append(
                OrderEvent(
                    timestamp=event.timestamp,
                    trace_id=str(uuid.uuid4()),
                    event_id=f"ord_{event.event_id}_{symbol}",
                    symbol=symbol,
                    order_type=order_type,
                    quantity=qty,
                    price=price,
                    order_id=f"ord_{uuid.uuid4().hex[:8]}",
                )
            )

        return orders

    def update_from_fill(self, fill: FillEvent) -> None:
        """根据成交记录更新持仓和现金"""
        symbol = fill.symbol
        cost = fill.fill_price * fill.fill_quantity + fill.commission + fill.stamp_duty
        self.trade_count += 1

        if fill.order_type == "BUY" or "BUY" in fill.order_id:
            self.cash -= cost
            pos = self.positions.setdefault(symbol, Position(symbol=symbol))
            total_cost = (
                pos.avg_cost * pos.shares + fill.fill_price * fill.fill_quantity
            )
            pos.shares += fill.fill_quantity
            pos.avg_cost = total_cost / pos.shares if pos.shares > 0 else 0.0
        else:
            proceeds = fill.fill_price * fill.fill_quantity
            net_proceeds = proceeds - fill.commission - fill.stamp_duty
            self.cash += net_proceeds
            if symbol in self.positions:
                pos = self.positions[symbol]
                pos.shares -= fill.fill_quantity
                # 计算已实现 P&L
                realized_pnl = proceeds - (pos.avg_cost * fill.fill_quantity)
                self.pnl_by_symbol[symbol] = (
                    self.pnl_by_symbol.get(symbol, 0.0) + realized_pnl
                )
                if pos.shares <= 0:
                    del self.positions[symbol]

    def update_market_values(self, current_prices: pl.DataFrame) -> None:
        """根据当前价格更新所有持仓市值"""
        for symbol, pos in self.positions.items():
            price_rows = current_prices.filter(pl.col("symbol") == symbol)
            if not price_rows.is_empty():
                price = price_rows.select("close").to_series()[0]
                pos.update_market_value(price)
                pos.current_price = price

                # 更新未实现 P&L
                unrealized = (price - pos.avg_cost) * pos.shares
                self.pnl_by_symbol[symbol] = unrealized

        self.total_value = self.cash + sum(
            p.market_value for p in self.positions.values()
        )
        self.equity_curve.append(self.total_value)
