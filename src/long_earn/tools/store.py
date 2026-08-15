"""知识存储工具

基于物质-运动统一架构（SubstanceStore）的知识持久化。
提供 init_system 供系统初始化使用。

ADR-007 Phase 4：写入路径收敛到 AppConfig.memory_path。
PG 全量迁移后：物质存储位于 PostgreSQL（core.pg 裁决连接参数），
memory_path 仅保留为兼容旧调用方。
"""

from pathlib import Path

from long_earn.config import AppConfig
from long_earn.services.logger_service import LoggerServiceImpl
from long_earn.substance.store import SubstanceStore

LOGGER = LoggerServiceImpl()


def init_system(config: AppConfig | None = None) -> None:
    """系统初始化 — 扫描 init 目录并加载到记忆系统（PostgreSQL 持久化）。

    Args:
        config: 应用配置，None 则从环境变量加载。统一走 ``AppConfig.memory_path``
            （PG 时代该路径仅作兼容，连接由 core.pg 裁决）
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
            store.save(memory_path)

    LOGGER.info("系统初始化完成")
