from typing import TypedDict


class State(TypedDict):
    """主图状态定义"""

    user_query: str  # 用户查询
    status: str  # 状态信息
    route: str  # 路由决策
    routing_reason: str  # 路由理由
    strategy_result: dict  # 策略研究结果
    stock_analysis_result: dict  # 股票分析结果
    summary: str  # 汇总结果
    error: str  # 错误信息
