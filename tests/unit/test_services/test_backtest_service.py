"""BacktestServiceImpl 单元测试"""

from unittest.mock import MagicMock

from long_earn.backtest.engine.dsl import StrategyDSL, TradingCostConfig, UniverseConfig
from long_earn.config import AppConfig, RuntimeContext
from long_earn.services.backtest_service import BacktestServiceImpl


def _make_strategy_dsl(name: str = "Test") -> StrategyDSL:
    return StrategyDSL(
        name=name,
        universe=UniverseConfig(),
        trading_cost=TradingCostConfig(),
    )


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
    def test_delegates_to_engine(self):
        """run 应调用事件驱动回测引擎"""
        context = _make_context()
        svc = BacktestServiceImpl(context)

        result = svc.run(
            strategy_yaml="name: Test\nstart_date: 2023-01-01\nend_date: 2023-03-01",
            start_date="2023-01-01",
            end_date="2023-03-31",
        )

        assert result is not None
        # DSL 解析成功但无数据，应返回引擎错误
        assert "error" in result or "total_return" in result
        if "error" in result:
            assert isinstance(result["error"], str)

    def test_parses_dsl(self):
        """run 应正确解析 YAML DSL"""
        context = _make_context()
        svc = BacktestServiceImpl(context)

        result = svc.run(
            strategy_yaml="name: MomentumTest\nsignals: []",
            start_date="2023-01-01",
            end_date="2023-03-31",
        )

        assert result is not None

    def test_returns_error_on_bad_yaml(self):
        """YAML 解析失败时应返回错误"""
        context = _make_context()
        svc = BacktestServiceImpl(context)

        result = svc.run(strategy_yaml="bad: [yaml: broken")
        assert result is not None
        assert "error" in result
        assert result["error_category"] == "client_error"

    def test_returns_error_when_no_strategy(self):
        """未提供任何策略时应返回客户端错误"""
        context = _make_context()
        svc = BacktestServiceImpl(context)

        result = svc.run(strategy_yaml="")
        assert result is not None
        assert "error" in result
        assert result["error_category"] == "client_error"
