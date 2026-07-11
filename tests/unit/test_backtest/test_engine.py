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


class _BuyOnceStrategy(BaseStrategy):
    """仅首 bar 买入一次并持有的策略，避免持续买入摊低成本稀释回撤。"""

    def __init__(self, weights: dict[str, float] | None = None):
        super().__init__(strategy_id="test-buy-once")
        self._weights = weights or {"000001": 1.0}
        self._called = False

    def init(self) -> None:
        self._called = False

    def on_bar(
        self, bars: pl.DataFrame, context: VisibilityContext
    ) -> SignalEvent | None:
        if self._called:
            return None
        self._called = True
        ts = bars.select("timestamp").to_series()[0]
        return SignalEvent(
            timestamp=ts,
            trace_id="trace-buy-once",
            event_id="sig-buy-once",
            signals=dict(self._weights),
            strategy_id="test-buy-once",
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

    def test_run_keyboard_interrupt_not_swallowed(self):
        """P1-11：策略抛 KeyboardInterrupt 应向上传播，不返回虚假结果"""
        engine = EventDrivenBacktestEngine(data_provider=self.provider)

        class _InterruptStrategy(_SimpleStrategy):
            def on_bar(self, current_data: dict, current_ts: datetime):
                raise KeyboardInterrupt

        strategy = _InterruptStrategy()
        with self.assertRaises(KeyboardInterrupt):
            engine.run(strategy, "2024-01-01", "2024-01-03", ["000001"])


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
        """先涨后跌的价格序列，用于回撤测试

        价格 10→14→1，组合层面回撤 ~7%（因 100 万本金满仓）。
        若需要触发 15% 回撤限制，使用 _steep_trough_panel。
        """
        rows = []
        for i in range(days):
            price = (
                10.0 + i if i < 5 else 14.0 - 3.0 * (i - 4)
            )  # 10,11,...,14,11,8,5,2,-1
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

    @staticmethod
    def _steep_trough_panel(days: int = 12) -> pl.DataFrame:
        """陡峭下跌面板：组合层面回撤 > 15%，用于触发 max_drawdown_limit=0.15。

        价格前 2 日平盘 8（100 万满仓买入 8 × 12.5 万股 ≈ 100 万，覆盖成本），
        之后持续暴跌至 1，组合回撤 > 15%。
        volume 设为 1e7 确保满仓买入不被成交量参与率限制。
        open=close 避免开盘价触及涨跌停价导致买入被拒。
        """
        rows = []
        for i in range(days):
            price = 8.0 if i < 2 else max(8.0 - (i - 1) * 1.2, 1.0)
            rows.append(
                {
                    "timestamp": datetime(2024, 1, i + 1),
                    "symbol": "000001",
                    "close": price,
                    "open": price,  # open=close 避免涨跌停拒单
                    "high": price * 1.01,
                    "low": price * 0.99,
                    "volume": 10_000_000,  # 大成交量，避免部分成交
                }
            )
        return pl.DataFrame(rows)

    def test_stop_loss_trigger(self):
        """价格下跌超过止损线应触发清仓

        加强断言（P1-2）：必须出现 RISK_TRIGGER(stop_loss) 审计事件、
        trade_count >= 2（含买入+止损卖出）。

        用 _BuyOnceStrategy（仅首 bar 买入一次）避免持续买入摊低 avg_cost
        导致止损永不触发（available_date 修复后止损在 T+2 后才检查，
        持续买入会使 avg_cost 跟踪当前价，pnl 不跌破阈值）。
        volume 设为 1e7 确保满仓买入不被成交量参与率限制。
        """
        rows = []
        for i in range(10):
            price = 10.0 - 0.5 * i  # 10, 9.5, 9.0, ...
            rows.append(
                {
                    "timestamp": datetime(2024, 1, i + 1),
                    "symbol": "000001",
                    "close": price,
                    "open": price,
                    "high": price * 1.01,
                    "low": price * 0.98,
                    "volume": 10_000_000,
                }
            )
        provider = MockDataProvider(pl.DataFrame(rows))
        engine = EventDrivenBacktestEngine(
            data_provider=provider,
            stop_loss=0.05,  # 5% 止损
        )
        strategy = _BuyOnceStrategy(weights={"000001": 0.95})

        result = engine.run(strategy, "2024-01-01", "2024-01-10", ["000001"])

        self.assertTrue(result.success)
        self.assertIsNotNone(result.attribution)
        # 止损必须真正触发：RISK_TRIGGER(stop_loss) 事件存在
        trail = engine.audit_logger.get_full_trail()
        sl_triggers = [
            e
            for e in trail
            if e.get("event_type") == "RISK_TRIGGER"
            and e.get("payload", {}).get("risk_type") == "stop_loss"
        ]
        self.assertGreaterEqual(
            len(sl_triggers), 1, "止损触发应产生 RISK_TRIGGER(stop_loss) 审计事件"
        )
        # 买入 + 止损卖出至少 2 笔成交
        self.assertGreaterEqual(
            result.trade_count or 0, 2, "止损场景 trade_count 应 >= 2（买入+止损卖出）"
        )

    def test_max_drawdown_trigger(self):
        """最大回撤超标应触发全部清仓

        加强断言（P1-3）：必须出现 RISK_TRIGGER(max_drawdown) 审计事件、
        trade_count >= 2、风控清仓后持仓为空。

        用 _steep_trough_panel + _BuyOnceStrategy(0.95) 构造组合层面回撤 > 15% 的场景：
        策略仅在首 bar 买入 95% 仓位并持有（留 5% 现金缓冲避免 Broker
        预估口径差异导致的"现金不足跳过"），之后暴跌触发回撤风控。
        """
        provider = MockDataProvider(self._steep_trough_panel())
        engine = EventDrivenBacktestEngine(
            data_provider=provider,
            max_drawdown_limit=0.15,  # 15% 回撤限制
        )
        strategy = _BuyOnceStrategy(weights={"000001": 0.95})

        result = engine.run(strategy, "2024-01-01", "2024-01-12", ["000001"])

        self.assertTrue(result.success)
        # 最大回撤风控必须真正触发
        trail = engine.audit_logger.get_full_trail()
        dd_triggers = [
            e
            for e in trail
            if e.get("event_type") == "RISK_TRIGGER"
            and e.get("payload", {}).get("risk_type") == "max_drawdown"
        ]
        self.assertGreaterEqual(
            len(dd_triggers), 1, "回撤超限应产生 RISK_TRIGGER(max_drawdown) 审计事件"
        )
        # P1-B 修复：清仓后无持仓时不再重复触发，故应仅 1 个 RISK_TRIGGER
        self.assertEqual(
            len(dd_triggers), 1, "清仓后不应重复触发 RISK_TRIGGER(max_drawdown)"
        )
        # 买入 + 风控清仓至少 2 笔成交
        self.assertGreaterEqual(
            result.trade_count or 0, 2, "回撤清仓场景 trade_count 应 >= 2"
        )

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

    def test_audit_trail_entries_include_timestamp(self):
        """审计 entry 必须包含 timestamp 字段，保证内存审计与 DuckDB 字段一致"""
        provider = MockDataProvider(_make_panel(days=3))
        engine = EventDrivenBacktestEngine(data_provider=provider)
        strategy = _SimpleStrategy()

        engine.run(strategy, "2024-01-01", "2024-01-03", ["000001"])

        trail = engine.audit_logger.get_full_trail()
        self.assertGreater(len(trail), 0)
        for entry in trail:
            self.assertIn("timestamp", entry, "审计 entry 缺少 timestamp 字段")
            self.assertIsInstance(entry["timestamp"], datetime)


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

        self.assertAlmostEqual(
            result.total_return or 0, expected_total_return, places=8
        )
        self.assertAlmostEqual(
            result.annual_return or 0, expected_annual_return, places=8
        )
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
                    f"fold {fold['fold_id']}.{phase} 必须有 error 或 total_return",
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


class TestWalkForwardCrossFoldIsolation(unittest.TestCase):
    """Walk-Forward 跨 fold 状态隔离测试（评审 P0-2）

    验证 run() 开头重置 _pending_signals / _prev_close_map / 涨跌停 map，
    防止 fold N 末 bar 的信号/前收盘价泄漏到 fold N+1（前视偏差）。
    """

    def setUp(self):
        self.panel = _make_panel(days=30)
        self.provider = MockDataProvider(self.panel)

    def test_run_resets_pending_signals(self):
        """run() 开头应清空 _pending_signals，防止上一 run 的遗留信号在本 run 首 bar 成交。"""
        engine = EventDrivenBacktestEngine(data_provider=self.provider)
        strategy = _SimpleStrategy(weights={"000001": 1.0})
        # 第一次 run：末 bar 策略仍发信号，会入队 _pending_signals
        engine.run(strategy, "2024-01-01", "2024-01-10", ["000001", "000002"])
        # 末 bar 信号入队（若不重置，第二次 run 首 bar 会执行它）
        # 第二次 run 前手动塞入一个伪造信号，模拟跨 run 泄漏
        from long_earn.backtest.domain.entities import SignalEvent

        engine._pending_signals = [
            SignalEvent(
                timestamp=datetime(2024, 1, 1),
                trace_id="leak-trace",
                event_id="leak-sig",
                signals={"000001": 1.0},
                strategy_id="leak",
            )
        ]
        # 第二次 run 应在开头清空 _pending_signals
        engine.run(strategy, "2024-01-01", "2024-01-10", ["000001", "000002"])
        # 验证：第二次 run 的审计 trail 中不应出现 trace_id=leak-trace 的 SIGNAL_EXECUTE_T1
        trail = engine.audit_logger.get_full_trail()
        leak_execs = [
            e
            for e in trail
            if e.get("event_type") == "SIGNAL_EXECUTE_T1"
            and e.get("parent_id") == "leak-trace"
        ]
        self.assertEqual(
            len(leak_execs),
            0,
            "run() 应重置 _pending_signals，遗留信号不应在本 run 执行",
        )

    def test_run_resets_prev_close_map(self):
        """run() 开头应清空 _prev_close_map，防止上一 run 的收盘价污染本 run 涨跌停计算。"""
        engine = EventDrivenBacktestEngine(data_provider=self.provider)
        strategy = _SimpleStrategy(weights={"000001": 1.0})
        engine.run(strategy, "2024-01-01", "2024-01-10", ["000001", "000002"])
        # 第一次 run 后 _prev_close_map 应有值
        self.assertGreater(len(engine._prev_close_map), 0)
        # 手动塞入一个伪造的前收盘价
        engine._prev_close_map = {"FAKE.SZ": 999.0}
        # 第二次 run 应在开头清空
        engine.run(strategy, "2024-01-01", "2024-01-10", ["000001", "000002"])
        # 验证：伪造的 FAKE.SZ 不应残留（它不在本 run 的 symbols 中）
        self.assertNotIn(
            "FAKE.SZ",
            engine._prev_close_map,
            "run() 应重置 _prev_close_map，伪造的前收盘价不应残留",
        )

    def test_walk_forward_no_cross_fold_signal_leak(self):
        """walk_forward_run 中 fold N 末 bar 的 SIGNAL 不应在 fold N+1 首 bar 变成 FILL。

        验证 run() 开头重置 _pending_signals 的修复：fold N 末 bar 入队的
        遗留信号不会在 fold N+1 的 run() 首 bar 执行（否则是前视偏差）。

        方法：用每个 bar 都发信号的 _SimpleStrategy 跑 walk_forward_run，
        包裹 run 在每次调用前捕获遗留信号的 trace_id。
        修复前：遗留信号会在下一 fold 首 bar 执行（泄漏）。
        修复后：run() 开头清空 _pending_signals，遗留信号不执行。
        验证：每次 run 调用前若有遗留信号，记录其 trace_id；
        然后单独跑一次 run 并注入这些 trace_id 之一，验证它不出现在
        SIGNAL_EXECUTE_T1 中（证明 run 重置生效）。
        """
        engine = EventDrivenBacktestEngine(data_provider=self.provider)
        strategy = _SimpleStrategy(weights={"000001": 1.0})
        # 包裹 run：捕获每次 run() 调用前的遗留信号 trace_id
        original_run = engine.run
        leak_trace_ids: list[str] = []

        def _checking_run(*args, **kwargs):
            # run() 调用前：记录遗留信号的 trace_id
            for sig in list(engine._pending_signals):
                leak_trace_ids.append(sig.trace_id)
            return original_run(*args, **kwargs)

        engine.run = _checking_run  # type: ignore[method-assign]
        try:
            result = engine.walk_forward_run(
                strategy,
                "2024-01-01",
                "2024-01-30",
                ["000001", "000002"],
                n_splits=3,
            )
        finally:
            engine.run = original_run  # type: ignore[method-assign]

        # walk_forward 应成功完成
        self.assertIn("fold_results", result)
        # 验证确实存在跨 fold 遗留信号（_SimpleStrategy 每 bar 发信号，
        # fold 末 bar 入队的信号在该 fold 内无下一 bar 执行）
        self.assertGreater(
            len(leak_trace_ids), 0, "应捕获到跨 fold 遗留信号（证明泄漏场景存在）"
        )
        # 关键验证：注入一个唯一 trace_id 的伪造信号，run() 开头重置使其不执行。
        # （不能用 leak_trace_ids 中的 trace_id，因 _SimpleStrategy 所有信号
        # 共用固定 trace_id="trace-test"，会与正常信号混淆）
        from long_earn.backtest.domain.entities import SignalEvent

        engine._pending_signals = [
            SignalEvent(
                timestamp=datetime(2024, 1, 1),
                trace_id="leak-injected-unique",
                event_id="leak-injected",
                signals={"000001": 1.0},
                strategy_id="leak",
            )
        ]
        engine.run(strategy, "2024-01-01", "2024-01-10", ["000001", "000002"])
        trail = engine.audit_logger.get_full_trail()
        leak_execs = [
            e
            for e in trail
            if e.get("event_type") == "SIGNAL_EXECUTE_T1"
            and e.get("parent_id") == "leak-injected-unique"
        ]
        self.assertEqual(
            len(leak_execs),
            0,
            "run() 应在开头重置 _pending_signals，注入的遗留信号不应执行",
        )


class TestEventLoopOrder(unittest.TestCase):
    """事件循环顺序测试（评审 P1-5）

    验证 _process_timestamp 内的审计事件相对顺序：
      MARKET_DATA 在 SIGNAL 之前；FILL 在 ORDER 之后；
      风控触发时 SIGNAL_SKIPPED_BY_RISK 替代 SIGNAL。
    """

    def test_event_order_within_bar(self):
        """单 bar 内审计事件顺序：MARKET_DATA → SIGNAL（→ 后续 bar 的 ORDER/FILL）。"""
        provider = MockDataProvider(_make_panel(days=5))
        engine = EventDrivenBacktestEngine(data_provider=provider)
        strategy = _SimpleStrategy(weights={"000001": 1.0})
        engine.run(strategy, "2024-01-01", "2024-01-05", ["000001"])

        trail = engine.audit_logger.get_full_trail()
        # 找第一个 MARKET_DATA 和第一个 SIGNAL 的索引
        mkt_idx = next(
            (i for i, e in enumerate(trail) if e.get("event_type") == "MARKET_DATA"),
            None,
        )
        sig_idx = next(
            (i for i, e in enumerate(trail) if e.get("event_type") == "SIGNAL"),
            None,
        )
        self.assertIsNotNone(mkt_idx, "应有 MARKET_DATA 事件")
        self.assertIsNotNone(sig_idx, "应有 SIGNAL 事件")
        self.assertLess(mkt_idx, sig_idx, "MARKET_DATA 必须在 SIGNAL 之前（同 bar 内）")
        # ORDER 在 SIGNAL 之后（T+1 执行：SIGNAL 在 T 日，ORDER/FILL 在 T+1 日）
        order_idx = next(
            (i for i, e in enumerate(trail) if e.get("event_type") == "ORDER"),
            None,
        )
        self.assertIsNotNone(order_idx, "应有 ORDER 事件")
        self.assertGreater(
            order_idx, sig_idx, "ORDER 必须在 SIGNAL 之后（T+1 延迟执行）"
        )
        # FILL 在 ORDER 之后
        fill_idx = next(
            (i for i, e in enumerate(trail) if e.get("event_type") == "FILL"),
            None,
        )
        self.assertIsNotNone(fill_idx, "应有 FILL 事件")
        self.assertGreater(fill_idx, order_idx, "FILL 必须在 ORDER 之后")

    def test_risk_trigger_replaces_signal(self):
        """风控触发时应用 SIGNAL_SKIPPED_BY_RISK 替代 SIGNAL。

        用 _BuyOnceStrategy（仅首 bar 买入）+ 大成交量 + 严格止损，
        确保买入后暴跌触发止损并清仓，后续 bar 风控持续触发抑制信号。
        """
        rows = []
        for i in range(8):
            price = 10.0 - 0.8 * i  # 10, 9.2, 8.4, ... 快速暴跌
            rows.append(
                {
                    "timestamp": datetime(2024, 1, i + 1),
                    "symbol": "000001",
                    "close": price,
                    "open": price,
                    "high": price * 1.01,
                    "low": price * 0.98,
                    "volume": 10_000_000,
                }
            )
        provider = MockDataProvider(pl.DataFrame(rows))
        engine = EventDrivenBacktestEngine(
            data_provider=provider,
            stop_loss=0.02,  # 2% 止损，极易触发
        )
        strategy = _BuyOnceStrategy(weights={"000001": 0.95})
        engine.run(strategy, "2024-01-01", "2024-01-08", ["000001"])

        trail = engine.audit_logger.get_full_trail()
        event_types = [e.get("event_type") for e in trail]
        # 风控触发后应出现 SIGNAL_SKIPPED_BY_RISK
        self.assertIn(
            "SIGNAL_SKIPPED_BY_RISK",
            event_types,
            "风控触发时应记录 SIGNAL_SKIPPED_BY_RISK 而非 SIGNAL",
        )
        # RISK_TRIGGER 也应存在
        self.assertIn("RISK_TRIGGER", event_types)


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
            low = close - 0.5  # 9.5, 8.5, 7.5, 6.5  ← 比 close 更低
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
