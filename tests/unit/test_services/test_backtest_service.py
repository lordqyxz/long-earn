"""BacktestServiceImpl 单元测试

ADR-009 收尾：DSLStrategy 仅走算子目录路径，旧式 factors / filter / rank /
expression 信号路径已退役。本测试覆盖：
1. BacktestServiceImpl.run 高层行为（DSL 解析 / 错误归类）
2. DSLStrategy 算子路径失败可观测性（history_fetch / operator_execute / equal 空选）
3. _build_strategy_diagnostics 跨 bar 累积去重 + metrics_unreliable 判定
"""

from unittest.mock import MagicMock

import pandas as pd
import polars as pl

from long_earn.backtest.engine.audit import RUN_TAG_TEST
from long_earn.backtest.engine.dsl_strategy import DSLStrategy
from long_earn.config import AppConfig
from long_earn.services.backtest_service import BacktestServiceImpl


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
            tags=[RUN_TAG_TEST],
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
            tags=[RUN_TAG_TEST],
        )

        assert result is not None

    def test_returns_error_on_bad_yaml(self):
        """YAML 解析失败时应返回错误"""
        svc = _make_service()

        result = svc.run(strategy_yaml="bad: [yaml: broken")
        assert result is not None
        assert "error" in result
        assert result["error_category"] == "client_error"


class TestRunOosBoundaries:
    def test_rejects_window_outside_test_split_before_backtest(self):
        svc = _make_service()

        result = svc.run_oos(
            strategy_yaml="name: test\nsignals: []",
            start_date="2024-12-31",
            end_date="2025-02-01",
        )

        assert "必须位于测试集" in result["error"]
        assert result["fold_results"] == []

    def test_rejects_reversed_oos_window_before_backtest(self):
        svc = _make_service()

        result = svc.run_oos(
            strategy_yaml="name: test\nsignals: []",
            start_date="2025-02-01",
            end_date="2025-01-01",
        )

        assert result["error"] == "OOS 日期倒序"

    def test_returns_error_when_no_strategy(self):
        """未提供任何策略时应返回客户端错误"""
        svc = _make_service()

        result = svc.run(strategy_yaml="")
        assert result is not None
        assert "error" in result
        assert result["error_category"] == "client_error"


class _StubDSL:
    """构造测试用的 DSL 桩（绕开 pydantic 校验）。

    ADR-009 收尾后仅含算子目录字段：``operator_factors`` / ``signals`` / ``weights``。
    """

    def __init__(self, operator_factors=None, signals=None, weights=None):
        self.name = "stub"
        self.operator_factors = operator_factors or []
        self.signals = signals or []
        self.weights = weights or _StubWeights(method="equal")
        self.universe = _StubUniverse()


class _StubUniverse:
    """股票池配置桩（rebalance_freq 默认每日调仓）。"""

    def __init__(self, rebalance_freq: str = "1D"):
        self.rebalance_freq = rebalance_freq


class _StubWeights:
    """权重配置桩（ADR-009 收尾：仅 equal）"""

    def __init__(self, method: str = "equal"):
        self.method = method


def _make_history_panel() -> pl.DataFrame:
    """构造 5 个 bar × 2 symbol 的 polars 历史面板（算子路径输入）。"""
    rows = []
    for day in range(1, 6):
        for sym, base in [("A", 10.0), ("B", 20.0)]:
            rows.append(
                {
                    "timestamp": pd.Timestamp(f"2024-01-0{day}"),
                    "symbol": sym,
                    "open": base + day - 1,
                    "high": base + day,
                    "low": base + day - 1.5,
                    "close": base + day,  # A: 11..15, B: 21..25
                    "volume": 1000.0,
                }
            )
    return pl.DataFrame(rows)


class _StubContext:
    """模拟 VisibilityContext：暴露 get_history_df 和 current_timestamp。"""

    def __init__(self, history_df: pl.DataFrame, current_ts):
        self._df = history_df
        self.current_timestamp = current_ts

    def get_history_df(self) -> pl.DataFrame:
        return self._df


class _BrokenContext:
    """模拟 get_history_df 抛异常的 context。"""

    def __init__(self, current_ts):
        self.current_timestamp = current_ts

    def get_history_df(self) -> pl.DataFrame:
        raise RuntimeError("data layer down")


