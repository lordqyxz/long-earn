"""向量化回测引擎核心测试"""

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from long_earn.backtest.engine.core import VectorizedBacktestEngine, run_backtest
from long_earn.backtest.engine.dsl import (
    RiskControlConfig,
    StrategyDSL,
    UniverseConfig,
    WeightConfig,
)
from long_earn.backtest.models import BacktestResult


def _make_mock_data(
    symbols: list[str],
    start: str = "2024-01-01",
    end: str = "2024-01-20",
) -> pd.DataFrame:
    dates = pd.date_range(start, end, freq="B")
    idx = pd.MultiIndex.from_product([dates, symbols], names=["date", "symbol"])
    n = len(idx)
    rng = np.random.default_rng(42)
    return pd.DataFrame(
        {
            "open": rng.uniform(9, 11, n),
            "high": rng.uniform(10, 12, n),
            "low": rng.uniform(8, 10, n),
            "close": rng.uniform(9.5, 10.5, n),
            "volume": rng.uniform(1e6, 5e6, n),
            "amount": 0.0,
            "adj_factor": 1.0,
            "roe": rng.uniform(0.05, 0.20, n),
            "eps": rng.uniform(0.5, 2.0, n),
            "net_profit_yoy": rng.uniform(-0.1, 0.3, n),
        },
        index=idx,
    )


def _make_simple_strategy(
    symbols: list[str] | None = None,
) -> StrategyDSL:
    """创建简单的等权重策略 DSL

    factors 是 {name: expression} 字典，signals 是信号步骤列表。
    """
    _ = symbols  # unused, kept for caller readability
    return StrategyDSL(
        name="TestStrategy",
        universe=UniverseConfig(type="csi300"),
        start_date="2024-01-01",
        end_date="2024-01-20",
        factors={"momentum": "close / shift(close, 5) - 1"},
        signals=[
            {"type": "filter", "condition": "momentum > 0"},
            {"type": "rank", "by": "momentum", "top": 10},
        ],
        weights=WeightConfig(method="equal_weight", field=None),
        risk_control=RiskControlConfig(max_position_per_stock=0.3),
    )


def _make_mock_providers(symbols: list[str], data: pd.DataFrame):
    mock_dp = MagicMock()
    mock_dp.get_merged_panel.return_value = data
    mock_up = MagicMock()
    mock_up.get_symbols.return_value = symbols
    return mock_dp, mock_up


class TestVectorizedBacktestEngine:
    def test_init_default_providers(self):
        engine = VectorizedBacktestEngine()
        assert engine.data_provider is not None
        assert engine.universe_provider is not None

    def test_init_custom_providers(self):
        mock_dp = MagicMock()
        mock_up = MagicMock()
        engine = VectorizedBacktestEngine(
            data_provider=mock_dp, universe_provider=mock_up
        )
        assert engine.data_provider is mock_dp
        assert engine.universe_provider is mock_up

    def test_run_strategy_validation_error(self):
        mock_dp, mock_up = _make_mock_providers(["000001"], _make_mock_data(["000001"]))
        engine = VectorizedBacktestEngine(
            data_provider=mock_dp, universe_provider=mock_up
        )
        strategy = _make_simple_strategy()
        strategy.factors["bad"] = "nonexistent_field > 0"

        result = engine.run(strategy)
        assert not result.success
        assert result.error_category == "strategy_validation"

    def test_run_empty_universe(self):
        mock_dp = MagicMock()
        mock_up = MagicMock()
        mock_up.get_symbols.return_value = []
        engine = VectorizedBacktestEngine(
            data_provider=mock_dp, universe_provider=mock_up
        )

        result = engine.run(_make_simple_strategy())
        assert not result.success
        assert result.error_category == "data_error"

    def test_run_empty_data(self):
        mock_dp = MagicMock()
        empty_idx = pd.MultiIndex.from_tuples([], names=["date", "symbol"])
        mock_dp.get_merged_panel.return_value = pd.DataFrame(index=empty_idx)
        mock_up = MagicMock()
        mock_up.get_symbols.return_value = ["000001"]
        engine = VectorizedBacktestEngine(
            data_provider=mock_dp, universe_provider=mock_up
        )

        result = engine.run(_make_simple_strategy())
        assert not result.success
        assert result.error_category == "data_error"

    def test_run_successful_backtest(self):
        symbols = ["000001", "000002", "000003"]
        data = _make_mock_data(symbols, "2024-01-01", "2024-03-29")
        mock_dp, mock_up = _make_mock_providers(symbols, data)
        engine = VectorizedBacktestEngine(
            data_provider=mock_dp, universe_provider=mock_up
        )

        result = engine.run(_make_simple_strategy())

        assert result.success
        assert result.message == "回测成功"
        assert result.total_return is not None
        assert result.sharpe_ratio is not None
        assert result.max_drawdown is not None
        assert result.trading_days is not None
        assert result.trading_days > 0
        assert len(result.daily_returns) > 0
        assert len(result.positions_history) > 0

    def test_run_exception_returns_error(self):
        mock_dp = MagicMock()
        mock_dp.get_merged_panel.side_effect = RuntimeError("connection refused")
        mock_up = MagicMock()
        mock_up.get_symbols.return_value = ["000001"]
        engine = VectorizedBacktestEngine(
            data_provider=mock_dp, universe_provider=mock_up
        )

        result = engine.run(_make_simple_strategy())
        assert not result.success
        assert result.error_category == "engine_error"


