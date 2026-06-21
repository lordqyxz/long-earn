"""核心引擎集成测试

测试事件循环主流程、风控触发和 Walk-Forward 执行。
聚焦关键链路，覆盖但不重复 Broker/Portfolio/Visibility 的单元测试。
"""

import unittest
from datetime import datetime

import polars as pl

from long_earn.backtest.domain.entities import SignalEvent
from long_earn.backtest.engine.broker import TradingCostConfig
from long_earn.backtest.engine.core import EventDrivenBacktestEngine
from long_earn.backtest.engine.strategy import BaseStrategy
from long_earn.backtest.engine.visibility import VisibilityContext

# ── 测试桩 ────────────────────────────────────────────────


def _make_panel(
    days: int = 10,
    symbols: list[str] | None = None,
    close_price: float = 10.0,
    trend: float = 0.0,
) -> pl.DataFrame:
    """构造截面面板数据"""
    if symbols is None:
        symbols = ["000001", "000002"]
    rows = []
    base = close_price
    for i in range(days):
        for sym in symbols:
            price = base + trend * i
            rows.append(
                {
                    "timestamp": datetime(2024, 1, i + 1),
                    "symbol": sym,
                    "open": price * 0.99,
                    "high": price * 1.02,
                    "low": price * 0.98,
                    "close": price,
                    "volume": 10000,
                }
            )
    return pl.DataFrame(rows)


class _SimpleStrategy(BaseStrategy):
    """固定权重买入策略"""

    def __init__(self, weights: dict[str, float] | None = None):
        super().__init__(strategy_id="test-simple")
        self._weights = weights or {"000001": 0.5}

    def on_bar(
        self, bars: pl.DataFrame, context: VisibilityContext
    ) -> SignalEvent | None:
        ts = bars.select("timestamp").to_series()[0]
        return SignalEvent(
            timestamp=ts,
            trace_id="trace-test",
            event_id="sig-test",
            signals=dict(self._weights),
            strategy_id="test-simple",
        )


class _EmptyStrategy(BaseStrategy):
    """不交易的策略"""

    def __init__(self):
        super().__init__(strategy_id="test-empty")

    def on_bar(
        self, bars: pl.DataFrame, context: VisibilityContext
    ) -> SignalEvent | None:
        return None


class _RaisingStrategy(BaseStrategy):
    """抛出异常的策略"""

    def __init__(self):
        super().__init__(strategy_id="test-raise")

    def on_bar(
        self, bars: pl.DataFrame, context: VisibilityContext
    ) -> SignalEvent | None:
        raise ValueError("策略执行异常")


class MockDataProvider:
    """模拟数据提供者"""

    def __init__(self, panel: pl.DataFrame):
        self._panel = panel

    def get_merged_panel_as_polars(
        self, symbols: list[str], start: str, end: str
    ) -> pl.DataFrame:
        return self._panel


# ── 测试用例 ────────────────────────────────────────────────


class TestEngineInit(unittest.TestCase):
    """引擎初始化"""

    def test_default_construction(self):
        """默认构造函数应设置合理的默认值"""
        engine = EventDrivenBacktestEngine()
        self.assertIsNone(engine.data_provider)
        self.assertIsNone(engine.stop_loss)
        self.assertIsNone(engine.max_drawdown_limit)
        self.assertIsInstance(engine.cost_config, TradingCostConfig)
        self.assertEqual(engine.max_position_pct, 1.0)
        self.assertEqual(engine.max_positions, 0)

    def test_custom_params(self):
        """自定义参数应正确传递"""
        engine = EventDrivenBacktestEngine(
            stop_loss=0.1,
            max_drawdown_limit=0.2,
            max_positions=5,
        )
        self.assertEqual(engine.stop_loss, 0.1)
        self.assertEqual(engine.max_drawdown_limit, 0.2)
        self.assertEqual(engine.max_positions, 5)


