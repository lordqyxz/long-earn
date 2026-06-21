"""监控服务实现

提供节点执行和提示词监控功能。
"""

import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import wraps
from typing import Any

from long_earn.services import LoggerService, MonitoringService


@dataclass
class PerformanceMetrics:
    """性能指标"""

    execution_count: int = 0
    total_time: float = 0.0
    success_count: int = 0
    error_count: int = 0
    token_usage: dict[str, int] = field(default_factory=dict)

    @property
    def avg_time(self) -> float:
        """平均执行时间"""
        if self.execution_count == 0:
            return 0.0
        return self.total_time / self.execution_count

    @property
    def success_rate(self) -> float:
        """成功率"""
        if self.execution_count == 0:
            return 0.0
        return self.success_count / self.execution_count


class MonitoringContext:
    """监控上下文管理器

    用于 with 语句，自动记录节点执行的开始、结束和异常。

    用法:
        with monitoring.track("node_name"):
            # 执行业务逻辑
            result = do_something()
    """

    def __init__(self, monitoring: "MonitoringServiceImpl", node_name: str):
        self.monitoring = monitoring
        self.node_name = node_name
        self.metrics = monitoring._get_metrics(node_name)
        self.start_time: float = 0.0

    def __enter__(self):
        """进入上下文，记录开始时间"""
        self.start_time = time.time()
        self.metrics.execution_count += 1
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出上下文，记录执行时间和成功/失败状态"""
        elapsed = time.time() - self.start_time
        self.metrics.total_time += elapsed

        if exc_type is None:
            self.metrics.success_count += 1
        else:
            self.metrics.error_count += 1

        return False


class MonitoringServiceImpl(MonitoringService):
    """监控服务实现

    参考 LangGraph Runtime 实践：
    1. 作为可注入服务
    2. 支持动态启用/禁用
    3. 提供上下文管理器和装饰器两种方式

    用法:
        # 在 context 中使用
        context = RuntimeContext(monitoring=MonitoringServiceImpl())

        # 使用上下文管理器（推荐）
        with context.monitoring.track("node_name"):
            # 执行业务逻辑
            result = do_something()

        # 使用装饰器（适用于简单场景）
        @context.monitoring.monitor_node("node_name")
        def my_node(state):
            ...
    """

    def __init__(self, enabled: bool = True):
        """初始化监控服务

        Args:
            enabled: 是否启用监控
        """
        self.enabled = enabled
        self._metrics: dict[str, PerformanceMetrics] = {}
        self._prompt_metrics: dict[str, PerformanceMetrics] = {}

    def _get_metrics(self, name: str) -> PerformanceMetrics:
        """获取性能指标

        Args:
            name: 名称

        Returns:
            性能指标
        """
        if name not in self._metrics:
            self._metrics[name] = PerformanceMetrics()
        return self._metrics[name]

    def _get_prompt_metrics(self, name: str) -> PerformanceMetrics:
        """获取提示词性能指标

        Args:
            name: 名称

        Returns:
            性能指标
        """
        if name not in self._prompt_metrics:
            self._prompt_metrics[name] = PerformanceMetrics()
        return self._prompt_metrics[name]

    @contextmanager
    def track(self, node_name: str):
        """创建监控上下文管理器

        用法:
            with monitoring.track("intent_analyze"):
                # 执行业务逻辑
                result = do_something()

        Args:
            node_name: 节点名称

        Yields:
            MonitoringContext 实例
        """
        ctx = MonitoringContext(self, node_name)
        with ctx:
            yield ctx

    def monitor_node(self, node_name: str):
        """节点监控装饰器

        用法:
            @monitor_node("intent_analyze")
            def intent_analyze_node(state):
                ...
        """

        def decorator(func: Callable):
            @wraps(func)
            def wrapper(state: Any, *args, **kwargs):
                if not self.enabled:
                    return func(state, *args, **kwargs)

                metrics = self._get_metrics(node_name)
                start_time = time.time()

                try:
                    result = func(state, *args, **kwargs)
                    metrics.execution_count += 1
                    metrics.success_count += 1
                    return result
                except Exception:
                    metrics.execution_count += 1
                    metrics.error_count += 1
                    raise
                finally:
                    elapsed = time.time() - start_time
                    metrics.total_time += elapsed

            return wrapper

        return decorator

    def monitor_prompt(self, prompt_name: str):
        """提示词监控装饰器

        用法:
            @monitor_prompt("routing_prompt")
            def generate_routing_prompt():
                ...
        """

        def decorator(func: Callable):
            @wraps(func)
            def wrapper(*args, **kwargs):
                if not self.enabled:
                    return func(*args, **kwargs)

                metrics = self._get_prompt_metrics(prompt_name)
                start_time = time.time()

                try:
                    result = func(*args, **kwargs)
                    metrics.execution_count += 1
                    metrics.success_count += 1
                    return result
                except Exception:
                    metrics.execution_count += 1
                    metrics.error_count += 1
                    raise
                finally:
                    elapsed = time.time() - start_time
                    metrics.total_time += elapsed

            return wrapper

        return decorator

    def track_tokens(self, usage_metadata: dict[str, Any]) -> None:
        """追踪 token 使用

        Args:
            usage_metadata: token 使用元数据
        """
        if not self.enabled:
            return

        # 记录到全局指标
        for key, value in usage_metadata.items():
            if isinstance(value, int):
                if (
                    key
                    not in self._metrics.get("global", PerformanceMetrics()).token_usage
                ):
                    self._get_metrics("global").token_usage[key] = 0
                self._get_metrics("global").token_usage[key] += value

    def get_metrics(self, name: str) -> PerformanceMetrics | None:
        """获取节点性能指标

        Args:
            name: 节点名称

        Returns:
            性能指标
        """
        return self._metrics.get(name)

    def get_prompt_metrics(self, name: str) -> PerformanceMetrics | None:
        """获取提示词性能指标

        Args:
            name: 提示词名称

        Returns:
            性能指标
        """
        return self._prompt_metrics.get(name)

    def get_all_metrics(self) -> dict[str, PerformanceMetrics]:
        """获取所有性能指标

        Returns:
            所有指标
        """
        return {**self._metrics, **self._prompt_metrics}

    def log_report(self, logger: LoggerService) -> None:
        """输出性能报告

        Args:
            logger: 日志服务
        """
        logger.info("=" * 60)
        logger.info("性能监控报告")
        logger.info("=" * 60)

        for name, metrics in self._metrics.items():
            logger.info(
                f"节点：{name} | "
                f"执行：{metrics.execution_count} | "
                f"成功：{metrics.success_count} | "
                f"失败：{metrics.error_count} | "
                f"平均时间：{metrics.avg_time:.3f}s | "
                f"成功率：{metrics.success_rate:.2%}"
            )

        for name, metrics in self._prompt_metrics.items():
            logger.info(
                f"提示词：{name} | "
                f"执行：{metrics.execution_count} | "
                f"成功：{metrics.success_count} | "
                f"失败：{metrics.error_count} | "
                f"平均时间：{metrics.avg_time:.3f}s"
            )

        logger.info("=" * 60)


# 已移除向后兼容导出，请使用 context.get("monitoring")
