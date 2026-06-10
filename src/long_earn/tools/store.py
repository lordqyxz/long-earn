"""知识存储工具

基于 numpy/pandas 记忆系统的知识检索和持久化。
实际业务逻辑已迁移到 MemoryServiceImpl，本模块仅保留 init_system 供启动使用。
"""

import os
from pathlib import Path

from long_earn.memory.store import MemoryStore
from long_earn.services.logger_service import LoggerServiceImpl

LOGGER = LoggerServiceImpl()


def _get_store() -> MemoryStore:
    """获取 MemoryStore 实例"""
    return MemoryStore()


def init_system() -> None:
    """系统初始化 — 扫描 init 目录并加载到记忆系统"""
    LOGGER.info("开始系统初始化...")
    store = _get_store()

    init_dir = Path(os.getenv("INIT_DIR", "./init"))
    if init_dir.exists():
        count = store.load_directory(init_dir)
        if count > 0:
            LOGGER.info(f"知识库加载完成，共 {count} 条事实")

            memory_path = Path(
                os.path.expanduser(os.getenv("MEMORY_PATH", "~/.long_earn/memory.npz"))
            )
            memory_path.parent.mkdir(parents=True, exist_ok=True)
            store.save(memory_path)

    LOGGER.info("系统初始化完成")


def search_knowledge(
    query: str,
    k: int = 3,
    categories: list[str] | None = None,
    terms: list[str] | None = None,
    source_files: list[str] | None = None,
) -> list[str]:
    """搜索知识库（兼容旧接口）"""
    try:
        store = _get_store()
        return store.search_as_strings(
            query,
            k=k,
            categories=categories,
            terms=terms,
            source_files=source_files,
        )
    except Exception as e:
        LOGGER.error(f"搜索知识库失败: {e}")
        return []