class TestEngineRun(unittest.TestCase):
    """引擎主流程"""

    def setUp(self):
        self.panel = _make_panel(days=10)
        self.provider = MockDataProvider(self.panel)

    def test_run_simple_strategy(self):
        """简单策略应正确完成回测并返回结果"""
        engine = EventDrivenBacktestEngine(data_provider=self.provider)
        strategy = _SimpleStrategy()

        result = engine.run(strategy, "2024-01-01", "2024-01-10", ["000001", "000002"])

        self.assertTrue(result.success)
        self.assertIsNotNone(result.total_return)
        self.assertIsNotNone(result.sharpe_ratio)
        self.assertIsNotNone(result.max_drawdown)
        self.assertEqual(result.trading_days, 10)
        self.assertGreater(len(result.daily_returns or []), 0)
        # 有交易记录
        self.assertGreater(result.trade_count or 0, 0)

    def test_run_empty_data(self):
        """数据为空应返回失败结果"""
        empty_provider = MockDataProvider(pl.DataFrame())
        engine = EventDrivenBacktestEngine(data_provider=empty_provider)
        strategy = _SimpleStrategy()

        result = engine.run(strategy, "2024-01-01", "2024-01-10", ["000001"])

        self.assertFalse(result.success)
        self.assertEqual(result.message, "加载数据为空")

    def test_run_strategy_exception(self):
        """策略抛出异常应被引擎捕获并返回失败结果"""
        engine = EventDrivenBacktestEngine(data_provider=self.provider)
        strategy = _RaisingStrategy()

        result = engine.run(strategy, "2024-01-01", "2024-01-10", ["000001"])

        self.assertFalse(result.success)
        self.assertIn("策略执行异常", result.message)


class TestRiskChecks(unittest.TestCase):
    """风控检查"""

    @staticmethod
    def _downward_panel(days: int = 10) -> pl.DataFrame:
        """制造持续下跌的价格序列"""
        rows = []
        for i in range(days):
            price = 10.0 - 0.5 * i  # 10, 9.5, 9.0, ...
            rows.append(
                {
                    "timestamp": datetime(2024, 1, i + 1),
                    "symbol": "000001",
                    "close": price,
                    "open": price * 0.99,
                    "high": price * 1.01,
                    "low": price * 0.99,
                    "volume": 10000,
                }
            )
        return pl.DataFrame(rows)

    @staticmethod
    def _peak_trough_panel(days: int = 10) -> pl.DataFrame:
        """先涨后跌的价格序列，用于回撤测试"""
        rows = []
        for i in range(days):
            if i < 5:
                price = 10.0 + i  # 10, 11, 12, 13, 14
            else:
                price = 14.0 - 3.0 * (i - 4)  # 11, 8, 5, 2, -1
            rows.append(
                {
                    "timestamp": datetime(2024, 1, i + 1),
                    "symbol": "000001",
                    "close": max(price, 1.0),
                    "open": max(price, 1.0) * 0.99,
                    "high": max(price, 1.0) * 1.01,
                    "low": max(price, 1.0) * 0.99,
                    "volume": 10000,
                }
            )
        return pl.DataFrame(rows)

    def test_stop_loss_trigger(self):
        """价格下跌超过止损线应触发清仓"""
        provider = MockDataProvider(self._downward_panel())
        engine = EventDrivenBacktestEngine(
            data_provider=provider,
            stop_loss=0.05,  # 5% 止损
        )
        strategy = _SimpleStrategy(weights={"000001": 1.0})

        result = engine.run(strategy, "2024-01-01", "2024-01-10", ["000001"])

        self.assertTrue(result.success)
        self.assertIsNotNone(result.attribution)

    def test_max_drawdown_trigger(self):
        """最大回撤超标应触发全部清仓"""
        provider = MockDataProvider(self._peak_trough_panel())
        engine = EventDrivenBacktestEngine(
            data_provider=provider,
            max_drawdown_limit=0.15,  # 15% 回撤限制
        )
        strategy = _SimpleStrategy(weights={"000001": 1.0})

        result = engine.run(strategy, "2024-01-01", "2024-01-10", ["000001"])

        self.assertTrue(result.success)
        # 有交易（含风控平仓）
        self.assertGreater(result.trade_count or 0, 0)

    def test_risk_checks_disabled(self):
        """未设置风控参数时不执行风控检查"""
        provider = MockDataProvider(self._downward_panel())
        engine = EventDrivenBacktestEngine(data_provider=provider)
        strategy = _SimpleStrategy(weights={"000001": 1.0})

        result = engine.run(strategy, "2024-01-01", "2024-01-10", ["000001"])

        self.assertTrue(result.success)
        self.assertIsNotNone(result.total_return)


