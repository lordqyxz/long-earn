"""ADR-015 统计过拟合门单元测试。

验证三道统计门（Walk-Forward 稳定性 / DSR / PBO）的判定逻辑。
面向接口测试，覆盖契约：

- S1: 稳定性门拒绝窗口不一致的策略（Q1/Q2 问题根因）
- S2: DSR 门拒绝多重检验不显著的 sharpe
- S3: PBO 门拒绝多策略集合过拟合
"""

from __future__ import annotations

from typing import Any

import pytest

from long_earn.strategy_optimization.overfit_gates import (
    BacktestOverfitGate,
    DeflatedSharpeGate,
    WalkForwardStabilityGate,
)


def _fold(sharpe: float) -> dict[str, Any]:
    """构造 fold_results 元素。"""
    return {"fold_id": 0, "train": {}, "test": {"sharpe_ratio": sharpe}}


class TestWalkForwardStabilityGate:
    """S1: Walk-Forward 稳定性门。"""

    @pytest.mark.parametrize(
        ("sharpes", "should_pass"),
        [
            # 1. 稳定：所有折 sharpe 正且方差小
            ([0.4, 0.5, 0.45], True),
            # 2. Q1/Q2 不一致根因：worst fold < 0（窗口极度不一致）
            ([0.3, -0.5, 1.4], False),
            # 3. 方差过大：[0.3, -0.05, 1.4] 即使 worst 通过，std 仍超限
            ([1.5, -0.05, 0.3], False),
            # 4. 一致性不足：仅 1/3 折正
            ([0.3, -0.05, -0.1], False),
            # 5. 单折（无法判断稳定性，放行）
            ([0.5], True),
            # 6. 全部负（worst fold 触发）
            ([-0.3, -0.5, -0.2], False),
        ],
    )
    def test_stability_judgment(self, sharpes: list[float], should_pass: bool) -> None:
        gate = WalkForwardStabilityGate()
        fold_results = [_fold(s) for s in sharpes]
        result = gate.evaluate(fold_results)
        assert result.passed == should_pass

    def test_empty_fold_results_rejected(self) -> None:
        """无 fold 数据应被拒绝。"""
        gate = WalkForwardStabilityGate()
        result = gate.evaluate([])
        assert not result.passed
        assert "无有效" in result.reason

    def test_result_contains_metrics(self) -> None:
        """结果应包含 worst_fold_sharpe / std / consistency_ratio。"""
        gate = WalkForwardStabilityGate()
        result = gate.evaluate([_fold(0.3), _fold(0.5)])
        assert result.worst_fold_sharpe == pytest.approx(0.3)
        assert result.consistency_ratio == pytest.approx(1.0)
        assert len(result.fold_sharpes) == 2


class TestDeflatedSharpeGate:
    """S2: Deflated Sharpe Ratio 门。"""

    def test_high_sharpe_few_trials_passes(self) -> None:
        """高 sharpe + 少试验数应通过。"""
        gate = DeflatedSharpeGate()
        # SR=2.0, N=1, T=252 → t = (2.0 - 0) / (1/sqrt(252)) = 2.0 * 15.87 = 31.75
        result = gate.evaluate(
            observed_sharpe=2.0, n_trials=1, n_observations=252
        )
        assert result.passed
        assert result.t_statistic > 1.96

    def test_low_sharpe_many_trials_rejected(self) -> None:
        """低 sharpe + 多试验数应拒绝（multiple testing 校正生效）。"""
        gate = DeflatedSharpeGate()
        # SR=0.3, N=100, T=252 → E[max_100] = sqrt(2*ln(100)) ≈ 3.03
        # t = (0.3 - 3.03*0.063) / 0.063 ≈ (0.3 - 0.191) / 0.063 ≈ 1.73 < 1.96
        result = gate.evaluate(
            observed_sharpe=0.3, n_trials=100, n_observations=252
        )
        assert not result.passed
        assert result.expected_max_noise > 2.5  # E[max_N] 约等于 3.03

    def test_n_trials_1_no_correction(self) -> None:
        """N=1 时 E[max_N]=0，无多重检验校正。"""
        gate = DeflatedSharpeGate()
        result = gate.evaluate(
            observed_sharpe=0.5, n_trials=1, n_observations=252
        )
        assert result.expected_max_noise == 0.0
        assert result.passed

    def test_insufficient_observations_rejected(self) -> None:
        """回测天数 < 2 应拒绝。"""
        gate = DeflatedSharpeGate()
        result = gate.evaluate(
            observed_sharpe=2.0, n_trials=1, n_observations=1
        )
        assert not result.passed
        assert "不足" in result.reason

    def test_threshold_increases_with_n(self) -> None:
        """N 增大时，相同 sharpe 更难通过（selection bias 收紧）。"""
        gate = DeflatedSharpeGate()
        # SR=1.0, T=252, N=1 vs N=100
        result_n1 = gate.evaluate(observed_sharpe=1.0, n_trials=1, n_observations=252)
        result_n100 = gate.evaluate(
            observed_sharpe=1.0, n_trials=100, n_observations=252
        )
        # N=1 应更容易通过
        assert result_n1.t_statistic > result_n100.t_statistic


class TestBacktestOverfitGate:
    """S3: PBO 概率门（CSCV）。"""

    def test_insufficient_strategies_passes(self) -> None:
        """策略数 < 2 时跳过检验（返回 passed=True）。"""
        gate = BacktestOverfitGate()
        result = gate.evaluate([1.0], [0.5])
        assert result.passed
        assert "不足" in result.reason

    def test_consistent_strategies_pass(self) -> None:
        """IS/OOS sharpe 一致高 → PBO 应较低，通过。"""
        gate = BacktestOverfitGate(n_samples=100)
        # IS 和 OOS 排序一致，IS 最优 = OOS 最优
        is_sharpes = [1.5, 1.0, 0.5, 0.2]
        oos_sharpes = [1.4, 0.9, 0.4, 0.1]
        result = gate.evaluate(is_sharpes, oos_sharpes)
        assert result.passed
        assert result.pbo_probability <= 0.5

    def test_inverted_strategies_rejected(self) -> None:
        """IS 最优 = OOS 最差 → PBO 应较高，拒绝。"""
        gate = BacktestOverfitGate(n_samples=200)
        # IS 排序: [2.0, 1.0, 0.5, 0.0]
        # OOS 排序反转: [0.0, 0.5, 1.0, 2.0] → IS 最优在 OOS 最差
        is_sharpes = [2.0, 1.0, 0.5, 0.0]
        oos_sharpes = [0.0, 0.5, 1.0, 2.0]
        result = gate.evaluate(is_sharpes, oos_sharpes)
        assert not result.passed
        assert result.pbo_probability > 0.5

    def test_length_mismatch_passes(self) -> None:
        """is_sharpes 与 oos_sharpes 长度不一致时跳过。"""
        gate = BacktestOverfitGate()
        result = gate.evaluate([1.0, 0.5], [0.3])
        assert result.passed

    def test_reproducible_with_fixed_seed(self) -> None:
        """相同输入两次运行结果一致（固定种子）。"""
        gate = BacktestOverfitGate(n_samples=100)
        is_sharpes = [1.5, 1.0, 0.5]
        oos_sharpes = [1.4, 0.9, 0.4]
        result1 = gate.evaluate(is_sharpes, oos_sharpes)
        result2 = gate.evaluate(is_sharpes, oos_sharpes)
        assert result1.pbo_probability == result2.pbo_probability
