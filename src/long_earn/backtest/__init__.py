"""回测引擎包

向量化回测引擎，支持 YAML DSL 策略描述，可被 LangGraph 节点直接调用。

领域模型：
- StrategyDSL: 策略 YAML DSL 模型（引擎输入）
- BacktestResult: 回测结果模型（引擎输出）
- PerformanceMetrics: 绩效指标值对象（不可变）
- Portfolio: 投资组合实体（管理持仓和调仓）
"""

from long_earn.backtest.domain.entities import (
    DateRange,
    PerformanceMetrics,
    Portfolio,
)
from long_earn.backtest.domain.exceptions import (
    BacktestDomainError,
    BacktestExecutionError,
    DataLoadError,
    ExpressionEvalError,
    StrategyValidationError,
    UniverseError,
)
from long_earn.backtest.engine.core import VectorizedBacktestEngine, run_backtest
from long_earn.backtest.engine.dsl import parse_strategy_yaml
from long_earn.backtest.models import BacktestResult

__all__ = [
    "BacktestDomainError",
    "BacktestExecutionError",
    "BacktestResult",
    "DataLoadError",
    "DateRange",
    "ExpressionEvalError",
    "PerformanceMetrics",
    "Portfolio",
    "StrategyValidationError",
    "UniverseError",
    "VectorizedBacktestEngine",
    "parse_strategy_yaml",
    "run_backtest",
]
