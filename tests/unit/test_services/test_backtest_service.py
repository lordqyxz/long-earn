"""BacktestServiceImpl 单元测试"""

from unittest.mock import MagicMock

import pandas as pd

from long_earn.config import AppConfig
from long_earn.services.backtest_service import BacktestServiceImpl, DSLStrategy


def _make_service() -> BacktestServiceImpl:
    """创建测试用的 BacktestServiceImpl（解耦 RuntimeContext 后直接接 config+logger）"""
    config = AppConfig()
    config.backtest_start_date = "2023-01-01"
    config.backtest_end_date = "2023-03-31"
    return BacktestServiceImpl(config, MagicMock())


class TestRunBacktest:
    def test_delegates_to_engine(self):
        """run 应调用事件驱动回测引擎"""
        svc = _make_service()

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
        svc = _make_service()

        result = svc.run(
            strategy_yaml="name: MomentumTest\nsignals: []",
            start_date="2023-01-01",
            end_date="2023-03-31",
        )

        assert result is not None

    def test_returns_error_on_bad_yaml(self):
        """YAML 解析失败时应返回错误"""
        svc = _make_service()

        result = svc.run(strategy_yaml="bad: [yaml: broken")
        assert result is not None
        assert "error" in result
        assert result["error_category"] == "client_error"

    def test_returns_error_when_no_strategy(self):
        """未提供任何策略时应返回客户端错误"""
        svc = _make_service()

        result = svc.run(strategy_yaml="")
        assert result is not None
        assert "error" in result
        assert result["error_category"] == "client_error"


class _StubDSL:
    """构造测试用的 DSL 桩（绕开 pydantic 校验）"""

    def __init__(self, factors=None, signals=None):
        self.name = "stub"
        self.factors = factors or {}
        self.signals = signals or []


class TestDSLStrategyFailureObservability:
    """DSLStrategy 失败可观测性测试

    防止"所有因子/step 静默失败 → 退化为全持仓平均权重 → 业绩 0 但 success=True"的假象。
    """

    def _make_df(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "symbol": ["000001", "000002"],
                "close": [10.0, 20.0],
                "open": [9.5, 19.0],
                "volume": [1000, 2000],
            }
        ).set_index("symbol")

    def test_bad_factor_records_failure(self):
        """因子表达式引用不存在字段时，失败必须被记录到 factor_failures"""
        dsl = _StubDSL(
            factors={"bad": "non_existent_field * 2", "good": "close * 1.0"}
        )
        strategy = DSLStrategy("test", dsl)
        df = self._make_df()

        out = strategy._compute_factors(df.copy())

        # 至少捕获到一个失败的因子
        assert len(strategy.factor_failures) >= 1
        # 失败记录含 alias / expr / error 字段
        failure = strategy.factor_failures[0]
        assert "alias" in failure
        assert "expr" in failure
        assert "error" in failure
        # 好因子仍然被计算（不影响其他因子）
        assert "good" in out.columns or len(strategy.factor_failures) == 2

    def test_bad_signal_step_records_failure(self):
        """signal step 表达式失败时，必须被记录到 step_failures"""
        dsl = _StubDSL(
            signals=[
                {"type": "filter", "condition": "non_existent_field > 0"},
                {"type": "rank", "by": "close", "top": 1},
            ]
        )
        strategy = DSLStrategy("test", dsl)
        df = self._make_df()

        strategy._execute_signal_steps(df)

        assert len(strategy.step_failures) >= 1
        first = strategy.step_failures[0]
        assert first["type"] == "filter"
        assert "non_existent_field" in first["error"] or first["error"]

    def test_clean_strategy_has_no_failures(self):
        """正常策略 factor_failures / step_failures 为空"""
        dsl = _StubDSL(
            factors={"f1": "close * 1.0"},
            signals=[{"type": "rank", "by": "close", "top": 1}],
        )
        strategy = DSLStrategy("test", dsl)
        df = self._make_df()

        strategy._compute_factors(df.copy())
        strategy._execute_signal_steps(df)

        assert strategy.factor_failures == []
        assert strategy.step_failures == []


