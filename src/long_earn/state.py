from typing import Any, Dict, TypedDict


class State(TypedDict, total=True):
    """主图状态定义"""

    user_query: str
    status: str
    route: str
    routing_reason: str
    strategy_result: dict
    stock_analysis_result: dict
    summary: str
    error: str
    metrics: Dict[str, Any]
