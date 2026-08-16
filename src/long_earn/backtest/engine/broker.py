"""模拟撮合经纪人

负责将订单 (OrderEvent) 转换为成交记录 (FillEvent)，并计算交易成本。
支持市价单、限价单、止损单、止损限价单及 OCO 订单。
"""

import math
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
from long_earn.backtest.engine.audit import OrderSkipReason


@dataclass
class TradingCostConfig:
    """交易成本配置 (默认 A 股参数)

    A 股关键约束：
    - 佣金万三起步，**最低 5 元/单**（券商行规）——小订单 <16667 元会触发最低佣金
    - 印花税仅卖出收，2023-08 起从万十减半到万五
    - 滑点按 bps 计：2bps = 0.02% 接近实际中等流动性股票成交磨损
    - 成交量参与率：单笔订单不超过当日成交量的 10%（P0-04 修复）
    - 冲击成本：平方根模型 k * sqrt(order_amount / daily_volume)
    """

    commission_rate: float = 0.0003  # 万三
    stamp_duty: float = 0.0005  # 万五 (仅卖出，2023-08 后减半)
    slippage_bps: float = 2.0  # 2bps
    min_commission: float = 5.0  # 最低 5 元/单（A 股券商行规）
    max_volume_participation: float = 0.1  # 单笔不超过日成交量的 10%（P0-04）
    impact_cost_k: float = 0.01  # 平方根冲击模型系数
    transfer_fee_rate: float = 0.00001  # 过户费（沪市双向万分之 0.1，P1-03）

    @property
    def slippage_rate(self) -> float:
        return self.slippage_bps * 0.0001

    def compute_impact_bps(self, order_amount: float, daily_volume: float) -> float:
        """平方根冲击模型：impact_bps = k * sqrt(order_amount / daily_volume)

        Args:
            order_amount: 订单金额（元）
            daily_volume: 当日成交量（元，price * volume）

        Returns:
            冲击成本（bps 单位），额外加到 slippage_bps 上
        """
        if daily_volume <= 0 or order_amount <= 0:
            return 0.0
        participation = order_amount / daily_volume
        if participation <= 0:
            return 0.0
        return self.impact_cost_k * (participation**0.5) * 10000  # 转为 bps

    def compute_commission(self, amount: float) -> float:
        """计算佣金：max(rate * amount, min_commission)

        amount 是成交金额（fill_price * fill_quantity）。
        旧版直接 amount * rate 让小订单佣金严重低估，导致 LLM 生成的高频/小资金
        策略回测业绩失真——本轮修复（轮 18）保证最低 5 元约束生效。
        """
        return max(amount * self.commission_rate, self.min_commission)


def validate_order_fields(order: OrderEvent) -> tuple[OrderSkipReason, str] | None:
    """校验订单自身数值合法性（AUDIT-P3-02）。

    撮合前 fail closed：订单数量必须为有限正数；限价（price）与止损价
    （stop_price）若给定必须为有限正数。NaN / Inf / 0 / 负数一律拒绝。

    返回 ``(OrderSkipReason, detail)`` 表示拒绝原因，``None`` 表示通过。
    引擎预检（记 ORDER_SKIPPED 审计）与 Broker 入口（抛异常 fail closed）
    共用本函数，保证两层判定一致、不留判定漂移空间。
    """
    if order.quantity is None or not math.isfinite(order.quantity) or order.quantity <= 0:
        return (
            OrderSkipReason.INVALID_QUANTITY,
            f"{order.symbol} quantity={order.quantity}",
        )
    if order.price is not None and (
        not math.isfinite(order.price) or order.price <= 0
    ):
        return (
            OrderSkipReason.INVALID_PRICE,
            f"{order.symbol} 限价 price={order.price}",
        )
    if order.stop_price is not None and (
        not math.isfinite(order.stop_price) or order.stop_price <= 0
    ):
        return (
            OrderSkipReason.INVALID_PRICE,
            f"{order.symbol} 止损价 stop_price={order.stop_price}",
        )
    return None


