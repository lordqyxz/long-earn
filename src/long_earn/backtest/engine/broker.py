"""模拟撮合经纪人

负责将订单 (OrderEvent) 转换为成交记录 (FillEvent)，并计算交易成本。
支持市价单、限价单、止损单、止损限价单及 OCO 订单。
"""

import uuid
from dataclasses import dataclass

from loguru import logger

from long_earn.backtest.domain.entities import (
    ExecType,
    FillEvent,
    OpenOrder,
    OrderEvent,
    OrderStatus,
)
from long_earn.backtest.domain.exceptions import OrderExecutionError


@dataclass
class TradingCostConfig:
    """交易成本配置 (默认 A 股参数)

    A 股关键约束：
    - 佣金万三起步，**最低 5 元/单**（券商行规）——小订单 <16667 元会触发最低佣金
    - 印花税仅卖出收，2023-08 起从万十减半到万五
    - 滑点按 bps 计：2bps = 0.02% 接近实际中等流动性股票成交磨损
    """

    commission_rate: float = 0.0003  # 万三
    stamp_duty: float = 0.0005  # 万五 (仅卖出，2023-08 后减半)
    slippage_bps: float = 2.0  # 2bps
    min_commission: float = 5.0  # 最低 5 元/单（A 股券商行规）

    @property
    def slippage_rate(self) -> float:
        return self.slippage_bps * 0.0001

    def compute_commission(self, amount: float) -> float:
        """计算佣金：max(rate * amount, min_commission)

        amount 是成交金额（fill_price * fill_quantity）。
        旧版直接 amount * rate 让小订单佣金严重低估，导致 LLM 生成的高频/小资金
        策略回测业绩失真——本轮修复（轮 18）保证最低 5 元约束生效。
        """
        return max(amount * self.commission_rate, self.min_commission)


