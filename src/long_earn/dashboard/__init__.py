"""Dashboard 可视化层

提供回测结果分析、风险指标计算和 Web 可视化仪表盘。
基于 FastAPI + React SPA 架构。
"""

from long_earn.dashboard.analyzer import BacktestAnalyzer
from long_earn.dashboard.event_analyzer import EventAnalyzer
from long_earn.dashboard.fastapi_app import (
    serve_visualization_fastapi,
)

__all__ = [
    "BacktestAnalyzer",
    "EventAnalyzer",
    "serve_visualization_fastapi",
]
