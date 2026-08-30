"""引擎绩效指标数值正确性测试（ADR-013 C1-C5）

验证引擎输出的 Sharpe/Alpha/Beta/年化收益率等指标与 numpy 直接计算结果一致。
"""

from __future__ import annotations

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
        rows.append(
            {
                "timestamp": ts,
                "symbol": "A.SZ",
                "open": close * 0.99,
                "high": close * 1.01,
                "low": close * 0.98,
                "close": close,
                "volume": 100000.0,
            }
        )
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


def _numpy_beta(port_equity: list[float], bm_prices: list[float]) -> float:
    """用 numpy 计算 Beta：Cov(R_p, R_m) / Var(R_m)，ddof=1。"""
    eq_arr = np.array(port_equity, dtype=float)
    bm_arr = np.array(bm_prices, dtype=float)
    port_ret = np.diff(eq_arr) / eq_arr[:-1]
    bm_ret = np.diff(bm_arr) / bm_arr[:-1]
    cov = float(np.cov(port_ret, bm_ret)[0, 1])
    var_bm = float(np.var(bm_ret, ddof=1))
    return cov / var_bm if var_bm > 0 else 0.0


def _numpy_alpha(port_equity: list[float], bm_prices: list[float]) -> float:
    """Jensen's Alpha: α = R_p_annual - β · R_m_annual (R_f=0)。"""
    eq_arr = np.array(port_equity, dtype=float)
    bm_arr = np.array(bm_prices, dtype=float)
    port_ret = np.diff(eq_arr) / eq_arr[:-1]
    bm_ret = np.diff(bm_arr) / bm_arr[:-1]
    beta = _numpy_beta(port_equity, bm_prices)
    port_annual = float(np.mean(port_ret)) * 252
    bm_annual = float(np.mean(bm_ret)) * 252
    return port_annual - beta * bm_annual


def _numpy_information_ratio(port_equity: list[float], bm_prices: list[float]) -> float:
    """信息比率：IR = α / tracking_error。"""
    alpha = _numpy_alpha(port_equity, bm_prices)
    tracking_error = _numpy_tracking_error(port_equity, bm_prices)
    return alpha / tracking_error if tracking_error > 0 else 0.0


def _numpy_tracking_error(port_equity: list[float], bm_prices: list[float]) -> float:
    """跟踪误差：std(excess_returns, ddof=1) * sqrt(252)。"""
    eq_arr = np.array(port_equity, dtype=float)
    bm_arr = np.array(bm_prices, dtype=float)
    port_ret = np.diff(eq_arr) / eq_arr[:-1]
    bm_ret = np.diff(bm_arr) / bm_arr[:-1]
    excess = port_ret - bm_ret
    return float(np.std(excess, ddof=1)) * np.sqrt(252)


def _numpy_benchmark_return(bm_prices: list[float]) -> float:
    """基准收益率：(last / first) - 1。"""
    bm_arr = np.array(bm_prices, dtype=float)
    return float((bm_arr[-1] / bm_arr[0]) - 1) if bm_arr[0] > 0 else 0.0


def _panel_with_benchmark(
    port_days: int = 30,
    port_growth: float = 1.005,
    bm_growth: float = 1.003,
) -> pl.DataFrame:
    """构造含策略标的和基准标的的面板。

    port_days: 策略天数（日频）
    port_growth: 策略标的日收益率（如 1.005 = 每日涨 0.5%）
    bm_growth: 基准标的日收益率
    """
    rows = []
    base = datetime(2024, 1, 1)
    for i in range(port_days):
        ts = base + timedelta(days=i)
        close_a = round(100.0 * (port_growth**i), 4)
        close_bm = round(2000.0 * (bm_growth**i), 4)
        rows.append(
            {
                "timestamp": ts,
                "symbol": "A.SZ",
                "open": close_a * 0.99,
                "high": close_a * 1.01,
                "low": close_a * 0.98,
                "close": close_a,
                "volume": 100000.0,
            }
        )
        rows.append(
            {
                "timestamp": ts,
                "symbol": "000300.SH",
                "open": close_bm * 0.99,
                "high": close_bm * 1.01,
                "low": close_bm * 0.98,
                "close": close_bm,
                "volume": 1000000.0,
            }
        )
    return pl.DataFrame(rows)


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


