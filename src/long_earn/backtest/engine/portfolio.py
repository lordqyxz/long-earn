"""投资组合管理器

负责将策略生成的信号转换为具体订单，并管理实时持仓与资金。
"""

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import polars as pl
from loguru import logger

from long_earn.backtest.domain.entities import (
    FillEvent,
    OrderEvent,
    Position,
    SignalEvent,
)

if TYPE_CHECKING:
    from long_earn.backtest.engine.broker import TradingCostConfig


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

    def __init__(
        self,
        initial_capital: float = 1_000_000.0,
        cost_config: "TradingCostConfig | None" = None,
    ):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.positions: dict[str, Position] = {}
        self.total_value = initial_capital
        self.equity_curve: list[float] = []
        self.peak_value: float = initial_capital
        self.realized_pnl: dict[str, float] = {}
        self.pnl_by_symbol: dict[str, float] = {}
        self.trade_count: int = 0
        # 交易成本配置：用于在生成订单时预估佣金（含最低佣金保护），使现金约束
        # 与 Broker 实际成交成本一致，避免"预估够、实际不足"的现金断裂。
        self.cost_config = cost_config

    def _estimate_commission(self, amount: float) -> float:
        """预估佣金，与 Broker.compute_commission 保持一致。

        未配置 cost_config 时退化为按 0.1% 缓冲（旧行为），保证历史调用兼容。
        """
        if self.cost_config is None:
            return amount * 0.001
        return self.cost_config.compute_commission(amount)

    def _slippage_rate(self) -> float:
        """滑点率（未配置时按 2bps）。"""
        if self.cost_config is None:
            return 0.0002
        return self.cost_config.slippage_rate

    def _stamp_duty_rate(self) -> float:
        """卖出印花税率（未配置时按默认万五）。"""
        if self.cost_config is None:
            return 0.0005
        return self.cost_config.stamp_duty

    def _estimate_buy_cost(self, target_value: float) -> float:
        """预估买入实际成本 = 成交金额(含滑点) + 佣金(含最低保护)。

        与 Broker._fill_market/_fill_limit 的 BUY 成本口径一致：
        fill_price = current*(1+slippage)，amount = target_value*(1+slippage)，
        commission = max(amount*rate, min_commission)。
        """
        slip = self._slippage_rate()
        amount = target_value * (1.0 + slip)
        return amount + self._estimate_commission(amount)

    def _estimate_sell_net(self, target_value: float) -> float:
        """预估卖出净回笼 = 成交金额(扣滑点) - 佣金 - 印花税。"""
        slip = self._slippage_rate()
        proceeds = target_value * (1.0 - slip)
        return (
            proceeds
            - self._estimate_commission(proceeds)
            - proceeds * self._stamp_duty_rate()
        )

    def _max_affordable_buy(self, cash: float) -> float:
        """在现金 ``cash`` 下，反解能承受的最大买入目标金额（含滑点+佣金）。

        求解 _estimate_buy_cost(v) <= cash，即
        v*(1+slip) + max(v*(1+slip)*rate, min_c) <= cash。
        分两段（最低佣金档 / 费率档）取较保守者。
        """
        if self.cost_config is None:
            return cash / 1.001
        slip = self._slippage_rate()
        rate = self.cost_config.commission_rate
        min_c = self.cost_config.min_commission
        gross_factor = 1.0 + slip  # target_value -> amount 的系数
        # 费率档：v*(1+slip)*(1+rate) <= cash
        rate_branch = cash / (gross_factor * (1.0 + rate)) if rate >= 0 else cash
        # 最低佣金档：v*(1+slip) + min_c <= cash  （仅当该档确实落在最低区间才有效）
        min_branch = (cash - min_c) / gross_factor if cash >= min_c else 0.0
        # 两段交界处取较小者最保守；但要确保选中的段与其假设一致
        candidate = min(rate_branch, min_branch)
        return max(0.0, candidate)

    def process_signal(
        self,
        event: SignalEvent,
        current_prices: pl.DataFrame,
        max_positions: int = 0,
        max_position_pct: float = 1.0,
    ) -> list[OrderEvent]:
        """将信号转换为订单

        语义：target_weights 是策略想要的**完整目标组合**——任何当前持仓
        但不在 target_weights 中的标的会被解读为"目标权重 0 → 全部卖出"。
        这样 DSL 策略发 {B:1.0} 时可以正确把 A 卖出再买 B（rebalancing）。
        反之若实现成"只处理 target 中的 symbol，其它不动"，DSL 策略
        将无法表达切换持仓的意图，仓位会不断累积——本轮修复的核心 bug。

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

        # 关键修复：把"当前持仓但不在 target_weights 中"的标的填 weight=0，
        # 让 _compute_order_infos 能生成 SELL 订单完成 rebalancing。
        target_weights = dict(target_weights)
        for held in self.positions:
            target_weights.setdefault(held, 0.0)

        target_weights = self._apply_position_limits(target_weights, max_positions)

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
        """计算每个标的的目标订单信息（方向、金额、价格）

        target_weight=0 + 当前持仓 → 全部卖出（rebalancing 语义）。
        target_weight=0 + 无持仓 → 跳过（什么都不做）。
        """
        order_infos: list[dict] = []
        for symbol, raw_weight in target_weights.items():
            # 不允许做空：负权重视为 0
            target_weight = max(0.0, raw_weight)

            current_pos = self.positions.get(symbol)
            # weight=0 + 无持仓 → 跳过
            if target_weight == 0 and current_pos is None:
                continue

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

            order_infos.append(
                {
                    "symbol": symbol,
                    "order_type": order_type,
                    "diff_val": diff_val,
                    "price": price,
                }
            )

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
                # 卖出回笼资金计入可用现金（扣减滑点 + 佣金 + 印花税）
                remaining_cash += self._estimate_sell_net(abs(diff_val))
            else:
                # 买入：预估成本 = 成交金额(含滑点) + 佣金(含最低保护)，与 Broker 一致
                estimated_cost = self._estimate_buy_cost(diff_val)
                if estimated_cost > remaining_cash:
                    # 可用现金不足：反解能承受的最大买入金额
                    diff_val = self._max_affordable_buy(remaining_cash)
                    if diff_val < 1.0:
                        continue
                    estimated_cost = self._estimate_buy_cost(diff_val)
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
