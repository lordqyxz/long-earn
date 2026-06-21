"""交易策略优化模块端到端测试（mock 回测）。

用 FakeStrategyOptimizer + mock 回测服务，确定性验证：验收门槛判定 +
完整 pipeline（optimize → backtest → accept）。真实引擎验证见
test_strategy_optimization_real_engine_e2e.py。
"""

from __future__ import annotations

from typing import Any

import pytest

from long_earn.strategy_optimization import (
    AcceptanceGate,
    FakeStrategyOptimizer,
    OptimizationPipeline,
    optimize_strategy,
)

BASE_STRATEGY: dict[str, Any] = {
    "strategy_name": "MomentumV1",
    "description": "动量策略",
}


class _MockBacktest:
    """按注入指标返回的 mock 回测服务。"""

    def __init__(self, metrics: dict[str, Any]) -> None:
        self._metrics = dict(metrics)
        self.calls = 0

    def run(self, strategy_yaml: str = "", start_date: str = "", end_date: str = "") -> dict[str, Any]:
        self.calls += 1
        return dict(self._metrics)


def _bt(sharpe: float, total_return: float, *, degenerate: bool = False, error: str = "") -> dict[str, Any]:
    return {
        "sharpe_ratio": sharpe,
        "total_return": total_return,
        "strategy_diagnostics": {"degenerate": degenerate},
        **({"error": error} if error else {}),
    }


class TestAcceptanceGate:
    @pytest.mark.parametrize(
        ("baseline", "optimized", "accepted"),
        [
            (_bt(1.0, 0.2), _bt(1.5, 0.3), True),                 # sharpe 提升 → 接受
            (_bt(1.5, 0.3), _bt(1.4, 0.35), False),               # sharpe 未提升 → 拒绝
            (_bt(1.0, 0.2), _bt(2.0, 0.5, degenerate=True), False),  # 退化 → 拒绝
            (_bt(1.0, 0.2), _bt(0.0, 0.0, error="数据缺失"), False),  # 回测失败 → 拒绝
            ({"total_return": 0.1}, _bt(0.8, 0.3), True),          # 基线无 sharpe → 接受
        ],
    )
    def test_acceptance(self, baseline, optimized, accepted):
        res = AcceptanceGate().evaluate(baseline, optimized)
        assert res.accepted is accepted


class TestOptimizationPipelineE2E:
    def _pipeline(self, optimized_metrics: dict[str, Any]):
        return OptimizationPipeline(
            FakeStrategyOptimizer(), _MockBacktest(optimized_metrics)
        )

    def test_pipeline_accepts_improved_strategy(self):
        outcome = self._pipeline(_bt(1.5, 0.3)).run(
            base_strategy=BASE_STRATEGY,
            base_strategy_yaml="strategy: ...",
            improvement_suggestions=["增加波动率过滤"],
            baseline_backtest=_bt(1.0, 0.2),
        )
        assert outcome.accepted is True
        assert outcome.optimized_strategy["strategy_name"] == "MomentumV1_opt"
        assert outcome.lineage_depth == 1

    def test_pipeline_rejects_degraded_strategy(self):
        outcome = self._pipeline(_bt(0.5, 0.1)).run(
            base_strategy=BASE_STRATEGY,
            base_strategy_yaml="strategy: ...",
            improvement_suggestions=["xxx"],
            baseline_backtest=_bt(1.5, 0.3),
        )
        assert outcome.accepted is False
        assert outcome.lineage_depth == 1

    def test_pipeline_rejects_without_yaml(self):
        backtest = _MockBacktest(_bt(1.5, 0.3))
        outcome = self._pipeline(_bt(1.5, 0.3)).run(
            base_strategy=BASE_STRATEGY,
            base_strategy_yaml="",
            improvement_suggestions=[],
            baseline_backtest=_bt(1.0, 0.2),
        )
        assert outcome.accepted is False
        assert backtest.calls == 0

    def test_optimize_strategy_convenience_fn(self):
        outcome = optimize_strategy(
            base_strategy=BASE_STRATEGY,
            base_strategy_yaml="strategy: ...",
            improvement_suggestions=["改进"],
            optimizer=FakeStrategyOptimizer(),
            backtest_service=_MockBacktest(_bt(2.0, 0.4)),
            baseline_backtest=_bt(1.0, 0.2),
        )
        assert outcome.accepted is True

    def test_lineage_accumulates_across_rounds(self):
        pipeline = self._pipeline(_bt(1.8, 0.35))
        round1 = pipeline.run(BASE_STRATEGY, "strategy: ...", ["s1"], baseline_backtest=_bt(1.0, 0.2))
        round2 = pipeline.run(
            round1.optimized_strategy, "strategy: ...", ["s2"], baseline_backtest=_bt(1.8, 0.35)
        )
        assert round1.lineage_depth == 1
        assert round2.lineage_depth == 2