def test_volume_limit_marks_metrics_unreliable(mock_data_provider):
    """C5：成交量限制导致部分成交时 metrics_unreliable=True。

    极低成交量参与率（0.0001）+ 放大成交量面板（1e6 股/日）→ 参与率
    限额 100 股（恰 1 手），首笔买入订单被截断为整手部分成交
    （partial_fill=True），_total_partial_fills/_total_orders > 0.5
    触发 metrics_unreliable。
    _SimpleBuyStrategy 仅首 bar 发一次信号，故仅 1 笔订单。
    （原命名 test_filter_all_rejected_marks_metrics_unreliable 误导，已修正。）
    """
    panel = _simple_panel(days=20).with_columns(pl.lit(1_000_000.0).alias("volume"))
    provider = mock_data_provider(panel)

    # 极低成交量参与率 + 大资金 = 几乎所有订单部分成交
    from long_earn.backtest.engine.broker import TradingCostConfig

    cost = TradingCostConfig(max_volume_participation=0.0001)
    engine = EventDrivenBacktestEngine(data_provider=provider, cost_config=cost)
    result = engine.run(_SimpleBuyStrategy(), "2024-01-01", "2024-01-22", ["A.SZ"])
    assert result.success
    assert result.metrics_unreliable, (
        "成交量限制导致大量部分成交时应标记 metrics_unreliable=True"
    )


def test_volume_below_lot_produces_no_fill(mock_data_provider):
    """P0-04 收口：参与率限额不足 1 手时无成交（碎股取整）。

    面板 volume=1e5 × participation=0.0001 → 限额 10 股 < 100 股一手。
    旧行为会产生 10 股的碎股部分成交（并触发 metrics_unreliable）；
    整手取整后买入数量向下取整到 0 → 无成交，trade_count=0。
    """
    panel = _simple_panel(days=20)  # volume=100000
    provider = mock_data_provider(panel)

    from long_earn.backtest.engine.broker import TradingCostConfig

    cost = TradingCostConfig(max_volume_participation=0.0001)
    engine = EventDrivenBacktestEngine(data_provider=provider, cost_config=cost)
    result = engine.run(_SimpleBuyStrategy(), "2024-01-01", "2024-01-22", ["A.SZ"])
    assert result.success
    assert result.trade_count == 0, "参与率限额不足 1 手时不应产生任何成交"


