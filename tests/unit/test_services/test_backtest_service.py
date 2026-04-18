"""BacktestServiceImpl 单元测试"""

from unittest.mock import MagicMock, patch

import pytest

from long_earn.config import AppConfig, RuntimeContext
from long_earn.services.backtest_service import QLIB_AVAILABLE, BacktestServiceImpl

# ---------- 简单策略代码，用于测试 ----------
SIMPLE_STRATEGY = '''
class SimpleStrategy:
    """简单测试策略：等权买入"""

    def generate_signals(self, date_str):
        return {"sh600519": 0.5, "sz000001": 0.5}
'''

EMPTY_STRATEGY = '''
class EmptyStrategy:
    """空策略：不产生信号"""

    def generate_signals(self, date_str):
        return None
'''

INVALID_STRATEGY = """
# 这段代码没有 generate_signals 方法的类
class NotAStrategy:
    def do_nothing(self):
        pass
"""

BROKEN_STRATEGY = """
# 语法错误的代码
class BrokenStrategy(
    def generate_signals(self, date_str):
        return {}
"""


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


# ---------- 测试：_get_trading_dates ----------


class TestGetTradingDates:
    def test_fallback_to_business_days(self):
        """当 qlib 不可用或失败时，应返回工作日列表"""
        context = _make_context()
        svc = BacktestServiceImpl(context)

        dates = svc._get_trading_dates("2023-01-01", "2023-01-31")
        assert len(dates) > 0
        # 2023年1月有约22个工作日
        assert 20 <= len(dates) <= 23

    @pytest.mark.skipif(not QLIB_AVAILABLE, reason="qlib not available")
    def test_qlib_calendar(self):
        """qlib 可用时，应使用 qlib 日历"""
        context = _make_context()
        svc = BacktestServiceImpl(context)

        dates = svc._get_trading_dates("2023-01-01", "2023-01-31")
        assert len(dates) > 0


# ---------- 测试：_get_portfolio_return_mock ----------


class TestGetPortfolioReturnMock:
    def test_basic_signals(self):
        context = _make_context()
        svc = BacktestServiceImpl(context)

        signals = {"sh600519": 0.5, "sz000001": 0.5}
        result = svc._get_portfolio_return_mock(signals, "2023-01-20")

        assert isinstance(result, float)

    def test_deterministic(self):
        """同一日期相同信号应返回相同结果（mock 使用 hash 做种子）"""
        context = _make_context()
        svc = BacktestServiceImpl(context)

        signals = {"sh600519": 0.5, "sz000001": 0.5}
        r1 = svc._get_portfolio_return_mock(signals, "2023-01-20")
        r2 = svc._get_portfolio_return_mock(signals, "2023-01-20")
        assert r1 == r2


# ---------- 测试：_calculate_metrics ----------


class TestCalculateMetrics:
    def test_positive_returns(self):
        context = _make_context()
        svc = BacktestServiceImpl(context)

        daily_returns = [0.01, 0.02, -0.01, 0.015, 0.005]
        metrics = svc._calculate_metrics(daily_returns, "2023-01-01", "2023-12-31")

        assert "total_return" in metrics
        assert "annual_return" in metrics
        assert "sharpe_ratio" in metrics
        assert "max_drawdown" in metrics
        assert "win_rate" in metrics
        assert "trading_days" in metrics

        assert metrics["trading_days"] == 5
        assert metrics["win_rate"] == 0.8  # 4/5 正收益

    def test_empty_returns(self):
        """空收益列表应返回零值指标"""
        context = _make_context()
        svc = BacktestServiceImpl(context)

        daily_returns = [0.0, 0.0, 0.0]
        metrics = svc._calculate_metrics(daily_returns, "2023-01-01", "2023-12-31")

        assert metrics["sharpe_ratio"] == 0.0
        assert metrics["total_return"] == 0.0


# ---------- 测试：run_backtest ----------


class TestRunBacktest:
    def test_simple_strategy_mock(self):
        """使用简单策略 + mock 数据运行回测"""
        context = _make_context()
        svc = BacktestServiceImpl(context)

        # 使用短日期范围加速测试
        result = svc.run_backtest(
            SIMPLE_STRATEGY,
            start_date="2023-01-01",
            end_date="2023-01-31",
        )

        # qlib 不可用时使用 mock，仍然应能返回结果
        assert result is not None
        assert "total_return" in result
        assert "trading_days" in result
        assert result["trading_days"] > 0

    def test_empty_strategy_returns_none(self):
        """空策略（不产生信号）应返回 None"""
        context = _make_context()
        svc = BacktestServiceImpl(context)

        result = svc.run_backtest(
            EMPTY_STRATEGY,
            start_date="2023-01-01",
            end_date="2023-01-31",
        )
        assert result is None

    def test_invalid_strategy_returns_none(self):
        """没有 generate_signals 方法的策略应返回 None"""
        context = _make_context()
        svc = BacktestServiceImpl(context)

        result = svc.run_backtest(
            INVALID_STRATEGY,
            start_date="2023-01-01",
            end_date="2023-01-31",
        )
        assert result is None

    def test_broken_syntax_returns_none(self):
        """语法错误的策略代码应返回 None"""
        context = _make_context()
        svc = BacktestServiceImpl(context)

        result = svc.run_backtest(
            BROKEN_STRATEGY,
            start_date="2023-01-01",
            end_date="2023-01-31",
        )
        assert result is None

    def test_uses_config_defaults(self):
        """未指定日期时应使用配置中的默认日期"""
        config = AppConfig()
        # 不覆盖 backtest 日期，使用 AppConfig 默认值
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
        svc = BacktestServiceImpl(context)

        # mock _get_trading_dates 来验证参数
        with patch.object(svc, "_get_trading_dates", return_value=[]) as mock_dates:
            svc.run_backtest(SIMPLE_STRATEGY)
            call_args = mock_dates.call_args
            assert call_args[0][0] == "2020-01-01"  # AppConfig default
            assert call_args[0][1] == "2023-12-31"  # AppConfig default

    def test_logger_called_on_error(self):
        """策略加载失败时应记录日志"""
        context = _make_context()
        svc = BacktestServiceImpl(context)

        svc.run_backtest(
            BROKEN_STRATEGY, start_date="2023-01-01", end_date="2023-01-31"
        )

        # Broken strategy causes syntax error, which should be logged
        context.logger.error.assert_called()