class Broker:
    """
    模拟撮合经纪人

    职责：
    1. 接收 OrderEvent → 根据订单类型计算成交。
    2. 跟踪待成交订单（限价/止损/止损限价）。
    3. 计算交易成本 (佣金、印花税、滑点)。
    4. 管理 OCO 互斥组。
    5. 生成 FillEvent。
    """

    def __init__(self, cost_config: TradingCostConfig | None = None):
        self.cost_config = cost_config or TradingCostConfig()
        # 待成交订单：order_id → OpenOrder
        self.pending_orders: dict[str, OpenOrder] = {}
        # OCO 互斥组：oco_group_id → set[order_id]
        self.oco_groups: dict[str, set[str]] = {}

    # ── 主入口 ──────────────────────────────────────────────────

    def submit_order(self, order: OrderEvent, current_price: float) -> list[FillEvent]:
        """
        提交订单并尝试撮合

        Args:
            order: 待执行订单
            current_price: 当前市场价格

        Returns:
            本次产生的成交事件列表（可能为空）
        """
        # 提前注册 OCO 组，确保即使立即成交也记录互斥关系
        self._register_oco(order)

        if self._is_cancelled_by_oco(order):
            logger.debug("OCO 订单 %s 已被同组其他订单取消", order.order_id)
            return []

        order_type = order.exec_type or ExecType.MARKET

        if order_type == ExecType.MARKET:
            return [self._fill_market(order, current_price)]

        fills: list[FillEvent] = []

        if order_type == ExecType.LIMIT:
            fill = self._try_fill_limit(order, current_price)
            if fill is not None:
                fills.append(fill)
                self._cancel_oco_siblings(order)
            else:
                self._pend_order(order)
            return fills

        if order_type in (ExecType.STOP, ExecType.STOP_LIMIT):
            triggered = self._check_stop_trigger(order, current_price)
            if triggered:
                if order_type == ExecType.STOP:
                    fills.append(self._fill_market(order, current_price))
                else:
                    # STOP_LIMIT: 触发后转为限价待成交
                    self._pend_order(order)
            else:
                self._pend_order(order)
            return fills

        raise OrderExecutionError(f"未知订单执行类型: {order_type}")

    def check_pending_orders(self, price_lookup: dict[str, float]) -> list[FillEvent]:
        """
        检查所有待成交订单（每个 bar 调用一次）

        Args:
            price_lookup: symbol → current_price 映射

        Returns:
            本 bar 产生的成交事件列表
        """
        fills: list[FillEvent] = []
        expired_ids: list[str] = []

        for oid, open_order in list(self.pending_orders.items()):
            # 可能被 OCO 取消，跳过已移除的订单
            if oid not in self.pending_orders:
                continue
            order = open_order.order
            sym_price = price_lookup.get(order.symbol)
            if sym_price is None:
                continue

            otype = order.exec_type or ExecType.MARKET
            new_fills = self._process_pending_order(otype, open_order, order, sym_price)
            if new_fills:
                fills.extend(new_fills)
                self._cancel_oco_siblings(order)
                self._finalize_order(oid)

        for oid in expired_ids:
            self._cancel_order(oid)

        return fills

    def _process_pending_order(
        self,
        otype: str,
        open_order: OpenOrder,
        order: OrderEvent,
        sym_price: float,
    ) -> list[FillEvent]:
        """根据订单类型处理单个待成交订单"""
        if otype == ExecType.LIMIT:
            fill = self._try_fill_limit(order, sym_price)
            return [fill] if fill is not None else []

        if otype == ExecType.STOP:
            triggered = open_order.trigger_activated or self._check_stop_trigger(
                order, sym_price
            )
            if triggered:
                open_order.trigger_activated = True
                return [self._fill_market(order, sym_price)]
            return []

        if otype == ExecType.STOP_LIMIT:
            if not open_order.trigger_activated:
                triggered = self._check_stop_trigger(order, sym_price)
                if triggered:
                    open_order.trigger_activated = True
            if open_order.trigger_activated:
                fill = self._try_fill_limit(order, sym_price)
                if fill is not None:
                    return [fill]
            return []

        return []

    # ── 订单撮合方法 ────────────────────────────────────────────

    def _fill_market(self, order: OrderEvent, current_price: float) -> FillEvent:
        """市价单立即成交（含滑点 + 最低佣金保护）"""
        slip_dir = 1 if order.order_type == "BUY" else -1
        fill_price = current_price * (1 + slip_dir * self.cost_config.slippage_rate)

        amount = order.quantity * fill_price
        commission = self.cost_config.compute_commission(amount)
        stamp_duty = 0.0
        if order.order_type == "SELL":
            stamp_duty = amount * self.cost_config.stamp_duty

        fill = FillEvent(
            timestamp=order.timestamp,
            trace_id=str(uuid.uuid4()),
            event_id=f"fill_{order.order_id}",
            order_id=order.order_id,
            symbol=order.symbol,
            order_type=order.order_type,
            fill_price=fill_price,
            fill_quantity=order.quantity,
            commission=commission,
            slippage=abs(fill_price - current_price) * order.quantity,
            stamp_duty=stamp_duty,
        )

        self._cancel_oco_siblings(order)
        return fill

    def _try_fill_limit(
        self, order: OrderEvent, current_price: float
    ) -> FillEvent | None:
        """尝试限价单成交（价格满足条件则成交，否则返回 None）

        保守成交规则（避免回测过于乐观）：
        - BUY LIMIT @ L：实际成交价取 max(L, current + slip)
          ——回测不能假设拿到 bar 内任意优于限价的价格，且必须承担滑点
        - SELL LIMIT @ L：实际成交价取 min(L, current - slip)
        旧实现 fill_price = current_price 等于"白拿 bar 内最低/最高价"，且
        漏掉滑点，会让限价策略回测业绩系统性高估。
        """
        if order.price is None:
            return None

        can_fill = False
        if order.order_type == "BUY":
            # 买入限价：当前价 <= 限价
            can_fill = current_price <= order.price
        else:
            # 卖出限价：当前价 >= 限价
            can_fill = current_price >= order.price

        if not can_fill:
            return None

        # 加滑点：买方向上付溢价，卖方向下让价
        slip_adj = current_price * self.cost_config.slippage_rate
        if order.order_type == "BUY":
            # 至少不优于限价：max(limit, current + slip)
            fill_price = max(order.price, current_price + slip_adj)
        else:
            # 至少不优于限价：min(limit, current - slip)
            fill_price = min(order.price, current_price - slip_adj)

        amount = order.quantity * fill_price
        commission = self.cost_config.compute_commission(amount)
        stamp_duty = 0.0
        if order.order_type == "SELL":
            stamp_duty = amount * self.cost_config.stamp_duty

        return FillEvent(
            timestamp=order.timestamp,
            trace_id=str(uuid.uuid4()),
            event_id=f"fill_{order.order_id}",
            order_id=order.order_id,
            symbol=order.symbol,
            order_type=order.order_type,
            fill_price=fill_price,
            fill_quantity=order.quantity,
            commission=commission,
            slippage=abs(fill_price - current_price) * order.quantity,
            stamp_duty=stamp_duty,
        )

    @staticmethod
    def _check_stop_trigger(order: OrderEvent, current_price: float) -> bool:
        """检查止损/止盈是否触发"""
        if order.stop_price is None:
            return False
        if order.order_type == "BUY":
            # 买入止损：当前价 >= 触发价（向上突破买入）
            return current_price >= order.stop_price
        # 卖出止损：当前价 <= 触发价（向下突破卖出）
        return current_price <= order.stop_price

    # ── OCO 管理 ────────────────────────────────────────────────

    def _register_oco(self, order: OrderEvent) -> None:
        """注册 OCO 组（在 submit_order 开始时调用，确保即使立即成交也记录组关系）"""
        if order.oco_group_id:
            self.oco_groups.setdefault(order.oco_group_id, set()).add(order.order_id)

    def _pend_order(self, order: OrderEvent) -> None:
        """将订单加入待成交队列"""
        self.pending_orders[order.order_id] = OpenOrder(order=order)

    def _finalize_order(self, order_id: str) -> None:
        """订单成交后从待成交队列移除"""
        self.pending_orders.pop(order_id, None)

    def _cancel_order(self, order_id: str) -> None:
        """取消订单"""
        open_order = self.pending_orders.pop(order_id, None)
        if open_order is not None:
            open_order.status = OrderStatus.CANCELLED

    def _is_cancelled_by_oco(self, order: OrderEvent) -> bool:
        """检查 OCO 组内是否已有订单成交"""
        if not order.oco_group_id:
            return False
        siblings = self.oco_groups.get(order.oco_group_id, set())
        # 如果同组中已有订单被移除（已成交），则本单取消
        for sid in siblings:
            if sid != order.order_id and sid not in self.pending_orders:
                return True
        return False

    def _cancel_oco_siblings(self, order: OrderEvent) -> None:
        """订单成交后取消同 OCO 组的其他订单"""
        if not order.oco_group_id:
            return
        siblings = self.oco_groups.get(order.oco_group_id, set())
        for sid in list(siblings):
            if sid != order.order_id:
                self._cancel_order(sid)

    # ── 向后兼容 ────────────────────────────────────────────────

    def execute_order(self, order: OrderEvent, current_price: float) -> FillEvent:
        """
        [向后兼容] 市价单立即成交

        旧接口：直接返回单个 FillEvent（仅支持市价单）。
        新代码请使用 submit_order()。
        """
        fills = self.submit_order(order, current_price)
        if not fills:
            raise OrderExecutionError(f"订单 {order.order_id} 无法作为市价单成交")
        return fills[-1]

    def get_pending_count(self) -> int:
        """获取待成交订单数量"""
        return len(self.pending_orders)

    def get_pending_orders(self) -> list[OpenOrder]:
        """获取所有待成交订单"""
        return list(self.pending_orders.values())

    def reset(self) -> None:
        """重置经纪人状态（新回测前调用）"""
        self.pending_orders.clear()
        self.oco_groups.clear()
