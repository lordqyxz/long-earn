import logging

from typing import Any, Dict

logger = logging.getLogger("long_earn")


class TokenCallback:
    """token统计回调函数"""

    def __init__(self):
        self.token_counts = {"prompt": 0, "completion": 0, "total": 0}

    def on_token_usage(self, usage: Dict[str, int]) -> None:
        """记录token使用情况"""
        # 更新token计数
        self.token_counts["prompt"] += usage.get("prompt_tokens", 0)
        self.token_counts["completion"] += usage.get("completion_tokens", 0)
        self.token_counts["total"] += usage.get("total_tokens", 0)

        # 记录日志
        logger.info(
            f"Token使用情况: 提示词={usage.get('prompt_tokens', 0)}, 完成={usage.get('completion_tokens', 0)}, 总计={usage.get('total_tokens', 0)}"
        )

    def get_token_counts(self) -> Dict[str, int]:
        """获取token统计"""
        return self.token_counts

    def reset(self) -> None:
        """重置token统计"""
        self.token_counts = {"prompt": 0, "completion": 0, "total": 0}
