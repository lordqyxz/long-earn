"""上下文初始化模块

提供统一的运行时上下文创建和初始化。
"""

from typing import TYPE_CHECKING

from long_earn.backtest.data.cache import DataCache
from long_earn.backtest.data.connector import (
    CompositeDataConnector as DataConnectorImpl,
)
from long_earn.config import AppConfig, RuntimeContext
from long_earn.ontology import Connector, OntologyRegistry
from long_earn.operator_dev.backlog import OperatorBacklog
from long_earn.services.backtest_service import BacktestServiceImpl
from long_earn.services.llm_service import LLMServiceImpl
from long_earn.services.logger_service import LoggerServiceImpl
from long_earn.services.memory_service import MemoryServiceImpl
from long_earn.services.monitoring_service import MonitoringServiceImpl
from long_earn.services.stock_service import StockServiceImpl

if TYPE_CHECKING:
    from long_earn.backtest.data.provider import MarketIntelligenceProvider
    from long_earn.backtest.data.realtime import RealtimeDataProvider


def create_runtime_context(config: AppConfig | None = None) -> RuntimeContext:
    """创建运行时上下文（一次性构造完整 DI Container）

    构造顺序（Clean Architecture）：
    1. 基础设施层（config / logger / monitoring）
    2. 数据层（data_connector，带 DuckDB 缓存）
    3. 业务服务层（llm / stock / backtest / memory）——
       接 `(config, logger)`（必要时 `data_connector`），与 ctx 解耦

    Args:
        config: 应用配置，None 则从环境变量加载

    Returns:
        所有字段（除 data_connector 外）均为非空的 RuntimeContext
    """
    if config is None:
        config = AppConfig.from_env()

    errors = config.validate()
    if errors:
        raise ValueError(f"配置验证失败：{', '.join(errors)}")

    # 1. 基础设施层
    logger = LoggerServiceImpl()
    monitoring = MonitoringServiceImpl(enabled=True)

    # 2. 数据层（带 DuckDB 缓存；ADR-014 阶段 F：DataConnector 替代 DataProvider）
    data_cache = DataCache()
    data_connector = DataConnectorImpl(cache=data_cache)

    # 2b. 算子缺口队列（strategy_rd gap_detector 写入 / operator_dev 消费）
    operator_backlog = OperatorBacklog()

    # 2b-adr014. 本体论注册表（先 seed，Connector 在 memory 就绪后注入）
    ontology_registry = OntologyRegistry()
    try:
        ontology_registry.seed()
    except Exception as exc:
        logger.warning(f"ontology 种子装载失败（非致命）: {exc}")

    # 2c. 市场情报能力（ciccwm 可用时注入；与 data_connector 分离的第二组接口）
    market_intelligence: MarketIntelligenceProvider | None = None
    try:
        from long_earn.backtest.data.ciccwm_provider import (  # noqa: PLC0415
            CiccwmDataProvider,
        )

        _ciccwm_intel = CiccwmDataProvider(data_cache)
        if _ciccwm_intel.is_available:
            market_intelligence = _ciccwm_intel
    except Exception as exc:
        logger.warning(f"market_intelligence 初始化失败: {exc}")

    # 2d. 实时行情能力（ADR-018：显式多源切换，非静默降级链）
    realtime_provider: RealtimeDataProvider | None = None
    try:
        from long_earn.backtest.data.realtime import (  # noqa: PLC0415
            CompositeRealtimeProvider,
        )

        realtime_provider = CompositeRealtimeProvider()
    except Exception as exc:
        logger.warning(f"realtime_provider 初始化失败: {exc}")

    # 3. 业务服务层 —— 已解耦，直接接 (config, logger) 构造
    llm_service = LLMServiceImpl(config, logger)
    # ADR-014 阶段 D：MemoryServiceImpl 注入 OntologyGraph（motion.activate 走图遍历）
    memory = MemoryServiceImpl(
        config,
        logger,
        ontology_graph=ontology_registry.graph,
    )
    # ADR-018：Connector 注入 memory_provider，经验/事件检索可用
    connector = Connector(
        registry=ontology_registry,
        data_provider=data_connector,
        memory_provider=memory,
    )
    # ADR-014 阶段 C：StockServiceImpl 注入 Connector（get_financial_metrics 走概念查询）
    stock_service = StockServiceImpl(config, logger, connector=connector)
    backtest_service = BacktestServiceImpl(
        config,
        logger,
        data_provider=data_connector,
        max_workers=getattr(config, "max_workers", 0),
    )

    return RuntimeContext(
        config=config,
        logger=logger,
        monitoring=monitoring,
        llm_service=llm_service,
        memory=memory,
        stock_service=stock_service,
        backtest_service=backtest_service,
        data_provider=data_connector,
        market_intelligence=market_intelligence,
        realtime_provider=realtime_provider,
        operator_backlog=operator_backlog,
        connector=connector,
        ontology_registry=ontology_registry,
    )


def initialize_context(config: AppConfig | None = None) -> RuntimeContext:
    """初始化运行时上下文

    应用启动时调用，完成：
    1. 记忆系统的初始化
    2. **启动时数据缓存同步**（ADR-014 阶段 G）：从 xtquant 增量同步到 DuckDB 缓存，
       同步完成后设置 ``LONG_EARN_CACHE_ONLY=1``，后续所有数据访问走纯缓存，
       支持高并发并行回测（worker 进程不触达 xtquant）。
    3. 回测引擎就绪检查

    跳过同步的场景（环境变量 ``LONG_EARN_SKIP_CACHE_SYNC=1``）：
    - CI / 单元测试（无 xtquant，无需同步）
    - 已知缓存新鲜，显式跳过加速启动
    - 纯 LLM 推理场景（不涉及数据访问）

    Args:
        config: 应用配置

    Returns:
        初始化好的 RuntimeContext
    """
    import os  # noqa: PLC0415

    context = create_runtime_context(config)
    context.require_memory().initialize()

    # 启动时数据缓存同步（ADR-014 阶段 G）
    skip_sync = os.environ.get("LONG_EARN_SKIP_CACHE_SYNC", "").strip().lower()
    if skip_sync in ("1", "true", "yes", "on"):
        context.logger.info("LONG_EARN_SKIP_CACHE_SYNC=1，跳过启动时数据同步")
    else:
        from long_earn.services.cache_sync import sync_data_cache  # noqa: PLC0415

        try:
            sync_data_cache(logger_service=context.logger)
        except Exception as exc:
            context.logger.warning(f"启动时数据同步异常（非致命，降级到纯缓存）: {exc}")
            from long_earn.services.cache_sync import (  # noqa: PLC0415
                set_cache_only,
            )

            set_cache_only()

    context.logger.info("回测引擎已就绪（内嵌模式）")
    return context