class TestDSLStrategyOperatorPathFailureObservability:
    """DSLStrategy 算子路径失败可观测性测试

    防止"算子执行链静默失败 → on_bar 返回 None → 业绩 0 但 success=True"的假象。
    ADR-009 收尾后失败模式收敛为三类：
    - history_fetch：context.get_history_df 抛异常
    - operator_execute：算子执行器抛异常
    - weights：_equal_weights 收到空 selected
    """

    def test_history_fetch_failure_records_step_failure(self):
        """context.get_history_df 抛异常时，必须记入 step_failures 并返回 None"""
        current_ts = pd.Timestamp("2024-01-05")
        dsl = _StubDSL(
            operator_factors=[
                {
                    "op": "returns",
                    "alias": "mom",
                    "params": {"field": "close", "period": 1},
                }
            ],
            signals=[
                {
                    "type": "operator",
                    "op": "rank_top",
                    "params": {"field": "mom", "top": 1},
                },
            ],
        )
        strategy = DSLStrategy("test", dsl)

        signal_event = strategy.on_bar(pl.DataFrame(), _BrokenContext(current_ts))

        assert signal_event is None
        assert any(f["type"] == "history_fetch" for f in strategy.step_failures), (
            "history_fetch 失败必须写入 step_failures"
        )

    def test_operator_execute_failure_records_step_failure(self):
        """算子执行器抛异常时，必须记入 step_failures 并返回 None"""
        current_ts = pd.Timestamp("2024-01-05")
        history = _make_history_panel()
        # 用一个不存在的算子 op 构造 executor（绕开解析期校验，直接注入坏 executor）
        dsl = _StubDSL()
        strategy = DSLStrategy("test", dsl)

        class _RaisingExecutor:
            def execute(self, panel, ts):
                raise RuntimeError("operator boom")

        strategy._op_executor = _RaisingExecutor()  # type: ignore[attr-defined]

        signal_event = strategy.on_bar(
            history.head(1), _StubContext(history, current_ts)
        )

        assert signal_event is None
        assert any(f["type"] == "operator_execute" for f in strategy.step_failures), (
            "operator_execute 失败必须写入 step_failures"
        )

    def test_empty_selected_records_weights_failure(self):
        """_equal_weights([]) 必须记入 step_failures（selected 为空 = 信号步骤没选出标的）"""
        dsl = _StubDSL()
        strategy = DSLStrategy("t", dsl)

        result = strategy._equal_weights([])

        assert result == {}
        assert any("selected 为空" in f["error"] for f in strategy.step_failures)

    def test_clean_strategy_has_no_failures(self):
        """正常算子路径：history 可取、算子可执行、selected 非空 → 无 step_failures"""
        current_ts = pd.Timestamp("2024-01-05")
        history = _make_history_panel()
        dsl = _StubDSL(
            operator_factors=[
                {
                    "op": "returns",
                    "alias": "mom",
                    "params": {"field": "close", "period": 1},
                }
            ],
            signals=[
                {
                    "type": "operator",
                    "op": "filter_threshold",
                    "params": {"field": "mom", "op": ">", "value": 0.0},
                },
                {
                    "type": "operator",
                    "op": "rank_top",
                    "params": {"field": "mom", "top": 2, "ascending": False},
                },
            ],
        )
        strategy = DSLStrategy("test", dsl)

        signal_event = strategy.on_bar(
            history.head(1), _StubContext(history, current_ts)
        )

        assert signal_event is not None
        assert strategy.step_failures == []


