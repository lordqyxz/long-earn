"""上下文初始化模块

提供统一的上下文创建和配置函数。
使用直接属性赋值，无需复杂的注册机制。
"""

from long_earn.config import AppConfig, RuntimeContext
from long_earn.services.backtest_service import BacktestServiceImpl
from long_earn.services.knowledge_service import KnowledgeServiceImpl
from long_earn.services.llm_service import LLMServiceImpl
from long_earn.services.logger_service import LoggerServiceImpl
from long_earn.services.monitoring_service import MonitoringServiceImpl
from long_earn.services.stock_service import StockServiceImpl


def create_runtime_context(config: AppConfig | None = None) -> RuntimeContext:
    """创建运行时上下文

    参考 LangGraph Runtime 实践：
    1. 集中管理所有依赖
    2. 直接属性访问
    3. 类型安全

    Args:
        config: 应用配置

    Returns:
        初始化好的运行时上下文
    """
    if config is None:
        config = AppConfig.from_env()

    errors = config.validate()
    if errors:
        raise ValueError(f"配置验证失败：{', '.join(errors)}")

    logger = LoggerServiceImpl()
    monitoring = MonitoringServiceImpl(enabled=True)

    temp_context = RuntimeContext(
        config=config,
        llm_service=None,  # type: ignore
        knowledge_service=None,  # type: ignore
        stock_service=None,  # type: ignore
        backtest_service=None,  # type: ignore
        logger=logger,
        monitoring=monitoring,
    )

    llm_service = LLMServiceImpl(temp_context)
    stock_service = StockServiceImpl(temp_context)
    backtest_service = BacktestServiceImpl(temp_context)

    knowledge_service = KnowledgeServiceImpl(temp_context)

    return RuntimeContext(
        config=config,
        llm_service=llm_service,
        knowledge_service=knowledge_service,
        stock_service=stock_service,
        backtest_service=backtest_service,
        logger=logger,
        monitoring=monitoring,
    )


def initialize_context(config: AppConfig | None = None) -> RuntimeContext:
    """初始化运行时上下文

    在应用启动时调用，完成所有服务的初始化。

    Args:
        config: 应用配置

    Returns:
        初始化好的运行时上下文
    """
    context = create_runtime_context(config)

    # 初始化知识库
    context.knowledge_service.initialize()

    return context
