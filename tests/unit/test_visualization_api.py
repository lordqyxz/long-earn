"""可视化 API 单元测试"""

from long_earn.dashboard.api import BacktestAPIHandler, serve_visualization


def test_handler_imports():
    """验证可视化 API 模块可导入且 Handler 类存在"""
    assert BacktestAPIHandler is not None
    assert hasattr(BacktestAPIHandler, "do_GET")
    assert hasattr(BacktestAPIHandler, "_health")
    assert hasattr(BacktestAPIHandler, "_list_runs")
    assert hasattr(BacktestAPIHandler, "_run_summary")
    assert hasattr(BacktestAPIHandler, "_run_equity")
    assert hasattr(BacktestAPIHandler, "_run_trades")
    assert hasattr(BacktestAPIHandler, "_run_signals")
    assert hasattr(BacktestAPIHandler, "_run_dashboard")


def test_serve_visualization_imports():
    """serve_visualization 函数可导入"""
    assert callable(serve_visualization)
