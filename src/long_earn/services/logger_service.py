"""日志服务实现

封装 loguru，提供统一的日志接口。
"""

import sys

from typing import Optional

from loguru import logger as loguru_logger

from long_earn.services import LoggerService


class LoggerServiceImpl(LoggerService):
    """日志服务实现

    参考 LangGraph Runtime 实践：
    1. 作为可注入服务，而非全局依赖
    2. 支持配置化
    3. 便于测试时替换为 Mock

    用法:
        # 在 context 中注册
        context.set("logger", LoggerServiceImpl())

        # 在节点中使用
        logger = context.get("logger")
        logger.info("消息")
    """

    def __init__(self, level: str = "INFO", format: Optional[str] = None):
        """初始化日志服务

        Args:
            level: 日志级别
            format: 日志格式
        """
        self.level = level

        if format is None:
            format = (
                "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
                "<level>{level: <8}</level> | "
                "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
                "<level>{message}</level>"
            )

        # 配置 loguru
        loguru_logger.remove()
        loguru_logger.add(
            sys.stderr,
            level=level,
            format=format,
            backtrace=True,
            diagnose=True,
        )

    def debug(self, message: str) -> None:
        """调试日志"""
        loguru_logger.debug(message)

    def info(self, message: str) -> None:
        """信息日志"""
        loguru_logger.info(message)

    def warning(self, message: str) -> None:
        """警告日志"""
        loguru_logger.warning(message)

    def error(self, message: str) -> None:
        """错误日志"""
        loguru_logger.error(message)

    def exception(self, message: str) -> None:
        """异常日志"""
        loguru_logger.exception(message)


# 已移除向后兼容导出，请使用 context.get("logger")