def validate_order_numeric(
    order: OrderEvent, current_price: float
) -> tuple[OrderSkipReason, str] | None:
    """订单 + 成交价全量数值校验（AUDIT-P3-02）。

    在 :func:`validate_order_fields` 基础上追加当前市场价校验，
    保证 NaN / Inf / 非正数价格或数量绝无可能流入撮合计算。
    """
    fields_invalid = validate_order_fields(order)
    if fields_invalid is not None:
        return fields_invalid
    if not math.isfinite(current_price) or current_price <= 0:
        return (
            OrderSkipReason.PRICE_INVALID,
            f"{order.symbol} 当前价 price={current_price}",
        )
    return None


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

    def submit_order(
        self, order: OrderEvent, current_price: float, daily_volume: float = 0.0
    ) -> list[FillEvent]:
        """
        提交订单并尝试撮合

        Args:
            order: 待执行订单
            current_price: 当前市场价格

        Returns:
            本次产生的成交事件列表（可能为空）
        """
        # P3-02: 非法数值输入（NaN/Inf/负数/0 价格或数量）fail closed——
        # 显式抛异常拒绝，绝不静默吞掉或让 NaN 流入撮合计算。
        invalid = validate_order_numeric(order, current_price)
        if invalid is not None:
            reason, detail = invalid
            raise OrderExecutionError(
                f"订单 {order.order_id} 数值非法被拒（{reason.value}）: {detail}"
            )

        # 提前注册 OCO 组，确保即使立即成交也记录互斥关系
        self._register_oco(order)

        if self._is_cancelled_by_oco(order):
            logger.debug(f"OCO 订单 {order.order_id} 已被同组其他订单取消")
            return []

        order_type = order.exec_type or ExecType.MARKET

        if order_type == ExecType.MARKET:
            return [self._fill_market(order, current_price, daily_volume)]

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
                    fills.append(self._fill_market(order, current_price, daily_volume))
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
            # P3-02: 行情价非法（NaN/Inf/非正数）时本 bar 跳过撮合，
            # 防止 NaN 成交价流入 FillEvent 污染组合净值。
            if not math.isfinite(sym_price) or sym_price <= 0:
                logger.warning(
                    f"待成交订单 {oid} 行情价非法，本 bar 跳过撮合: "
                    f"{order.symbol} price={sym_price}"
                )
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

    def _fill_market(
        self,
        order: OrderEvent,
        current_price: float,
        daily_volume: float = 0.0,
    ) -> FillEvent:
        """市价单立即成交（含滑点 + 最低佣金保护 + 成交量限制 + 冲击成本）。

        P0-04 修复：
        - 成交量参与率限制：fill_quantity = min(order.quantity, volume * participation)
        - 平方根冲击模型：额外滑点 = k * sqrt(order_amount / daily_volume)
        """
        slip_dir = 1 if order.order_type == "BUY" else -1

        # 成交量参与率限制
        fill_qty = order.quantity
        if daily_volume > 0 and self.cost_config.max_volume_participation > 0:
            max_qty = daily_volume * self.cost_config.max_volume_participation
            fill_qty = min(order.quantity, max_qty)

        partial_fill = fill_qty < order.quantity

        # 平方根冲击模型：额外滑点
        amount = fill_qty * current_price
        impact_bps = self.cost_config.compute_impact_bps(amount, daily_volume)
        total_slip_bps = self.cost_config.slippage_bps + impact_bps
        total_slip_rate = total_slip_bps * 0.0001

        fill_price = current_price * (1 + slip_dir * total_slip_rate)
        amount = fill_qty * fill_price
        commission = self.cost_config.compute_commission(amount)
        stamp_duty = 0.0
        if order.order_type == "SELL":
            stamp_duty = amount * self.cost_config.stamp_duty

        # 过户费（P1-03）：沪市（.SH）双向征收，深市不收
        transfer_fee = 0.0
        if order.symbol.upper().endswith(".SH"):
            transfer_fee = amount * self.cost_config.transfer_fee_rate

        fill = FillEvent(
            timestamp=order.timestamp,
            trace_id=str(uuid.uuid4()),
            event_id=f"fill_{order.order_id}",
            order_id=order.order_id,
            symbol=order.symbol,
            order_type=order.order_type,
            fill_price=fill_price,
            fill_quantity=fill_qty,
            commission=commission,
            slippage=abs(fill_price - current_price) * fill_qty,
            stamp_duty=stamp_duty,
            partial_fill=partial_fill,
            transfer_fee=transfer_fee,
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

        # 过户费（P1-03）：沪市双向征收
        transfer_fee = 0.0
        if order.symbol.upper().endswith(".SH"):
            transfer_fee = amount * self.cost_config.transfer_fee_rate

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
            transfer_fee=transfer_fee,
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

    def execute_order(
        self, order: OrderEvent, current_price: float, daily_volume: float = 0.0
    ) -> FillEvent:
        """
        [向后兼容] 市价单立即成交

        旧接口：直接返回单个 FillEvent（仅支持市价单）。
        新代码请使用 submit_order()。
        """
        fills = self.submit_order(order, current_price, daily_volume)
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
