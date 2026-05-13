"""向量化回测引擎核心测试"""

from unittest.mock import MagicMock

import numpy as np
import pandas as pd

from long_earn.backtest.engine.core import VectorizedBacktestEngine, run_backtest
from long_earn.backtest.engine.dsl import (
    RiskControlConfig,
    StrategyDSL,
    UniverseConfig,
    WeightConfig,
)


def _check_list(lst: list | None) -> list:
    """类型收窄辅助函数"""
    return lst if lst is not None else []


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
    _ = symbols
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
        weights=WeightConfig(method="equal_weight"),
        risk_control=RiskControlConfig(max_position_per_stock=0.3),
    )


def _make_mock_providers(symbols: list[str], data: pd.DataFrame):
    mock_dp = MagicMock()
    mock_dp.get_merged_panel.return_value = data
    mock_up = MagicMock()
    mock_up.get_symbols.return_value = symbols
    return mock_dp, mock_up


class TestVectorizedBacktestEngine:
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
        assert len(_check_list(result.daily_returns)) > 0
        assert len(_check_list(result.positions_history)) > 0

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


class TestRunBacktestConvenience:
    def test_invalid_yaml_returns_dsl_error(self):
        result = run_backtest("not valid yaml: {{{")
        assert not result.success
        assert result.error_category == "dsl_error"