class TestDSLFactorUsesHistoryWindow:
    """DSL 因子计算必须用历史窗口而非当前截面，否则 shift() 永远 NaN

    防止"LLM 生成动量/反转/波动率因子 → 因子永远 NaN → 信号步骤拿到全 NaN →
    要么 selected 空、要么退化为全持仓平均权重"的隐蔽失败。
    """

    def _make_history_df(self):
        """构造 5 个 bar × 2 symbol 的历史数据（polars）"""
        import polars as pl

        rows = []
        for day in range(1, 6):
            for sym, base in [("A", 10.0), ("B", 20.0)]:
                rows.append(
                    {
                        "timestamp": f"2024-01-0{day}",
                        "symbol": sym,
                        "open": base + day - 1,
                        "high": base + day,
                        "low": base + day - 1.5,
                        "close": base + day,  # A: 10..14, B: 20..24
                        "volume": 1000.0,
                    }
                )
        df = pl.DataFrame(rows)
        return df.with_columns(pl.col("timestamp").str.to_datetime())

    def _make_context(self, history_df, current_ts):
        """模拟 VisibilityContext：暴露 get_history_df 和 current_timestamp"""

        class _Ctx:
            def __init__(self):
                self._df = history_df
                self.current_timestamp = current_ts

            def get_history_df(self):
                return self._df

        return _Ctx()

    def test_shift_factor_no_longer_all_nan(self):
        """shift(close, 1) 在 5-bar 历史下应得到真实数值，而不是全 NaN"""
        import pandas as pd

        history = self._make_history_df()
        current_ts = pd.Timestamp("2024-01-05")

        # bars 是当前截面（兼容旧 on_bar 入参）
        bars = history.filter(history["timestamp"] == current_ts)

        dsl = _StubDSLWithWeights(
            weights=_StubWeights(method="equal"),
            factors={"prev_close": "shift(close, 1)"},
            signals=[{"type": "rank", "by": "prev_close", "top": 2}],
        )
        strategy = DSLStrategy("test", dsl)
        ctx = self._make_context(history, current_ts)

        signal_event = strategy.on_bar(bars, ctx)

        # 关键断言：on_bar 不是返回 None（因子能算出真实 prev_close）
        assert signal_event is not None, (
            "shift 因子在历史窗口下应能算出值，进而产生信号；"
            "之前 bug：on_bar 截面单行 → shift 全 NaN → 排序后 selected 空 → 无信号"
        )
        # final_weights 包含 2 个标的
        assert len(signal_event.signals) == 2

    def test_static_factor_still_works(self):
        """不需要历史的因子（close * 1.0）仍正常工作"""
        import pandas as pd

        history = self._make_history_df()
        current_ts = pd.Timestamp("2024-01-05")
        bars = history.filter(history["timestamp"] == current_ts)

        dsl = _StubDSLWithWeights(
            weights=_StubWeights(method="equal"),
            factors={"alpha": "close * 1.0"},
            signals=[{"type": "rank", "by": "alpha", "top": 1}],
        )
        strategy = DSLStrategy("test", dsl)
        ctx = self._make_context(history, current_ts)

        signal_event = strategy.on_bar(bars, ctx)
        assert signal_event is not None
        # 当前时刻 close: A=14, B=24，B 更高，rank top 1 选 B
        assert "B" in signal_event.signals
        assert strategy.factor_failures == []

    def test_history_fetch_failure_falls_back(self):
        """context.get_history_df 抛异常时应回退到 bars 截面，并记录到 step_failures"""
        import pandas as pd

        history = self._make_history_df()
        current_ts = pd.Timestamp("2024-01-05")
        bars = history.filter(history["timestamp"] == current_ts)

        class _BrokenCtx:
            current_timestamp = current_ts

            def get_history_df(self):
                raise RuntimeError("data layer down")

        dsl = _StubDSLWithWeights(
            weights=_StubWeights(method="equal"),
            factors={"alpha": "close * 1.0"},
            signals=[{"type": "rank", "by": "alpha", "top": 1}],
        )
        strategy = DSLStrategy("test", dsl)

        signal_event = strategy.on_bar(bars, _BrokenCtx())

        # 静态因子在 bars 兜底路径上也能算
        assert signal_event is not None
        # 历史拉取失败必须可观测
        assert any(
            f["type"] == "history_fetch" for f in strategy.step_failures
        ), "历史拉取失败必须写入 step_failures"


class _StubWeights:
    def __init__(self, method: str = "equal", signal_field: str = ""):
        self.method = method
        self.signal_field = signal_field


class _StubDSLWithWeights:
    def __init__(self, weights, factors=None, signals=None):
        self.name = "stub"
        self.factors = factors or {}
        self.signals = signals or []
        self.weights = weights


class TestDSLWeightFailureObservability:
    """DSLStrategy._compute_weights 静默退化必须写入 step_failures"""

    def _make_df(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "symbol": ["000001", "000002"],
                "close": [10.0, 20.0],
                "score": [0.5, 0.8],
            }
        ).set_index("symbol")

    def test_unknown_method_records_failure(self):
        """LLM 写错 weights.method 必须可观测"""
        dsl = _StubDSLWithWeights(weights=_StubWeights(method="weird"))
        strategy = DSLStrategy("t", dsl)
        df = self._make_df()

        result = strategy._compute_weights(df, ["000001"])

        assert result == {}
        assert any(
            "未知 weights.method" in f["error"] for f in strategy.step_failures
        )

    def test_equal_method_empty_selected_records_failure(self):
        """method=equal 但 selected 为空（信号步骤没选出标的）必须可观测"""
        dsl = _StubDSLWithWeights(weights=_StubWeights(method="equal"))
        strategy = DSLStrategy("t", dsl)

        result = strategy._compute_weights(self._make_df(), [])

        assert result == {}
        assert any("selected 为空" in f["error"] for f in strategy.step_failures)

    def test_signal_method_missing_field_records_failure(self):
        """method=signal 但 signal_field 不在 df.columns 必须可观测"""
        dsl = _StubDSLWithWeights(
            weights=_StubWeights(method="signal", signal_field="ghost_field")
        )
        strategy = DSLStrategy("t", dsl)

        result = strategy._compute_weights(self._make_df(), ["000001"])

        assert result == {}
        assert any(
            "ghost_field" in f["error"] for f in strategy.step_failures
        )

    def test_signal_method_zero_total_records_failure(self):
        """method=signal 但 signal_field 在 selected 上的正部和为 0 必须可观测"""
        df = pd.DataFrame(
            {"symbol": ["A", "B"], "score": [-1.0, -2.0]}
        ).set_index("symbol")
        dsl = _StubDSLWithWeights(
            weights=_StubWeights(method="signal", signal_field="score")
        )
        strategy = DSLStrategy("t", dsl)

        result = strategy._compute_weights(df, ["A", "B"])

        assert result == {}
        assert any("正部和为 0" in f["error"] for f in strategy.step_failures)

    def test_signal_method_happy_path(self):
        """method=signal 正常路径权重和为 1，无 step_failures"""
        dsl = _StubDSLWithWeights(
            weights=_StubWeights(method="signal", signal_field="score")
        )
        strategy = DSLStrategy("t", dsl)

        result = strategy._compute_weights(self._make_df(), ["000001", "000002"])

        assert result
        assert abs(sum(result.values()) - 1.0) < 1e-9
        assert strategy.step_failures == []


