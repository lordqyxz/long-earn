"""BacktestServiceImpl 单元测试

测试远程 HTTP API 回测服务的实现。
"""

from unittest.mock import MagicMock, patch

import pytest

from long_earn.config import AppConfig, RuntimeContext
from long_earn.services.backtest_service import BacktestServiceImpl


def _make_context():
    """创建测试用的 RuntimeContext mock"""
    config = AppConfig()
    config.backtest_start_date = "2023-01-01"
    config.backtest_end_date = "2023-03-31"

    mock_logger = MagicMock()
    mock_backtest = MagicMock()
    mock_llm = MagicMock()
    mock_knowledge = MagicMock()
    mock_stock = MagicMock()
    mock_monitoring = MagicMock()

    context = RuntimeContext(
        config=config,
        logger=mock_logger,
        backtest_service=mock_backtest,
        llm_service=mock_llm,
        knowledge_service=mock_knowledge,
        stock_service=mock_stock,
        monitoring=mock_monitoring,
    )
    return context


# ---------- 测试：初始化 ----------


class TestBacktestServiceImplInit:
    def test_init_sets_attributes(self):
        context = _make_context()
        svc = BacktestServiceImpl(context)

        assert svc.context is context
        assert svc.logger is context.logger
        assert svc.config is context.config


# ---------- 测试：run_backtest ----------


class TestRunBacktest:
    @patch("long_earn.services.backtest_service.run_backtest")
    def test_delegates_to_remote_service(self, mock_run_backtest):
        """run_backtest 应委托给远程 HTTP API"""
        mock_run_backtest.return_value = {
            "total_return": 0.15,
            "annual_return": 0.12,
            "sharpe_ratio": 0.8,
            "max_drawdown": -0.1,
            "win_rate": 0.6,
            "trading_days": 60,
        }

        context = _make_context()
        svc = BacktestServiceImpl(context)

        result = svc.run_backtest(
            strategy_code="class TestStrategy: pass",
            start_date="2023-01-01",
            end_date="2023-03-31",
        )

        mock_run_backtest.assert_called_once_with(
            strategy_code="class TestStrategy: pass",
            start_date="2023-01-01",
            end_date="2023-03-31",
            stock_list=None,
        )
        assert result is not None
        assert result["total_return"] == 0.15

    @patch("long_earn.services.backtest_service.run_backtest")
    def test_uses_config_defaults(self, mock_run_backtest):
        """未指定日期时应使用配置中的默认日期"""
        mock_run_backtest.return_value = {"total_return": 0.1}

        context = _make_context()
        svc = BacktestServiceImpl(context)

        svc.run_backtest(strategy_code="test code")

        mock_run_backtest.assert_called_once_with(
            strategy_code="test code",
            start_date="2023-01-01",
            end_date="2023-03-31",
            stock_list=None,
        )

    @patch("long_earn.services.backtest_service.run_backtest")
    def test_returns_none_on_remote_failure(self, mock_run_backtest):
        """远程服务失败时应返回 None"""
        mock_run_backtest.return_value = None

        context = _make_context()
        svc = BacktestServiceImpl(context)

        result = svc.run_backtest(strategy_code="bad code")
        assert result is None

    @patch("long_earn.services.backtest_service.run_backtest")
    def test_passes_stock_list(self, mock_run_backtest):
        """应正确传递 stock_list 参数"""
        mock_run_backtest.return_value = {"total_return": 0.1}

        context = _make_context()
        svc = BacktestServiceImpl(context)

        stock_list = ["SH600519", "SZ000001"]
        svc.run_backtest(
            strategy_code="test", stock_list=stock_list
        )

        mock_run_backtest.assert_called_once_with(
            strategy_code="test",
            start_date="2023-01-01",
            end_date="2023-03-31",
            stock_list=stock_list,
        )

    @patch("long_earn.services.backtest_service.run_backtest")
    def test_logs_on_success(self, mock_run_backtest):
        """成功回测应记录日志"""
        mock_run_backtest.return_value = {
            "total_return": 0.15,
            "sharpe_ratio": 0.8,
            "max_drawdown": -0.1,
        }

        context = _make_context()
        svc = BacktestServiceImpl(context)

        svc.run_backtest(strategy_code="test")

        # 验证调用了 logger.info
        context.logger.info.assert_called()

    @patch("long_earn.services.backtest_service.run_backtest")
    def test_returns_error_result(self, mock_run_backtest):
        """远程服务返回错误结果时应原样返回"""
        error_result = {
            "error": "代码逻辑错误",
            "error_category": "code_logic",
            "error_detail": "SyntaxError",
            "message": "invalid syntax",
        }
        mock_run_backtest.return_value = error_result

        context = _make_context()
        svc = BacktestServiceImpl(context)

        result = svc.run_backtest(strategy_code="bad code")
        assert result is not None
        assert "error" in result