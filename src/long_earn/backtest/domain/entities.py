"""回测领域实体与值对象

富领域模型 — 实体控制状态转换，值对象不可变。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import polars as pl

from long_earn.backtest.domain.exceptions import StrategyValidationError

_MAX_DRAWDOWN_THRESHOLD = 0.3


@dataclass(frozen=True)
class DateRange:
    """日期范围值对象"""

    start: str
    end: str

    def __post_init__(self):
        if self.start > self.end:
            raise StrategyValidationError(
                f"起始日期 {self.start} 不能晚于结束日期 {self.end}"
            )

    def __str__(self) -> str:
        return f"{self.start} ~ {self.end}"


@dataclass(frozen=True)
class PerformanceMetrics:
    """绩效指标值对象"""

    total_return: float = 0.0
    annual_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    trading_days: int = 0
    volatility: float = 0.0
    calmar_ratio: float = 0.0
    sortino_ratio: float = 0.0

    # 基准对比指标
    alpha: float = 0.0
    beta: float = 0.0
    information_ratio: float = 0.0
    tracking_error: float = 0.0
    benchmark_return: float = 0.0

    @property
    def is_profitable(self) -> bool:
        return self.total_return > 0

    @property
    def is_risk_adjusted_good(self) -> bool:
        return self.sharpe_ratio > 1.0 and self.max_drawdown < _MAX_DRAWDOWN_THRESHOLD

    @property
    def has_alpha(self) -> bool:
        """是否跑赢基准（正 Alpha）"""
        return self.alpha > 0


# ── 事件系统 (Event System) ────────────────────────────────────────────


@dataclass(frozen=True)
class Event:
    """回测引擎基础事件"""

    timestamp: datetime
    trace_id: str  # 贯穿 信号 -> 订单 -> 成交 的唯一 ID，用于因果审计
    event_id: str


@dataclass(frozen=True)
class MarketDataEvent(Event):
    """行情事件: 携带当前时间步的所有股票截面数据 (Slab)"""

    # slab 为 Polars DataFrame: index=symbol, columns=[open, high, low, close, ...]
    slab: pl.DataFrame


@dataclass(frozen=True)
class SignalEvent(Event):
    """信号事件: 策略生成的交易意向"""

    # signals 为 Polars Series 或 dict: {symbol: target_weight}
    signals: pl.Series | dict[str, float]
    strategy_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


# ── 订单执行类型常量 ────────────────────────────────────────────


class ExecType:
    """订单执行类型（策略层生成请求时使用）"""

    MARKET = "MKT"  # 市价单 — 立即以当前最优价成交
    LIMIT = "LMT"  # 限价单 — 指定价格或更优
    STOP = "STP"  # 止损/止盈单 — 触发后转市价
    STOP_LIMIT = "STL"  # 止损限价单 — 触发后转限价


# ── 订单状态常量 ────────────────────────────────────────────────


class OrderStatus:
    """订单状态（Broker 跟踪用）"""

    PENDING = "PENDING"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class OrderEvent(Event):
    """订单事件: 由 Portfolio 生成的交易请求"""

    symbol: str
    order_type: str  # 'BUY' | 'SELL' — 交易方向
    quantity: float  # 数量（正数）
    price: float | None = None  # 限制价，若为 None 则为市价单
    order_id: str = ""
    exec_type: str = ExecType.MARKET  # 执行类型，默认市价单
    stop_price: float | None = None  # 止损/止盈触发价（STOP/STOP_LIMIT）
    oco_group_id: str = ""  # OCO 组 ID，同一组的订单互斥


@dataclass
class OpenOrder:
    """待成交订单（Broker 跟踪用，非冻结）"""

    order: OrderEvent
    status: str = OrderStatus.PENDING
    filled_qty: float = 0.0
    avg_fill_price: float = 0.0
    submit_bar_idx: int = 0  # 提交时的 bar 索引，用于超时检查
    trigger_activated: bool = False  # STOP/STOP_LIMIT 是否已触发


@dataclass(frozen=True)
class FillEvent(Event):
    """成交事件: 由 Broker 模拟撮合后产生"""

    order_id: str
    symbol: str
    order_type: str  # 'BUY' | 'SELL'
    fill_price: float
    fill_quantity: float
    commission: float
    slippage: float
    stamp_duty: float


# ── 持仓与交易实体 ──────────────────────────────────────────────────────


@dataclass
class Position:
    """单个股票的持仓实体"""

    symbol: str
    shares: float = 0.0
    avg_cost: float = 0.0
    market_value: float = 0.0
    current_price: float = 0.0

    def update_market_value(self, current_price: float):
        self.market_value = self.shares * current_price


@dataclass
class Trade:
    """一次完成的交易记录"""

    symbol: str
    entry_time: datetime
    exit_time: datetime | None = None
    entry_price: float = 0.0
    exit_price: float | None = None
    profit_loss: float = 0.0
    pnl_pct: float = 0.0
