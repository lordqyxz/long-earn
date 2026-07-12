"""知识存储工具

基于物质-运动统一架构（SubstanceStore）的知识持久化。
提供 init_system 供系统初始化使用。

ADR-007 Phase 4：写入路径收敛到 AppConfig.memory_path（DuckDB），
不再直接读 os.getenv，与 MemoryServiceImpl 单一数据源对齐。
"""

from pathlib import Path

from long_earn.config import AppConfig
from long_earn.services.logger_service import LoggerServiceImpl
from long_earn.substance.store import SubstanceStore

LOGGER = LoggerServiceImpl()


def init_system(config: AppConfig | None = None) -> None:
    """系统初始化 — 扫描 init 目录并加载到记忆系统（DuckDB 持久化）。

    Args:
        config: 应用配置，None 则从环境变量加载。统一走 ``AppConfig.memory_path``
    """
    LOGGER.info("开始系统初始化...")
    config = config or AppConfig.from_env()
    store = SubstanceStore()

    init_dir = Path(config.init_dir)
    if init_dir.exists():
        count = store.load_directory(init_dir)
        if count > 0:
            LOGGER.info(f"知识库加载完成，共 {count} 条事实")

            memory_path = Path(config.memory_path).expanduser()
            memory_path.parent.mkdir(parents=True, exist_ok=True)
            store.save(memory_path)

    LOGGER.info("系统初始化完成")
