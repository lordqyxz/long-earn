"""BacktestServiceImpl 单元测试"""

from unittest.mock import MagicMock, patch

from long_earn.config import AppConfig, RuntimeContext
from long_earn.services.backtest_service import BacktestServiceImpl


def _make_context():
    """创建测试用的 RuntimeContext mock"""
    config = AppConfig()
    config.backtest_start_date = "2023-01-01"
    config.backtest_end_date = "2023-03-31"

    context = RuntimeContext(
        config=config,
        logger=MagicMock(),
        backtest_service=MagicMock(),
        llm_service=MagicMock(),
        memory=MagicMock(),
        stock_service=MagicMock(),
        monitoring=MagicMock(),
    )
    return context


class TestRunBacktest:
    @patch("long_earn.services.backtest_service._run_backtest")
    def test_delegates_to_local_engine(self, mock_run):
        """run 应直接调用向量化回测引擎"""
        from long_earn.backtest.models import BacktestResult

        mock_run.return_value = BacktestResult(
            success=True,
            message="回测成功",
            total_return=0.15,
            annual_return=0.12,
            sharpe_ratio=0.8,
            max_drawdown=-0.1,
            win_rate=0.6,
            trading_days=60,
            volatility=0.05,
            calmar_ratio=1.2,
            sortino_ratio=1.0,
            daily_returns=[],
            positions_history=[],
        )

        context = _make_context()
        svc = BacktestServiceImpl(context)

        result = svc.run(
            strategy_yaml="strategy:\n  name: Test\n",
            start_date="2023-01-01",
            end_date="2023-03-31",
        )

        mock_run.assert_called_once()
        assert result is not None
        assert result["total_return"] == 0.15

    @patch("long_earn.services.backtest_service._run_backtest")
    def test_returns_error_on_engine_failure(self, mock_run):
        """回测引擎失败时应返回包含 error 的结构化字典"""
        from long_earn.backtest.models import BacktestResult

        mock_run.return_value = BacktestResult(
            success=False,
            message="策略引用了不存在的字段",
            error_category="strategy_validation",
            error_detail="可用字段: [...]，缺失字段: ['foo']",
        )

        context = _make_context()
        svc = BacktestServiceImpl(context)

        result = svc.run(strategy_yaml="bad strategy")
        assert result is not None
        assert "error" in result
        assert result["error_category"] == "strategy_validation"

    def test_returns_error_when_no_strategy(self):
        """未提供任何策略时应返回客户端错误"""
        context = _make_context()
        svc = BacktestServiceImpl(context)

        result = svc.run(strategy_yaml="")
        assert result is not None
        assert "error" in result
        assert result["error_category"] == "client_error"