class TestBuildStrategyDiagnosticsAccumulation:
    """_build_strategy_diagnostics 跨 bar 累积 → 必须按 unique 标签判断退化

    ADR-009 收尾后诊断字段：
    - ``failed_factor_aliases``：factor_failures 按 alias 去重（算子路径不再写入，留空）
    - ``failed_step_labels``：step_failures 按 step 标签去重（operator_execute /
      on_bar history / method=equal）
    - ``degenerate``：trade_count == 0
    - ``metrics_unreliable``：degenerate 或 任何 step/factor 失败
    """

    def test_cross_bar_accumulated_step_failures_detected_via_unique_label(self):
        """模拟 100 bar × 同一 step 标签失败 → 100 条记录，但 unique label 只有 1"""
        svc = _make_service()
        strategy_obj = type("S", (), {})()
        strategy_obj.factor_failures = []
        strategy_obj.step_failures = [
            {"type": "operator_execute", "step": "operator_executor", "error": "boom"}
            for _ in range(100)
        ]

        dsl = type("D", (), {})()
        dsl.signals = [{"type": "operator"}]

        result = type("R", (), {"trade_count": 0})()

        diag = svc._build_strategy_diagnostics(strategy_obj, dsl, result)

        assert len(diag["step_failures"]) == 100
        assert diag["failed_step_labels"] == ["operator_executor"]
        # trade_count=0 → degenerate=True
        assert diag["degenerate"] is True
        assert diag["metrics_unreliable"] is True

    def test_partial_step_failures_with_trades_not_degenerate(self):
        """部分 step 失败 + trade_count > 0 → degenerate=False 但 metrics_unreliable=True"""
        svc = _make_service()
        strategy_obj = type("S", (), {})()
        strategy_obj.factor_failures = []
        strategy_obj.step_failures = [
            {"type": "history_fetch", "step": "on_bar history", "error": "e"},
        ]

        dsl = type("D", (), {})()
        dsl.signals = [{"type": "operator"}, {"type": "operator"}]

        result = type("R", (), {"trade_count": 50})()
        diag = svc._build_strategy_diagnostics(strategy_obj, dsl, result)

        # 1/2 step 失败，trade_count=50 → degenerate=False
        assert diag["degenerate"] is False
        # 但 metrics_unreliable 必须为 True：step 失败意味着选股逻辑残缺
        assert diag["metrics_unreliable"] is True

    def test_factor_failures_use_unique_alias(self):
        """factor_failures 按 alias 去重（算子路径不再写入，但诊断逻辑保留兼容）"""
        svc = _make_service()
        strategy_obj = type("S", (), {})()
        strategy_obj.factor_failures = [
            {"alias": f"f{i % 3}", "expr": "x", "error": "boom"} for i in range(200)
        ]
        strategy_obj.step_failures = []

        dsl = type("D", (), {})()
        dsl.signals = [{"type": "operator"}]

        result = type("R", (), {"trade_count": 100})()
        diag = svc._build_strategy_diagnostics(strategy_obj, dsl, result)

        assert set(diag["failed_factor_aliases"]) == {"f0", "f1", "f2"}
        # 3 个 factor 都失败过 → metrics_unreliable=True
        assert diag["metrics_unreliable"] is True

    def test_metrics_unreliable_when_any_step_fails(self):
        """任何 step 失败 → metrics_unreliable=True（即使 trade_count > 0）"""
        svc = _make_service()
        strategy_obj = type("S", (), {})()
        strategy_obj.factor_failures = []
        strategy_obj.step_failures = [
            {"type": "weights", "step": "method=equal", "error": "boom"},
        ]
        dsl = type("D", (), {})()
        dsl.signals = [{"type": "operator"}, {"type": "operator"}]
        result = type("R", (), {"trade_count": 100})()

        diag = svc._build_strategy_diagnostics(strategy_obj, dsl, result)

        assert diag["degenerate"] is False
        assert diag["metrics_unreliable"] is True

    def test_metrics_reliable_when_clean(self):
        """无任何失败 + trade_count > 0 → metrics_unreliable=False"""
        svc = _make_service()
        strategy_obj = type("S", (), {})()
        strategy_obj.factor_failures = []
        strategy_obj.step_failures = []
        dsl = type("D", (), {})()
        dsl.signals = [{"type": "operator"}]
        result = type("R", (), {"trade_count": 100})()

        diag = svc._build_strategy_diagnostics(strategy_obj, dsl, result)

        assert diag["degenerate"] is False
        assert diag["metrics_unreliable"] is False

    def test_degenerate_when_zero_trades_even_without_failures(self):
        """trade_count=0 + 无失败 → degenerate=True（策略啥都没干）"""
        svc = _make_service()
        strategy_obj = type("S", (), {})()
        strategy_obj.factor_failures = []
        strategy_obj.step_failures = []
        dsl = type("D", (), {})()
        dsl.signals = [{"type": "operator"}]
        result = type("R", (), {"trade_count": 0})()

        diag = svc._build_strategy_diagnostics(strategy_obj, dsl, result)

        assert diag["degenerate"] is True
        assert diag["metrics_unreliable"] is True

    def test_engine_skip_marks_metrics_unreliable(self):
        """引擎层 skip/部分成交标志应合并进 diagnostics.metrics_unreliable"""
        svc = _make_service()
        strategy_obj = type("S", (), {})()
        strategy_obj.factor_failures = []
        strategy_obj.step_failures = []
        dsl = type("D", (), {})()
        dsl.signals = [{"type": "operator"}]
        result = type("R", (), {"trade_count": 100, "metrics_unreliable": True})()

        diag = svc._build_strategy_diagnostics(strategy_obj, dsl, result)

        assert diag["degenerate"] is False
        assert diag["engine_metrics_unreliable"] is True
        assert diag["metrics_unreliable"] is True


class TestInflatedReturnBugFixOperatorPath:
    """虚高 bug 回归测试（ADR-009 收尾：算子路径版）

    复现原生产 bug 的精神：选股逻辑残缺时，selected 必须置空（保守不选），
    且 metrics_unreliable=True，让上层识别指标不可信。

    算子路径下 filter_threshold 自然产生 0 行 → selected=[]（无虚高风险），
    此测试验证该保护链路在 DSLStrategy 层的行为可观测。
    """

    def test_filter_to_zero_rows_produces_empty_selected_and_weights_failure(self):
        """filter_threshold 过滤后 0 行 → selected=[] → _equal_weights 记 failure"""
        current_ts = pd.Timestamp("2024-01-05")
        history = _make_history_panel()
        # mom 永远 > 0（价格递增），filter value=999 永不成立 → 0 行
        dsl = _StubDSL(
            operator_factors=[
                {
                    "op": "returns",
                    "alias": "mom",
                    "params": {"field": "close", "period": 1},
                }
            ],
            signals=[
                {
                    "type": "operator",
                    "op": "filter_threshold",
                    "params": {"field": "mom", "op": ">", "value": 999.0},
                },
                {
                    "type": "operator",
                    "op": "rank_top",
                    "params": {"field": "mom", "top": 2, "ascending": False},
                },
            ],
        )
        strategy = DSLStrategy("t", dsl)

        signal_event = strategy.on_bar(
            history.head(1), _StubContext(history, current_ts)
        )

        # filter 过滤后 0 行 → selected=[] → 无信号
        assert signal_event is None
        # _equal_weights([]) 记录了 weights failure
        assert any("selected 为空" in f["error"] for f in strategy.step_failures)