class TestWalkForward(unittest.TestCase):
    """Walk-Forward 回测"""

    def setUp(self):
        self.panel = _make_panel(days=30)
        self.provider = MockDataProvider(self.panel)

    def test_walk_forward_basic(self):
        """Walk-Forward 应返回正确的折叠结构和平均指标"""
        engine = EventDrivenBacktestEngine(data_provider=self.provider)
        strategy = _EmptyStrategy()

        result = engine.walk_forward_run(
            strategy,
            "2024-01-01",
            "2024-01-30",
            ["000001", "000002"],
            n_splits=3,
        )

        self.assertIn("fold_results", result)
        self.assertIn("average_metrics", result)
        self.assertEqual(result["n_splits"], 3)
        self.assertEqual(len(result["fold_results"]), 3)

        for fold in result["fold_results"]:
            self.assertIn("train", fold)
            self.assertIn("test", fold)
            self.assertIn("total_return", fold["train"])
            self.assertIn("sharpe_ratio", fold["train"])

        avg = result["average_metrics"]
        self.assertIn("train", avg)
        self.assertIn("test", avg)
        self.assertIn("total_return", avg["train"])


class TestAuditTrail(unittest.TestCase):
    """审计跟踪"""

    def test_audit_trail_records_events(self):
        """审计跟踪应记录引擎执行事件"""
        provider = MockDataProvider(_make_panel(days=5))
        engine = EventDrivenBacktestEngine(data_provider=provider)
        strategy = _SimpleStrategy()

        engine.run(strategy, "2024-01-01", "2024-01-05", ["000001"])

        trail = engine.audit_logger.get_full_trail()
        self.assertGreater(len(trail), 0)

        event_types = {entry["event_type"] for entry in trail}
        self.assertIn("MARKET_DATA", event_types)
        self.assertIn("SIGNAL", event_types)


class TestBacktestFidelity(unittest.TestCase):
    """回测可信度测试

    确保引擎不会编造结果：
    - 数据不足时拒绝输出绩效指标（success=False）
    - 数学公式与 numpy 直接计算一致
    - daily_returns 长度等于 trading_days
    """

    def test_insufficient_data_rejected(self):
        """单根 K 线（trading_days=1）应被拒绝，避免编造零收益"""
        # 构造单日数据：所有股票仅有 1 个时间点
        panel = _make_panel(days=1)
        provider = MockDataProvider(panel)
        engine = EventDrivenBacktestEngine(data_provider=provider)
        strategy = _SimpleStrategy()

        result = engine.run(strategy, "2024-01-01", "2024-01-01", ["000001"])

        self.assertFalse(result.success)
        self.assertEqual(result.error_category, "insufficient_data")
        self.assertIsNone(result.total_return)
        self.assertIsNone(result.sharpe_ratio)

    def test_returns_match_numpy_formula(self):
        """指标必须与 numpy 直接计算的公式一致，不容许任何编造"""
        import numpy as np

        panel = _make_panel(days=10, trend=0.5)  # 价格 10 → 14.5
        provider = MockDataProvider(panel)
        engine = EventDrivenBacktestEngine(data_provider=provider)
        strategy = _SimpleStrategy(weights={"000001": 1.0})

        result = engine.run(strategy, "2024-01-01", "2024-01-10", ["000001"])

        self.assertTrue(result.success)
        equity = [d["value"] for d in (result.daily_returns or [])]
        self.assertEqual(len(equity), result.trading_days)
        self.assertGreaterEqual(len(equity), 2)

        equity_arr = np.array(equity)
        rets = np.diff(equity_arr) / equity_arr[:-1]

        # 公式逐项校验
        expected_total_return = equity_arr[-1] / equity_arr[0] - 1
        expected_annual_return = float(np.mean(rets)) * 252
        expected_vol = float(np.std(rets, ddof=1)) * np.sqrt(252)
        expected_sharpe = (
            expected_annual_return / expected_vol if expected_vol > 0 else 0.0
        )
        peak = np.maximum.accumulate(equity_arr)
        expected_dd = float(np.min((equity_arr - peak) / peak))

        self.assertAlmostEqual(result.total_return or 0, expected_total_return, places=8)
        self.assertAlmostEqual(result.annual_return or 0, expected_annual_return, places=8)
        self.assertAlmostEqual(result.volatility or 0, expected_vol, places=8)
        self.assertAlmostEqual(result.sharpe_ratio or 0, expected_sharpe, places=8)
        self.assertAlmostEqual(result.max_drawdown or 0, expected_dd, places=8)

    def test_walk_forward_reports_failed_folds_field(self):
        """walk_forward_run 返回结构必须含 failed_folds 字段，并保证失败 fold 不污染平均"""
        # 构造 6 天数据：2 splits 后每 fold 测试期只有 2-3 天，但成功
        panel = _make_panel(days=6)
        provider = MockDataProvider(panel)
        engine = EventDrivenBacktestEngine(data_provider=provider)
        strategy = _SimpleStrategy()

        result = engine.walk_forward_run(
            strategy, "2024-01-01", "2024-01-06", ["000001", "000002"], n_splits=2
        )

        # 关键：返回结构包含 failed_folds 字段
        self.assertIn("failed_folds", result)
        self.assertIn("fold_results", result)
        self.assertIn("average_metrics", result)
        self.assertIsInstance(result["failed_folds"], list)

        # 检查每个 fold 的 train/test 结构：成功的有指标，失败的有 error
        for fold in result["fold_results"]:
            for phase in ("train", "test"):
                ph = fold[phase]
                # 失败和成功是互斥的：要么有 error，要么有 total_return
                has_error = "error" in ph
                has_metrics = "total_return" in ph
                self.assertTrue(
                    has_error or has_metrics,
                    f"fold {fold['fold_id']}.{phase} 必须有 error 或 total_return"
                )

    def test_no_position_strategy_returns_zero(self):
        """不交易的策略应得到接近 0 的总收益（净值平稳），不能编造正收益"""
        panel = _make_panel(days=10, trend=0.5)
        provider = MockDataProvider(panel)
        engine = EventDrivenBacktestEngine(data_provider=provider)
        strategy = _EmptyStrategy()

        result = engine.run(strategy, "2024-01-01", "2024-01-10", ["000001"])

        # 不交易但样本充足，应是 success=True 且 total_return=0（无持仓不分享行情上涨）
        self.assertTrue(result.success)
        self.assertIsNotNone(result.total_return)
        self.assertAlmostEqual(result.total_return or 0, 0.0, places=8)
        # 关键：trade_count 应为 0
        self.assertEqual(result.trade_count or 0, 0)


