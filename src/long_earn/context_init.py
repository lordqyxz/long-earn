"""上下文初始化模块

提供统一的运行时上下文创建和初始化。
"""

from long_earn.backtest.data.cache import DataCache
from long_earn.backtest.data.provider import MiniQmtDataProvider as DataProviderImpl
from long_earn.config import AppConfig, RuntimeContext
from long_earn.services.backtest_service import BacktestServiceImpl
from long_earn.services.llm_service import LLMServiceImpl
from long_earn.services.logger_service import LoggerServiceImpl
from long_earn.services.memory_service import MemoryServiceImpl
from long_earn.services.monitoring_service import MonitoringServiceImpl
from long_earn.services.stock_service import StockServiceImpl


def create_runtime_context(config: AppConfig | None = None) -> RuntimeContext:
    """创建运行时上下文

    构造顺序（Clean Architecture）：
    1. 基础设施层（config / logger / monitoring）—— 必须最先就绪
    2. 数据层（data_provider）
    3. 业务服务层（llm / stock / backtest / memory）—— 依赖上面两层

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

    # 1. 基础设施层
    logger = LoggerServiceImpl()
    monitoring = MonitoringServiceImpl(enabled=True)

    # 2. 数据层（带 DuckDB 缓存）
    data_cache = DataCache()
    data_provider = DataProviderImpl(cache=data_cache)

    # 3. 业务服务层 —— 复用同一个 ctx 实例，避免「temp_ctx → final_ctx」的双实例问题
    ctx = RuntimeContext(
        config=config,
        logger=logger,
        monitoring=monitoring,
        data_provider=data_provider,
    )
    ctx.llm_service = LLMServiceImpl(ctx)
    ctx.stock_service = StockServiceImpl(ctx)
    ctx.backtest_service = BacktestServiceImpl(ctx)
    ctx.memory = MemoryServiceImpl(ctx)

    return ctx


def initialize_context(config: AppConfig | None = None) -> RuntimeContext:
    """初始化运行时上下文

    应用启动时调用，完成记忆系统的初始化和回测引擎就绪检查。

    Args:
        config: 应用配置

    Returns:
        初始化好的 RuntimeContext
    """
    context = create_runtime_context(config)
    context.require_memory().initialize()
    context.logger.info("回测引擎已就绪（内嵌模式）")
    return context