def test_high_skip_ratio_marks_metrics_unreliable(mock_data_provider):
    """C5：订单大量被跳过（skip_ratio > 0.5）时 metrics_unreliable=True。

    构造场景：连续涨停日，买入订单被涨跌停板拒单（ORDER_SKIPPED），
    使 _total_skipped / _total_orders > 0.5。
    """
    # 构造连续涨停面板：从第 2 日开始每日 close 涨 15%（> 10% 涨停）
    rows = []
    base = datetime(2024, 1, 1)
    for i in range(20):
        ts = base + timedelta(days=i)
        close = 10.0 if i == 0 else round(10.0 * (1.15**i), 2)  # 每日涨停
        rows.append(
            {
                "timestamp": ts,
                "symbol": "A.SZ",
                "open": close,  # open = close，确保 open 也 >= 涨停价
                "high": close * 1.01,
                "low": close * 0.98,
                "close": close,
                "volume": 100000.0,
            }
        )
    panel = pl.DataFrame(rows)
    provider = mock_data_provider(panel)
    engine = EventDrivenBacktestEngine(data_provider=provider)

    # 策略：每日都发出买入信号（待 T+1 执行）
    class _AlwaysBuy:
        def __init__(self):
            self._state: dict = {}
            self.strategy_id = "skip_ratio_test"

        def init(self):
            self._state = {}

        def on_bar(self, bars, context=None):
            from long_earn.backtest.domain.entities import SignalEvent

            return SignalEvent(
                timestamp=bars["timestamp"][0],
                trace_id="trace-skip",
                event_id="sig-skip",
                signals={"A.SZ": 1.0},
                strategy_id="skip_ratio_test",
            )

    result = engine.run(_AlwaysBuy(), "2024-01-01", "2024-01-22", ["A.SZ"])
    assert result.success
    # 连续涨停，绝大多数买入订单应被拒，skip_ratio > 0.5
    assert result.metrics_unreliable, (
        "连续涨停导致大量订单被跳过时应标记 metrics_unreliable=True，"
        f"实际 metrics_unreliable={result.metrics_unreliable}，"
        f"trade_count={result.trade_count}"
    )
    # P2-B 加强：验证跳过原因确实是涨跌停，且跳过比例较高
    from long_earn.backtest.engine.audit import OrderSkipReason

    trail = engine.audit_logger.get_full_trail()
    skipped = [e for e in trail if e.get("event_type") == "ORDER_SKIPPED"]
    limit_skips = [
        e
        for e in skipped
        if e.get("payload", {}).get("reason") == OrderSkipReason.LIMIT_UP_REJECT
    ]
    assert len(limit_skips) > 0, "应存在涨跌停拒单的 ORDER_SKIPPED 事件"
    orders = [e for e in trail if e.get("event_type") == "ORDER"]
    total = len(orders) + len(skipped)
    assert total > 0, "应有订单（含跳过）"
    # skip_ratio 应较高（连续涨停几乎全部拒单），>= 0.5
    assert len(skipped) / total >= 0.5, (
        f"skip_ratio 应 >= 0.5，实际 {len(skipped)}/{total}"
    )


# ── C2: Alpha / Beta / IR 与 numpy 对齐 ──────────────────────────


def test_alpha_beta_ir_matches_numpy(mock_data_provider):
    """C2：引擎输出的 Alpha/Beta/IR 应与 numpy 直接计算一致。

    构造含策略标的和基准标的面板，策略买入持有策略标的，
    引擎以基准标的计算 Alpha/Beta/IR。
    """
    panel = _panel_with_benchmark(port_days=30, port_growth=1.005, bm_growth=1.003)
    provider = mock_data_provider(panel)
    engine = EventDrivenBacktestEngine(data_provider=provider)
    result = engine.run(
        _SimpleBuyStrategy(),
        "2024-01-01",
        "2024-01-31",
        ["A.SZ"],
        benchmark_symbol="000300.SH",
        full_data=panel,
    )
    assert result.success

    # 提取权益曲线和基准价格
    daily_values = [d["value"] for d in (result.daily_returns or [])]
    bm_prices = (
        panel.filter(pl.col("symbol") == "000300.SH")
        .sort("timestamp")["close"]
        .to_list()
    )

    numpy_beta = _numpy_beta(daily_values, bm_prices)
    numpy_alpha = _numpy_alpha(daily_values, bm_prices)
    numpy_ir = _numpy_information_ratio(daily_values, bm_prices)
    numpy_te = _numpy_tracking_error(daily_values, bm_prices)
    numpy_bm_ret = _numpy_benchmark_return(bm_prices)

    assert result.beta is not None
    assert result.alpha is not None
    assert result.information_ratio is not None
    assert result.tracking_error is not None
    assert result.benchmark_return is not None

    assert result.beta == pytest.approx(numpy_beta, rel=1e-4, abs=1e-6), (
        f"引擎 Beta={result.beta:.6f} != numpy={numpy_beta:.6f}"
    )
    assert result.alpha == pytest.approx(numpy_alpha, rel=1e-4, abs=1e-6), (
        f"引擎 Alpha={result.alpha:.6f} != numpy={numpy_alpha:.6f}"
    )
    assert result.information_ratio == pytest.approx(numpy_ir, rel=1e-4, abs=1e-6), (
        f"引擎 IR={result.information_ratio:.6f} != numpy={numpy_ir:.6f}"
    )
    assert result.tracking_error == pytest.approx(numpy_te, rel=1e-4, abs=1e-6), (
        f"引擎 TE={result.tracking_error:.6f} != numpy={numpy_te:.6f}"
    )
    assert result.benchmark_return == pytest.approx(numpy_bm_ret, rel=1e-4, abs=1e-6), (
        f"引擎 BM_ret={result.benchmark_return:.6f} != numpy={numpy_bm_ret:.6f}"
    )