class TestVectorizedBacktestEngineWeights:
    def test_run_with_equal_weights(self):
        symbols = ["000001", "000002", "000003"]
        data = _make_mock_data(symbols)
        mock_dp, mock_up = _make_mock_providers(symbols, data)
        engine = VectorizedBacktestEngine(
            data_provider=mock_dp, universe_provider=mock_up
        )
        strategy = StrategyDSL(
            name="EqualTest",
            universe=UniverseConfig(type="csi300"),
            start_date="2024-01-01",
            end_date="2024-01-20",
            factors={"mom": "close / shift(close, 5) - 1"},
            signals=[{"type": "filter", "condition": "mom > -10"}],
            weights=WeightConfig(method="equal"),
        )
        result = engine.run(strategy)
        assert result.success
        assert result.total_return is not None

    def test_run_with_signal_weights(self):
        symbols = ["000001", "000002", "000003"]
        data = _make_mock_data(symbols)
        mock_dp, mock_up = _make_mock_providers(symbols, data)
        engine = VectorizedBacktestEngine(
            data_provider=mock_dp, universe_provider=mock_up
        )
        strategy = StrategyDSL(
            name="SignalTest",
            universe=UniverseConfig(type="csi300"),
            start_date="2024-01-01",
            end_date="2024-01-20",
            factors={"score": "close / shift(close, 5) - 1"},
            signals=[{"type": "filter", "condition": "close > 0"}],
            weights=WeightConfig(method="signal", signal_field="score"),
        )
        result = engine.run(strategy)
        assert result.success

    def test_run_with_formula_weights(self):
        symbols = ["000001", "000002", "000003"]
        data = _make_mock_data(symbols)
        mock_dp, mock_up = _make_mock_providers(symbols, data)
        engine = VectorizedBacktestEngine(
            data_provider=mock_dp, universe_provider=mock_up
        )
        strategy = StrategyDSL(
            name="FormulaTest",
            universe=UniverseConfig(type="csi300"),
            start_date="2024-01-01",
            end_date="2024-01-20",
            factors={"mom": "close / shift(close, 5) - 1"},
            signals=[{"type": "filter", "condition": "mom > -10"}],
            weights=WeightConfig(method="custom_formula", formula="mom"),
        )
        result = engine.run(strategy)
        assert result.success

    def test_run_with_unknown_weight_method(self):
        symbols = ["000001", "000002", "000003"]
        data = _make_mock_data(symbols)
        mock_dp, mock_up = _make_mock_providers(symbols, data)
        engine = VectorizedBacktestEngine(
            data_provider=mock_dp, universe_provider=mock_up
        )
        strategy = StrategyDSL(
            name="UnknownMethod",
            universe=UniverseConfig(type="csi300"),
            start_date="2024-01-01",
            end_date="2024-01-20",
            factors={"mom": "close / shift(close, 5) - 1"},
            signals=[{"type": "filter", "condition": "mom > -10"}],
            weights=WeightConfig(method="unknown_method"),
        )
        result = engine.run(strategy)
        assert result.success


