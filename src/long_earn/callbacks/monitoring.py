"""LangGraph 节点监控装饰器模块

最佳实践：使用装饰器模式实现监控，保持业务代码与监控代码分离。
"""

import functools
import logging
import time
from typing import Any, Callable, Dict, Optional, TypeVar, cast

logger = logging.getLogger("long_earn")

F = TypeVar("F", bound=Callable[..., Any])


class MonitoringContext:
    """监控上下文 - 单例模式，跨节点共享"""

    _instance: Optional["MonitoringContext"] = None

    def __init__(self):
        self.token_counts: Dict[str, int] = {"prompt": 0, "completion": 0, "total": 0}
        self.node_metrics: Dict[str, Dict[str, Any]] = {}
        self.errors: list = []

    @classmethod
    def get_instance(cls) -> "MonitoringContext":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def reset(self) -> None:
        self.token_counts = {"prompt": 0, "completion": 0, "total": 0}
        self.node_metrics = {}
        self.errors = []

    def record_token_usage(self, usage: Dict[str, int]) -> None:
        self.token_counts["prompt"] += usage.get("prompt_tokens", 0)
        self.token_counts["completion"] += usage.get("completion_tokens", 0)
        self.token_counts["total"] += usage.get("total_tokens", 0)

    def record_node_metric(
        self, node_name: str, duration: float, success: bool, error: Optional[str] = None
    ) -> None:
        self.node_metrics[node_name] = {
            "duration": duration,
            "success": success,
            "error": error,
            "timestamp": time.time(),
        }

    def get_metrics(self) -> Dict[str, Any]:
        return {
            "token_counts": self.token_counts.copy(),
            "node_metrics": self.node_metrics.copy(),
            "errors": self.errors.copy(),
        }


def monitor_node(node_name: Optional[str] = None):
    """节点监控装饰器

    用法:
        @monitor_node("intent_analyze")
        def intent_analyze_node(state: State) -> State:
            ...

        # 或自动使用函数名
        @monitor_node()
        def intent_analyze_node(state: State) -> State:
            ...
    """

    def decorator(func: F) -> F:
        name = node_name or func.__name__

        @functools.wraps(func)
        def wrapper(state: Dict[str, Any], *args, **kwargs) -> Dict[str, Any]:
            ctx = MonitoringContext.get_instance()
            start_time = time.time()

            logger.info(f"[{name}] 节点开始执行")

            try:
                result = func(state, *args, **kwargs)
                duration = time.time() - start_time

                ctx.record_node_metric(name, duration, success=True)
                logger.info(f"[{name}] 节点执行完成，耗时: {duration:.2f}s")

                if result and isinstance(result, dict):
                    if "metrics" not in result:
                        result["metrics"] = {}
                    result["metrics"][name] = {"duration": duration, "success": True}

                return result

            except Exception as e:
                duration = time.time() - start_time
                error_msg = str(e)

                ctx.record_node_metric(name, duration, success=False, error=error_msg)
                ctx.errors.append({"node": name, "error": error_msg})

                logger.error(f"[{name}] 节点执行失败: {error_msg}", exc_info=True)

                return {
                    "error": error_msg,
                    "metrics": {name: {"duration": duration, "success": False, "error": error_msg}},
                }

        return cast(F, wrapper)

    return decorator


def track_tokens(usage: Dict[str, int]) -> None:
    """记录 token 使用量（在节点内部调用）

    用法:
        response = llm.invoke(prompt)
        if hasattr(response, 'usage_metadata'):
            track_tokens(response.usage_metadata)
    """
    ctx = MonitoringContext.get_instance()
    ctx.record_token_usage(usage)
    logger.info(
        f"Token 使用: prompt={usage.get('prompt_tokens', 0)}, "
        f"completion={usage.get('completion_tokens', 0)}, "
        f"total={usage.get('total_tokens', 0)}"
    )


def get_monitoring_metrics() -> Dict[str, Any]:
    """获取监控指标"""
    return MonitoringContext.get_instance().get_metrics()


def reset_monitoring() -> None:
    """重置监控数据"""
    MonitoringContext.get_instance().reset()
