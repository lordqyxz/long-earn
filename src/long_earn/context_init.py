"""上下文初始化模块

提供统一的运行时上下文创建和初始化。
"""

from long_earn.config import AppConfig, RuntimeContext
from long_earn.services.backtest_service import BacktestServiceImpl
from long_earn.services.llm_service import LLMServiceImpl
from long_earn.services.logger_service import LoggerServiceImpl
from long_earn.services.memory_service import MemoryServiceImpl
from long_earn.services.monitoring_service import MonitoringServiceImpl
from long_earn.services.stock_service import StockServiceImpl


def create_runtime_context(config: AppConfig | None = None) -> RuntimeContext:
    """创建运行时上下文

    Args:
        config: 应用配置，None 则从环境变量加载

    Returns:
        初始化好的 RuntimeContext
    """
    if config is None:
        config = AppConfig.from_env()

    errors = config.validate()
    if errors:
        raise ValueError(f"配置验证失败：{', '.join(errors)}")

    logger = LoggerServiceImpl()
    monitoring = MonitoringServiceImpl(enabled=True)

    # 创建临时上下文用于服务初始化
    temp_ctx = RuntimeContext(
        config=config,
        llm_service=None,  # type: ignore[arg-type]
        memory=None,  # type: ignore[arg-type]
        stock_service=None,  # type: ignore[arg-type]
        backtest_service=None,  # type: ignore[arg-type]
        logger=logger,
        monitoring=monitoring,
    )

    llm_service = LLMServiceImpl(temp_ctx)
    stock_service = StockServiceImpl(temp_ctx)
    backtest_service = BacktestServiceImpl(temp_ctx)
    memory_service = MemoryServiceImpl(temp_ctx)

    return RuntimeContext(
        config=config,
        llm_service=llm_service,
        memory=memory_service,
        stock_service=stock_service,
        backtest_service=backtest_service,
        logger=logger,
        monitoring=monitoring,
    )


def initialize_context(config: AppConfig | None = None) -> RuntimeContext:
    """初始化运行时上下文

    应用启动时调用，完成记忆系统的初始化和回测引擎就绪检查。

    Args:
        config: 应用配置

    Returns:
        初始化好的 RuntimeContext
    """
    context = create_runtime_context(config)

    # 初始化记忆系统（加载持久化数据 + init 目录）
    context.memory.initialize()

    context.logger.info("回测引擎已就绪（内嵌模式）")

    return context