class TestVectorizedBacktestEngineErrorPaths:
    def test_run_factor_computation_failure(self):
        """因子表达式使用了禁止的运算符 (>>)，求值失败但引擎应容错"""
        symbols = ["000001", "000002", "000003"]
        data = _make_mock_data(symbols)
        mock_dp, mock_up = _make_mock_providers(symbols, data)
        engine = VectorizedBacktestEngine(
            data_provider=mock_dp, universe_provider=mock_up
        )
        strategy = StrategyDSL(
            name="BadFactor",
            universe=UniverseConfig(type="csi300"),
            start_date="2024-01-01",
            end_date="2024-01-20",
            # close >> 1: close 是合法字段（通过校验），>> 是禁止的运算符（运行时失败）
            factors={"bad_factor": "close >> 1"},
            signals=[{"type": "filter", "condition": "close > 0"}],
            weights=WeightConfig(method="equal"),
        )
        # 引擎容错：因子失败记录警告，回测仍能完成
        result = engine.run(strategy)
        assert result.success

    def test_run_signal_filter_failure(self):
        """信号过滤条件使用了禁止的运算符，求值失败但引擎应容错"""
        symbols = ["000001", "000002", "000003"]
        data = _make_mock_data(symbols)
        mock_dp, mock_up = _make_mock_providers(symbols, data)
        engine = VectorizedBacktestEngine(
            data_provider=mock_dp, universe_provider=mock_up
        )
        strategy = StrategyDSL(
            name="BadFilter",
            universe=UniverseConfig(type="csi300"),
            start_date="2024-01-01",
            end_date="2024-01-20",
            factors={"mom": "close / shift(close, 5) - 1"},
            signals=[
                {"type": "filter", "condition": "close >> 1"},
                {"type": "filter", "condition": "close > 0"},
            ],
            weights=WeightConfig(method="equal"),
        )
        result = engine.run(strategy)
        assert result.success

    def test_run_signal_expression_failure(self):
        """表达式信号使用了禁止的运算符，求值失败但引擎应容错"""
        symbols = ["000001", "000002", "000003"]
        data = _make_mock_data(symbols)
        mock_dp, mock_up = _make_mock_providers(symbols, data)
        engine = VectorizedBacktestEngine(
            data_provider=mock_dp, universe_provider=mock_up
        )
        strategy = StrategyDSL(
            name="BadExpr",
            universe=UniverseConfig(type="csi300"),
            start_date="2024-01-01",
            end_date="2024-01-20",
            factors={"mom": "close / shift(close, 5) - 1"},
            signals=[
                {"type": "filter", "condition": "close > 0"},
                {"type": "expression", "formula": "close >> 1", "alias": "bad"},
            ],
            weights=WeightConfig(method="equal"),
        )
        result = engine.run(strategy)
        assert result.success

    def test_run_rank_missing_field(self):
        symbols = ["000001", "000002", "000003"]
        data = _make_mock_data(symbols)
        mock_dp, mock_up = _make_mock_providers(symbols, data)
        engine = VectorizedBacktestEngine(
            data_provider=mock_dp, universe_provider=mock_up
        )
        strategy = StrategyDSL(
            name="BadRank",
            universe=UniverseConfig(type="csi300"),
            start_date="2024-01-01",
            end_date="2024-01-20",
            factors={"mom": "close / shift(close, 5) - 1"},
            signals=[
                {"type": "filter", "condition": "close > 0"},
                {"type": "rank", "by": "revenue_yoy", "top": 5},
            ],
            weights=WeightConfig(method="equal"),
        )
        # 引擎容错：字段缺失记录警告但不会导致回测失败
        result = engine.run(strategy)
        assert result.success


class TestRunBacktestConvenience:
    def test_invalid_yaml_returns_dsl_error(self):
        result = run_backtest("not valid yaml: {{{")
        assert not result.success
        assert result.error_category == "dsl_error"

    def test_valid_yaml_runs_engine(self):
        import yaml

        strategy_dict = {
            "name": "Test",
            "universe": {"type": "csi300"},
            "start_date": "2024-01-01",
            "end_date": "2024-01-10",
            "factors": {"mom": "close / shift(close, 1) - 1"},
            "signals": [{"type": "filter", "condition": "mom > 0"}],
            "weights": {"method": "equal_weight"},
            "risk_control": {},
        }
        strategy_yaml = yaml.dump(strategy_dict, allow_unicode=True)

        with patch(
            "long_earn.backtest.engine.core.VectorizedBacktestEngine.run"
        ) as mock_run:
            mock_run.return_value = BacktestResult(
                success=True,
                message="回测成功",
                total_return=0.15,
                annual_return=0.12,
                sharpe_ratio=0.8,
                max_drawdown=-0.1,
                win_rate=0.6,
                trading_days=5,
                volatility=0.05,
                calmar_ratio=1.2,
                sortino_ratio=1.0,
                daily_returns=[],
                positions_history=[],
            )
            result = run_backtest(strategy_yaml)
            assert result.success
            mock_run.assert_called_once()

    def test_empty_yaml_is_handled(self):
        result = run_backtest("")
        assert not result.success
