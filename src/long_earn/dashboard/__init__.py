"""Dashboard 可视化层

提供回测结果分析、风险指标计算和 Web 可视化仪表盘。
只依赖 backtest/domain/interfaces.py（AuditProvider Protocol）和 backtest/engine/audit.py。
事件流分析器额外依赖 substance/（ADR-007 Phase 3）。
"""

from long_earn.dashboard.analyzer import BacktestAnalyzer
from long_earn.dashboard.api import (
    BacktestAPIHandler,
    VisualizationServer,
    serve_visualization,
)
from long_earn.dashboard.event_analyzer import EventAnalyzer

__all__ = [
    "BacktestAPIHandler",
    "BacktestAnalyzer",
    "EventAnalyzer",
    "VisualizationServer",
    "serve_visualization",
]
