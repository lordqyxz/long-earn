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
from long_earn.services.service_manager import LocalServiceManager, RemoteServiceManager
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
        service_manager=None,  # type: ignore
        logger=logger,
        monitoring=monitoring,
    )

    llm_service = LLMServiceImpl(temp_context)
    stock_service = StockServiceImpl(temp_context)
    backtest_service = BacktestServiceImpl(temp_context)
    knowledge_service = KnowledgeServiceImpl(temp_context)

    # 根据配置选择服务管理器：local 管理本地进程，remote 为空实现
    if config.service_manager_type == "local":
        service_manager = LocalServiceManager(temp_context)
    else:
        service_manager = RemoteServiceManager(temp_context)

    return RuntimeContext(
        config=config,
        llm_service=llm_service,
        knowledge_service=knowledge_service,
        stock_service=stock_service,
        backtest_service=backtest_service,
        service_manager=service_manager,
        logger=logger,
        monitoring=monitoring,
    )


def initialize_context(config: AppConfig | None = None) -> RuntimeContext:
    """初始化运行时上下文

    在应用启动时调用，完成所有服务的初始化。
    本地模式下会自动启动回测服务。

    Args:
        config: 应用配置

    Returns:
        初始化好的运行时上下文
    """
    context = create_runtime_context(config)

    # 初始化知识库
    context.knowledge_service.initialize()

    # 本地模式下自动启动回测服务
    if context.config.service_manager_type == "local":
        if not context.service_manager.is_running():
            context.logger.info("正在启动本地回测服务...")
            ok = context.service_manager.start()
            if not ok:
                context.logger.warning(
                    "本地回测服务启动失败，回测功能可能不可用。"
                    "请手动启动：cd backtest_service && uv run python -m long_earn_backtest"
                )
        else:
            context.logger.info("本地回测服务已在运行")

    return context
