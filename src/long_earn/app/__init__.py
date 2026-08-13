"""Web 应用层（原 Dashboard 可视化层）

提供回测结果分析、风险指标计算和 Web 可视化服务。
基于 FastAPI + React SPA 架构。
"""

from long_earn.app.analyzer import BacktestAnalyzer
from long_earn.app.app import (
    serve_visualization_fastapi,
)
from long_earn.app.event_analyzer import EventAnalyzer

__all__ = [
    "BacktestAnalyzer",
    "EventAnalyzer",
    "serve_visualization_fastapi",
]
