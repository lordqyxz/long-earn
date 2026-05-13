"""回测领域实体与值对象

富领域模型 — 实体控制状态转换，值对象不可变。
"""

from dataclasses import dataclass, field

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

    @property
    def is_profitable(self) -> bool:
        return self.total_return > 0

    @property
    def is_risk_adjusted_good(self) -> bool:
        return self.sharpe_ratio > 1.0 and self.max_drawdown < _MAX_DRAWDOWN_THRESHOLD


@dataclass
class Portfolio:
    """投资组合实体 — 管理持仓和调仓逻辑"""

    initial_capital: float = 1_000_000.0
    cash: float = 1_000_000.0
    positions: dict[str, float] = field(default_factory=dict)

    @property
    def total_value(self) -> float:
        return self.cash + sum(self.positions.values())

    @property
    def position_count(self) -> int:
        return len(self.positions)

    def rebalance(self, weights: dict[str, float], prices: dict[str, float]) -> None:
        """执行调仓

        Args:
            weights: 目标权重 {symbol: weight}
            prices: 当前价格 {symbol: price}
        """
        total = self.total_value
        new_positions: dict[str, float] = {}
        for symbol, weight in weights.items():
            if symbol in prices and prices[symbol] > 0:
                target_value = total * weight
                shares = target_value / prices[symbol]
                new_positions[symbol] = shares * prices[symbol]

        self.positions = new_positions