def test_no_benchmark_symbol_returns_zero_metrics(mock_data_provider):
    """C2b：未提供 benchmark_symbol 时 Alpha/Beta/IR 应为零值。"""
    panel = _panel_with_benchmark(port_days=30)
    provider = mock_data_provider(panel)
    engine = EventDrivenBacktestEngine(data_provider=provider)
    result = engine.run(
        _SimpleBuyStrategy(),
        "2024-01-01",
        "2024-01-31",
        ["A.SZ"],
        full_data=panel,
    )
    assert result.success
    assert result.alpha == 0.0
    assert result.beta == 0.0
    assert result.information_ratio == 0.0
    assert result.tracking_error == 0.0
    assert result.benchmark_return == 0.0


def test_benchmark_not_in_data_returns_zero_metrics(mock_data_provider):
    """C2c：基准标的不在数据中时 Alpha/Beta/IR 应为零值。"""
    panel = _panel_with_benchmark(port_days=30)
    provider = mock_data_provider(panel)
    engine = EventDrivenBacktestEngine(data_provider=provider)
    result = engine.run(
        _SimpleBuyStrategy(),
        "2024-01-01",
        "2024-01-31",
        ["A.SZ"],
        benchmark_symbol="NONEXISTENT",
        full_data=panel,
    )
    assert result.success
    assert result.alpha == 0.0
    assert result.beta == 0.0
    assert result.information_ratio == 0.0
    assert result.tracking_error == 0.0
    assert result.benchmark_return == 0.0


def test_benchmark_insufficient_data_returns_zero_metrics(mock_data_provider):
    """C2d：基准数据不足 MIN_BM_POINTS 时 Alpha/Beta/IR 应为零值。"""
    # 仅 1 天基准数据（低于 MIN_BM_POINTS=2）
    rows = []
    base = datetime(2024, 1, 1)
    for i in range(20):
        ts = base + timedelta(days=i)
        close_a = round(100.0 * (1.005**i), 4)
        rows.append(
            {
                "timestamp": ts,
                "symbol": "A.SZ",
                "open": close_a * 0.99,
                "high": close_a * 1.01,
                "low": close_a * 0.98,
                "close": close_a,
                "volume": 100000.0,
            }
        )
    # 仅 1 天基准数据
    rows.append(
        {
            "timestamp": base,
            "symbol": "000300.SH",
            "open": 2000.0,
            "high": 2020.0,
            "low": 1980.0,
            "close": 2000.0,
            "volume": 1000000.0,
        }
    )
    panel = pl.DataFrame(rows)
    provider = mock_data_provider(panel)
    engine = EventDrivenBacktestEngine(data_provider=provider)
    result = engine.run(
        _SimpleBuyStrategy(),
        "2024-01-01",
        "2024-01-21",
        ["A.SZ"],
        benchmark_symbol="000300.SH",
        full_data=panel,
    )
    assert result.success
    assert result.alpha == 0.0
    assert result.beta == 0.0
    assert result.information_ratio == 0.0
    assert result.tracking_error == 0.0
    assert result.benchmark_return == 0.0