class TestBuildStrategyDiagnosticsAccumulation:
    """_build_strategy_diagnostics 跨 bar 累积 → 必须按 unique 判断退化

    实测 e2e 暴露 'step_failures=1043/6' 的字段错位 bug：跨 bar 累积让
    len(step_failures) == total_steps 在多 bar 下永远 False。
    """

    def test_cross_bar_accumulated_failures_detected_via_unique(self):
        """模拟 100 bar × 6 个 step 全失败 → 600 条记录，但 unique step index 只有 6"""
        svc = _make_service()
        # 模拟 strategy 累积了 600 条失败（100 bar × 6 step）
        strategy_obj = type("S", (), {})()
        strategy_obj.factor_failures = []
        strategy_obj.step_failures = [
            {"index": str(i % 6), "type": "filter", "step": "x", "error": "boom"}
            for i in range(600)
        ]

        # DSL 定义：6 个 signal step
        dsl = type("D", (), {})()
        dsl.factors = {}
        dsl.signals = [{"type": "filter"} for _ in range(6)]

        # result 0 trade
        result = type("R", (), {"trade_count": 0})()

        diag = svc._build_strategy_diagnostics(strategy_obj, dsl, result)

        # 旧 bug：len(step_failures) == total_steps → 600 == 6 永假；
        # 新逻辑：unique step index 集合长度 == 6 → all_steps_failed 为 True
        assert len(diag["step_failures"]) == 600
        assert set(diag["failed_step_indices"]) == {"0", "1", "2", "3", "4", "5"}
        assert diag["degenerate"] is True

    def test_partial_step_failures_not_degenerate_by_step_metric(self):
        """只有部分 step 失败 → 不应仅基于 step 维度标记 degenerate
        （但 trade_count=0 仍可触发 degenerate 兜底）
        """
        svc = _make_service()
        strategy_obj = type("S", (), {})()
        strategy_obj.factor_failures = []
        # 只有 step 0 / 1 失败，2 / 3 / 4 / 5 没失败过
        strategy_obj.step_failures = [
            {"index": "0", "type": "filter", "step": "x", "error": "e"},
            {"index": "1", "type": "rank", "step": "y", "error": "e"},
        ] * 50  # 100 条记录但只有 2 个 unique index

        dsl = type("D", (), {})()
        dsl.factors = {}
        dsl.signals = [{"type": "filter"} for _ in range(6)]

        # 假设确实成交了（trade_count > 0）→ 不应 degenerate
        result = type("R", (), {"trade_count": 50})()
        diag = svc._build_strategy_diagnostics(strategy_obj, dsl, result)

        assert set(diag["failed_step_indices"]) == {"0", "1"}
        # 2/6 失败 + trade_count=50 → degenerate=False
        assert diag["degenerate"] is False

    def test_factor_failures_use_unique_alias(self):
        """factor_failures 同样按 alias 去重判断"""
        svc = _make_service()
        strategy_obj = type("S", (), {})()
        # 200 条记录，但只有 3 个 unique alias
        strategy_obj.factor_failures = [
            {"alias": f"f{i % 3}", "expr": "x", "error": "boom"}
            for i in range(200)
        ]
        strategy_obj.step_failures = []

        dsl = type("D", (), {})()
        # DSL 定义 3 个 factor
        dsl.factors = {"f0": "...", "f1": "...", "f2": "..."}
        dsl.signals = [{"type": "filter"}]

        result = type("R", (), {"trade_count": 100})()
        diag = svc._build_strategy_diagnostics(strategy_obj, dsl, result)

        assert set(diag["failed_factor_aliases"]) == {"f0", "f1", "f2"}
        # 3/3 factor 都失败过 → degenerate=True（即使 trade_count > 0）
        assert diag["degenerate"] is True
