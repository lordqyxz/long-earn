"""Dashboard 可视化层

提供回测结果分析、风险指标计算和 Web 可视化仪表盘。
只依赖 backtest/domain/interfaces.py（AuditProvider Protocol）和 backtest/engine/audit.py。
事件流分析器额外依赖 substance/（ADR-007 Phase 3）。

FastAPI 版本（推荐）：serve_visualization_fastapi
旧版兼容（stdlib http.server）：serve_visualization
"""

from long_earn.dashboard.analyzer import BacktestAnalyzer
from long_earn.dashboard.api import (
    BacktestAPIHandler,
    VisualizationServer,
    serve_visualization,
)
from long_earn.dashboard.event_analyzer import EventAnalyzer
from long_earn.dashboard.fastapi_app import (
    serve_visualization_fastapi,
)

__all__ = [
    "BacktestAPIHandler",
    "BacktestAnalyzer",
    "EventAnalyzer",
    "VisualizationServer",
    "serve_visualization",
    "serve_visualization_fastapi",
]