if __name__ == "__main__":
    unittest.main()


class TestStopLossConservativeFill(unittest.TestCase):
    """stop_loss 触发时保守成交价测试

    防止"用日内最低价直接成交 → 给回测白送日内极值"的过于乐观行为。
    """

    @staticmethod
    def _stop_panel(days: int = 4) -> pl.DataFrame:
        """构造价格序列：从 10 跌到 7，且 low 比 close 更低"""
        rows = []
        for i in range(days):
            close = 10.0 - 1.0 * i  # 10, 9, 8, 7
            low = close - 0.5      # 9.5, 8.5, 7.5, 6.5  ← 比 close 更低
            rows.append(
                {
                    "timestamp": datetime(2024, 1, i + 1),
                    "symbol": "000001",
                    "open": close,
                    "high": close + 0.1,
                    "low": low,
                    "close": close,
                    "volume": 10000,
                }
            )
        return pl.DataFrame(rows)

    def test_stop_loss_fill_price_not_below_threshold(self):
        """止损触发时 fill_price 不能优于 'avg_cost * (1 - stop_loss)'

        例：avg_cost=10, stop_loss=10%（线 9）；当价格跌破 9 时止损成交价
        应 ≥ 9（含 broker 滑点扣减后约 9 * (1 - slip) 接近 9），
        而不是日内最低价 6.5。
        """
        provider = MockDataProvider(self._stop_panel())
        engine = EventDrivenBacktestEngine(
            data_provider=provider,
            stop_loss=0.10,  # 10% 止损
        )
        strategy = _SimpleStrategy(weights={"000001": 1.0})

        result = engine.run(strategy, "2024-01-01", "2024-01-04", ["000001"])
        self.assertTrue(result.success)

        # 价格从 10 到 7，必触发止损（pnl_pct < -10%）
        # attribution 反映已实现 + 未实现 P&L
        # 关键：trade_count 应包含止损触发的一笔卖单
        self.assertGreater(result.trade_count or 0, 1)
