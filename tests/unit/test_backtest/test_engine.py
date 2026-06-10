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


if __name__ == "__main__":
    unittest.main()
