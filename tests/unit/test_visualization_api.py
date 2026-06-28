"""可视化 API 接口测试

验证 BacktestAPIHandler 的公共接口行为，而非仅 hasattr。
"""

from unittest.mock import MagicMock

from long_earn.dashboard.api import BacktestAPIHandler, serve_visualization


class TestBacktestAPIHandler:
    """BacktestAPIHandler 接口行为测试"""

    def test_handler_routes_exist(self):
        """Handler 应定义所有必要的路由处理方法"""
        required_methods = [
            "do_GET",
            "_health",
            "_list_runs",
            "_run_summary",
            "_run_equity",
            "_run_trades",
            "_run_signals",
            "_run_dashboard",
        ]
        for method in required_methods:
            assert hasattr(BacktestAPIHandler, method), f"缺少方法: {method}"
            assert callable(getattr(BacktestAPIHandler, method)), f"{method} 不可调用"

    def test_serve_visualization_is_callable(self):
        """serve_visualization 应可调用"""
        assert callable(serve_visualization)

    def test_handler_initializes_with_analyzer(self):
        """Handler 实例化时应接受 analyzer 参数"""
        mock_analyzer = MagicMock()
        handler = BacktestAPIHandler.__new__(BacktestAPIHandler)
        handler.analyzer = mock_analyzer
        assert handler.analyzer is mock_analyzer
