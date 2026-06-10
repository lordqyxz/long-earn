"""回测领域异常

业务领域异常，不遮蔽 Python 内置异常名。
"""


class BacktestDomainError(Exception):
    """回测领域基础异常"""

    pass


class StrategyValidationError(BacktestDomainError):
    """策略验证失败 — 策略参数不符合 DSL 规范"""

    pass


class DataLoadError(BacktestDomainError):
    """数据加载失败"""

    pass


class BacktestExecutionError(BacktestDomainError):
    """回测执行错误"""

    pass


class ExpressionEvalError(BacktestDomainError):
    """表达式求值错误"""

    pass


class UniverseError(BacktestDomainError):
    """股票池错误"""

    pass


class OrderExecutionError(BacktestDomainError):
    """订单执行失败 — 无效订单参数、价格条件不满足等"""

    pass
