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
        self.equity_curve: list[float] = []
        self.peak_value: float = initial_capital
        self.realized_pnl: dict[str, float] = {}
        self.pnl_by_symbol: dict[str, float] = {}
        self.trade_count: int = 0

    def process_signal(
        self,
        event: SignalEvent,
        current_prices: pl.DataFrame,
        max_positions: int = 0,
        max_position_pct: float = 1.0,
    ) -> list[OrderEvent]:
        """将信号转换为订单

        Args:
            event: 信号事件 (包含目标权重)
            current_prices: 当前时刻所有股票的价格 Slab
            max_positions: 最大持仓数（0 表示不限制）
            max_position_pct: 单只股票最大仓位比例
        """
        target_weights = event.signals

        if not isinstance(target_weights, dict):
            logger.error("Portfolio 目前仅支持 dict 格式的信号权重")
            return []

        target_weights = self._apply_position_limits(
            target_weights, max_positions
        )

        order_infos = self._compute_order_infos(
            target_weights, current_prices, max_position_pct
        )

        return self._generate_orders(order_infos, event)

    def _apply_position_limits(
        self,
        target_weights: dict[str, float],
        max_positions: int,
    ) -> dict[str, float]:
        """应用持仓数量限制，返回裁剪后的目标权重"""
        if max_positions <= 0:
            return target_weights

        current_count = len(self.positions)
        new_symbols = [s for s in target_weights if s not in self.positions]
        available = max_positions - current_count
        if available <= 0:
            return {}  # 已满仓，不开新仓
        if len(new_symbols) > available:
            new_sorted = sorted(
                new_symbols, key=lambda s: target_weights.get(s, 0), reverse=True
            )
            for s in new_sorted[available:]:
                target_weights.pop(s, None)
        return target_weights

    def _compute_order_infos(
        self,
        target_weights: dict[str, float],
        current_prices: pl.DataFrame,
        max_position_pct: float,
    ) -> list[dict]:
        """计算每个标的的目标订单信息（方向、金额、价格）"""
        order_infos: list[dict] = []
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

            # 买入时限制单个持仓不超过 max_position_pct
            if order_type == "BUY" and max_position_pct < 1.0:
                max_val = self.total_value * max_position_pct
                if target_val > max_val:
                    target_val = max_val
                    diff_val = target_val - current_val
                    if diff_val <= 0:
                        continue

            order_infos.append({
                "symbol": symbol,
                "order_type": order_type,
                "diff_val": diff_val,
                "price": price,
            })

        # 按先卖后买排序，使卖出回笼资金可用于同 bar 买入
        order_infos.sort(key=lambda x: 0 if x["order_type"] == "SELL" else 1)
        return order_infos

    def _generate_orders(
        self,
        order_infos: list[dict],
        event: SignalEvent,
    ) -> list[OrderEvent]:
        """根据订单信息生成 OrderEvent，处理现金约束"""
        orders: list[OrderEvent] = []
        remaining_cash = self.cash
        for info in order_infos:
            symbol = info["symbol"]
            order_type = info["order_type"]
            diff_val = info["diff_val"]
            price = info["price"]

            if order_type == "SELL":
                # 卖出回笼资金计入可用现金（预留 0.1% 费用缓冲）
                remaining_cash += abs(diff_val) * 0.999
            else:
                # 买入时检查可用现金（含预估交易成本缓冲）
                estimated_cost = diff_val * 1.001
                if estimated_cost > remaining_cash:
                    # 可用现金不足以覆盖预估成本，缩减交易金额至可承受范围
                    diff_val = remaining_cash / 1.001
                    if diff_val < 1.0:
                        continue
                    estimated_cost = diff_val * 1.001
                remaining_cash -= estimated_cost

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

        if fill.order_type == "BUY":
            if cost > self.cash + 1e-6:
                raise ValueError(
                    f"现金不足: 买入 {fill.symbol} 需要 {cost:.2f}，"
                    f"可用 {self.cash:.2f}（实际成交成本超出预估）"
                )
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
                # 计算已实现 P&L
                realized = proceeds - (pos.avg_cost * fill.fill_quantity)
                self.realized_pnl[symbol] = (
                    self.realized_pnl.get(symbol, 0.0) + realized
                )
                pos.shares -= fill.fill_quantity
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

        self.total_value = self.cash + sum(
            p.market_value for p in self.positions.values()
        )

        # 更新 peak_value（O(1) 追踪，替代 max(equity_curve) 的 O(N) 查找）
        self.peak_value = max(self.peak_value, self.total_value)

    def _sync_equity_curve(self) -> None:
        """将当前 total_value 同步到 equity_curve（由引擎在 bar 末尾调用）"""
        self.equity_curve.append(self.total_value)
        # 同步 pnl_by_symbol = realized + unrealized
        self.pnl_by_symbol = dict(self.realized_pnl)
        for symbol, pos in self.positions.items():
            self.pnl_by_symbol[symbol] = (
                self.realized_pnl.get(symbol, 0.0)
                + (pos.current_price - pos.avg_cost) * pos.shares
            )
