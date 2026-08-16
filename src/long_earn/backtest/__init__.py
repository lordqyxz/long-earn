"""回测引擎包

事件驱动回测引擎，支持 YAML DSL 策略描述（ADR-009 收尾：仅算子目录路径）。
可被 LangGraph 节点直接调用。

领域模型：
- StrategyDSL: 策略 YAML DSL 模型（引擎输入）
- BacktestResult: 回测结果模型（引擎输出）
- PerformanceMetrics: 绩效指标值对象（不可变）
- Portfolio: 投资组合实体（管理持仓和调仓）

引擎核心：
- EventDrivenBacktestEngine: 事件驱动回测引擎
- BaseStrategy: 策略基类（Agent 友好状态化接口）
- TimeSeriesSplit: 时序交叉验证分割器（Walk-Forward OOS 用）
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
from long_earn.backtest.engine.parallel import _MAX_GRID_DEFAULT
from long_earn.backtest.engine.param_grid import ParamGrid
from long_earn.backtest.engine.strategy import BaseStrategy
from long_earn.backtest.engine.timeseries_split import TimeSeriesSplit
from long_earn.backtest.models import BacktestResult

__all__ = [
    "_MAX_GRID_DEFAULT",
    "BacktestDomainError",
    "BacktestExecutionError",
    "BacktestResult",
    "BaseStrategy",
    "DataLoadError",
    "DateRange",
    "EventDrivenBacktestEngine",
    "ExpressionEvalError",
    "InMemoryAuditTrail",
    "ParamGrid",
    "PerformanceMetrics",
    "Position",
    "StrategyDSL",
    "StrategyValidationError",
    "TimeSeriesSplit",
    "UniverseError",
    "parse_strategy_yaml",
]
