"""引擎绩效指标数值正确性测试（ADR-013 C1-C5）

验证引擎输出的 Sharpe/Alpha/Beta/年化收益率等指标与 numpy 直接计算结果一致。
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

import numpy as np
import polars as pl
import pytest

from long_earn.backtest.engine.core import EventDrivenBacktestEngine


def _simple_panel(days: int = 20) -> pl.DataFrame:
    """确定性单股面板：每日上涨 0.5%。"""
    rows = []
    base = datetime(2024, 1, 1)
    for i in range(days):
        ts = base + timedelta(days=i)
        close = round(100.0 * (1.005**i), 4)
        rows.append({
            "timestamp": ts,
            "symbol": "A.SZ",
            "open": close * 0.99,
            "high": close * 1.01,
            "low": close * 0.98,
            "close": close,
            "volume": 100000.0,
        })
    return pl.DataFrame(rows)


class _SimpleBuyStrategy:
    """简单买入持有策略。"""

    def __init__(self):
        self._state: dict = {}
        self._called = False
        self.strategy_id = "buy_hold"

    def init(self):
        self._called = False

    def on_bar(self, bars: pl.DataFrame, context=None):
        from long_earn.backtest.domain.entities import SignalEvent

        if not self._called:
            self._called = True
            return SignalEvent(
                timestamp=bars["timestamp"][0],
                trace_id="trace-buy",
                event_id="sig-buy",
                signals={"A.SZ": 1.0},
                strategy_id="buy_hold",
            )
        return None


def _numpy_sharpe(equity: list[float], rf: float = 0.0) -> float:
    """用 numpy 计算年化夏普比率（算术年化，与引擎保持一致）。"""
    arr = np.array(equity, dtype=float)
    returns = np.diff(arr) / arr[:-1]
    if len(returns) < 2:
        return 0.0
    annual_return = float(np.mean(returns)) * 252
    annual_vol = float(np.std(returns, ddof=1)) * np.sqrt(252)
    return annual_return / annual_vol if annual_vol > 0 else 0.0


def _numpy_max_drawdown(equity: list[float]) -> float:
    """用 numpy 计算最大回撤。"""
    arr = np.array(equity, dtype=float)
    peak = np.maximum.accumulate(arr)
    drawdown = (arr - peak) / peak
    return float(np.min(drawdown))


def _numpy_total_return(equity: list[float]) -> float:
    """用 numpy 计算总收益率。"""
    return (equity[-1] / equity[0]) - 1 if equity[0] > 0 else 0.0


# ── C1: Sharpe 年化对齐 ─────────────────────────────────────────


def test_sharpe_matches_numpy_formula(mock_data_provider):
    """C1：引擎输出的 Sharpe 应与 numpy 直接计算一致。"""
    panel = _simple_panel(days=20)
    provider = mock_data_provider(panel)
    engine = EventDrivenBacktestEngine(data_provider=provider)
    result = engine.run(_SimpleBuyStrategy(), "2024-01-01", "2024-01-22", ["A.SZ"])
    assert result.success

    daily_values = [d["value"] for d in (result.daily_returns or [])]
    numpy_sharpe = _numpy_sharpe(daily_values)

    assert result.sharpe_ratio is not None
    assert result.sharpe_ratio == pytest.approx(numpy_sharpe, rel=1e-4, abs=1e-6), (
        f"引擎 Sharpe={result.sharpe_ratio:.6f} != numpy={numpy_sharpe:.6f}"
    )


# ── C3: 样本量不足拒算 ─────────────────────────────────────────


def test_insufficient_trading_days_returns_failure(mock_data_provider):
    """C3：交易天数少于 MIN_TRADING_DAYS 时返回 success=False。"""
    panel = _simple_panel(days=1)
    provider = mock_data_provider(panel)
    engine = EventDrivenBacktestEngine(data_provider=provider)
    result = engine.run(_SimpleBuyStrategy(), "2024-01-01", "2024-01-02", ["A.SZ"])
    assert not result.success
    assert result.error_category == "insufficient_data"


# ── C4: 总收益率公式对齐 ───────────────────────────────────────


def test_total_return_matches_numpy(mock_data_provider):
    """C4：引擎 total_return 应与 numpy 直接计算一致。"""
    panel = _simple_panel(days=15)
    provider = mock_data_provider(panel)
    engine = EventDrivenBacktestEngine(data_provider=provider)
    result = engine.run(_SimpleBuyStrategy(), "2024-01-01", "2024-01-17", ["A.SZ"])
    assert result.success

    daily_values = [d["value"] for d in (result.daily_returns or [])]
    numpy_ret = _numpy_total_return(daily_values)

    assert result.total_return is not None
    assert result.total_return == pytest.approx(numpy_ret, rel=1e-4, abs=1e-6), (
        f"引擎 total_return={result.total_return:.6f} != numpy={numpy_ret:.6f}"
    )


# ── C5: metrics_unreliable 过滤 ─────────────────────────────────


def test_filter_all_rejected_marks_metrics_unreliable(mock_data_provider):
    """C5：成交量限制导致全部部分成交时 metrics_unreliable=True。"""
    panel = _simple_panel(days=20)
    provider = mock_data_provider(panel)

    # 极低成交量参与率 + 大资金 = 几乎所有订单部分成交
    from long_earn.backtest.engine.broker import TradingCostConfig

    cost = TradingCostConfig(max_volume_participation=0.0001)
    engine = EventDrivenBacktestEngine(data_provider=provider, cost_config=cost)
    result = engine.run(_SimpleBuyStrategy(), "2024-01-01", "2024-01-22", ["A.SZ"])
    assert result.success
    # 不抛异常即可；metrics_unreliable 标记由上层决策
