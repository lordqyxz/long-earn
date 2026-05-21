"""回测引擎包

事件驱动回测引擎，支持 YAML DSL 策略描述和 ML 算法交易。
可被 LangGraph 节点直接调用。

领域模型：
- StrategyDSL: 策略 YAML DSL 模型（引擎输入）
- BacktestResult: 回测结果模型（引擎输出）
- PerformanceMetrics: 绩效指标值对象（不可变）
- Portfolio: 投资组合实体（管理持仓和调仓）

引擎核心：
- EventDrivenBacktestEngine: 事件驱动回测引擎
- BaseStrategy: 策略基类（Agent 友好状态化接口）
- MLSignalStrategy: ML 策略基类（支持特征工程）
- FeatureEngine: 截面特征工程
"""

from long_earn.backtest.domain.entities import (
    DateRange,
    PerformanceMetrics,
    Position,
)
from long_earn.backtest.domain.exceptions import (
    BacktestDomainError,
    BacktestExecutionError,
    DataLoadError,
    ExpressionEvalError,
    StrategyValidationError,
    UniverseError,
)
from long_earn.backtest.engine.core import EventDrivenBacktestEngine, InMemoryAuditTrail
from long_earn.backtest.engine.dsl import (
    StrategyDSL,
    parse_strategy_yaml,
)
from long_earn.backtest.engine.ml_strategy import (
    FeatureEngine,
    MLSignalStrategy,
    TimeSeriesSplit,
    compute_atr,
    compute_bollinger_bands,
    compute_macd,
    compute_rsi,
)
from long_earn.backtest.engine.strategy import BaseStrategy
from long_earn.backtest.models import BacktestResult

__all__ = [
    "BacktestDomainError",
    "BacktestExecutionError",
    "BacktestResult",
    "BaseStrategy",
    "DataLoadError",
    "DateRange",
    "EventDrivenBacktestEngine",
    "ExpressionEvalError",
    "FeatureEngine",
    "InMemoryAuditTrail",
    "MLSignalStrategy",
    "PerformanceMetrics",
    "Position",
    "StrategyDSL",
    "StrategyValidationError",
    "TimeSeriesSplit",
    "UniverseError",
    "compute_atr",
    "compute_bollinger_bands",
    "compute_macd",
    "compute_rsi",
    "parse_strategy_yaml",
]
