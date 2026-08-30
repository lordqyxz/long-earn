"""ADR-015 统计过拟合门单元测试。

验证三道统计门（Walk-Forward 稳定性 / DSR / PBO）的判定逻辑。
面向接口测试，覆盖契约：

- S1: 稳定性门拒绝窗口不一致的策略（Q1/Q2 问题根因）
- S2: DSR 门拒绝多重检验不显著的 sharpe
- S3: PBO 门拒绝多策略集合过拟合
"""

from __future__ import annotations

import random
from typing import Any

import pytest

from long_earn.strategy_optimization.overfit_gates import (
    MERGE_THRESHOLD_DEFAULT,
    BacktestOverfitGate,
    DeflatedSharpeGate,
    WalkForwardStabilityGate,
    daily_return_moments,
    diagnostic_to_dict,
    evaluate_haircut_sharpe,
    evaluate_merge_gate,
    evaluate_mintrl,
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

    def test_partial_fold_sharpes_rejected(self) -> None:
        """3 个 fold 仅 1 个有 sharpe → 不通过。"""
        gate = WalkForwardStabilityGate()
        fold_results = [
            _fold(0.5),
            {"fold_id": 1, "train": {}, "test": {}},
            {"fold_id": 2, "train": {}, "test": {}},
        ]
        result = gate.evaluate(fold_results)
        assert not result.passed
        assert "有效折数不足" in result.reason
        assert len(result.fold_sharpes) == 1

    def test_single_fold_with_sharpe_passes(self) -> None:
        """单折且完整仍可通过。"""
        gate = WalkForwardStabilityGate()
        result = gate.evaluate([_fold(0.5)])
        assert result.passed

    def test_gap_empty_test_fold_fails_stability(self) -> None:
        """gap 致空 test 折时，fold 占位但无 sharpe → 有效折数不足。"""
        gate = WalkForwardStabilityGate()
        fold_results = [
            _fold(0.5),
            {"fold_id": 1, "train": {}, "test": {}},
            _fold(0.4),
        ]
        result = gate.evaluate(fold_results)
        assert not result.passed
        assert "有效折数不足" in result.reason
        assert len(result.fold_sharpes) == 2


class TestDeflatedSharpeGate:
    """S2: Deflated Sharpe Ratio 门。"""

    def test_high_sharpe_few_trials_passes(self) -> None:
        """高 sharpe + 少试验数应通过。"""
        gate = DeflatedSharpeGate()
        # SR=2.0, N=1, T=252 → t = (2.0 - 0) / (1/sqrt(252)) = 2.0 * 15.87 = 31.75
        result = gate.evaluate(observed_sharpe=2.0, n_trials=1, n_observations=252)
        assert result.passed
        assert result.t_statistic > 1.96

    def test_low_sharpe_many_trials_rejected(self) -> None:
        """低 sharpe + 多试验数应拒绝（multiple testing 校正生效）。"""
        gate = DeflatedSharpeGate()
        # SR=0.3, N=100, T=252 → E[max_100] = sqrt(2*ln(100)) ≈ 3.03
        # t = (0.3 - 3.03*0.063) / 0.063 ≈ (0.3 - 0.191) / 0.063 ≈ 1.73 < 1.96
        result = gate.evaluate(observed_sharpe=0.3, n_trials=100, n_observations=252)
        assert not result.passed
        assert result.expected_max_noise > 2.5  # E[max_N] 约等于 3.03

    def test_n_trials_1_no_correction(self) -> None:
        """N=1 时 E[max_N]=0，无多重检验校正。"""
        gate = DeflatedSharpeGate()
        result = gate.evaluate(observed_sharpe=0.5, n_trials=1, n_observations=252)
        assert result.expected_max_noise == 0.0
        assert result.passed

    def test_insufficient_observations_skipped(self) -> None:
        """回测天数 < 2 应跳过（诊断门控，非 failed）。"""
        gate = DeflatedSharpeGate()
        result = gate.evaluate(observed_sharpe=2.0, n_trials=1, n_observations=1)
        assert result.skipped
        assert not result.passed
        assert result.status == "skipped"
        assert "不足" in result.reason

    def test_full_dsr_with_skew_kurtosis(self) -> None:
        """提供 skew/kurt 时走完整非简化 DSR。"""
        gate = DeflatedSharpeGate()
        result = gate.evaluate(
            observed_sharpe=2.0,
            n_trials=1,
            n_observations=252,
            skew=0.1,
            kurtosis=3.2,
        )
        assert not result.simplified
        assert result.status in ("passed", "failed")
        assert result.t_statistic > 0

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

    def test_insufficient_strategies_skipped(self) -> None:
        """策略数 < 2 时跳过检验（status=skipped，不得视为通过）。"""
        gate = BacktestOverfitGate()
        result = gate.evaluate([1.0], [0.5])
        assert result.skipped
        assert not result.passed
        assert result.status == "skipped"
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
        # threshold=0.49 避免 CSCV 采样恰为 0.5 时边界通过
        gate = BacktestOverfitGate(threshold=0.49, n_samples=200)
        # IS 排序: [2.0, 1.0, 0.5, 0.0]
        # OOS 排序反转: [0.0, 0.5, 1.0, 2.0] → IS 最优在 OOS 最差
        is_sharpes = [2.0, 1.0, 0.5, 0.0]
        oos_sharpes = [0.0, 0.5, 1.0, 2.0]
        result = gate.evaluate(is_sharpes, oos_sharpes)
        assert not result.passed
        assert result.status == "failed"
        assert result.pbo_probability >= 0.49

    def test_length_mismatch_skipped(self) -> None:
        """is_sharpes 与 oos_sharpes 长度不一致时跳过。"""
        gate = BacktestOverfitGate()
        result = gate.evaluate([1.0, 0.5], [0.3])
        assert result.skipped
        assert not result.passed
        assert result.status == "skipped"

    def test_reproducible_with_fixed_seed(self) -> None:
        """相同输入两次运行结果一致（固定种子）。"""
        gate = BacktestOverfitGate(n_samples=100)
        is_sharpes = [1.5, 1.0, 0.5]
        oos_sharpes = [1.4, 0.9, 0.4]
        result1 = gate.evaluate(is_sharpes, oos_sharpes)
        result2 = gate.evaluate(is_sharpes, oos_sharpes)
        assert result1.pbo_probability == result2.pbo_probability

    def test_pair_legacy_reason_tag(self) -> None:
        """legacy pair 路径 reason 含 method=pair_legacy。"""
        gate = BacktestOverfitGate(n_samples=50)
        result = gate.evaluate([1.5, 1.0, 0.5], [1.4, 0.9, 0.4])
        assert "method=pair_legacy" in result.reason


def _build_correlated_returns_matrix(
    *,
    t_rows: int = 200,
    n_cols: int = 4,
    seed: int = 42,
) -> list[list[float]]:
    """同噪声+漂移：各列高度相关，CSCV PBO 应偏低。"""
    rng = random.Random(seed)
    base = [rng.gauss(0.001, 0.01) for _ in range(t_rows)]
    return [
        [base[t] + rng.gauss(0.0, 0.0005) for _ in range(n_cols)] for t in range(t_rows)
    ]


def _build_anticorrelated_returns_matrix(
    *,
    t_rows: int = 200,
    n_cols: int = 4,
    n_blocks: int = 8,
) -> list[list[float]]:
    """块内单峰列：IS 赢家 OOS 常落后，CSCV PBO 应偏高。"""
    block_size = t_rows // n_blocks
    usable_t = block_size * n_blocks
    matrix: list[list[float]] = []
    for t in range(usable_t):
        block_idx = t // block_size
        peak_col = block_idx % n_cols
        row = [-0.002] * n_cols
        row[peak_col] = 0.03
        matrix.append(row)
    return matrix


class TestBacktestOverfitGateMatrixCSCV:
    """S3 扩展：T×N 收益矩阵标准 CSCV PBO。"""

    def test_correlated_columns_low_pbo(self) -> None:
        """相关列（同噪声+漂移）→ PBO 较低，倾向通过。"""
        gate = BacktestOverfitGate(threshold=0.5, n_samples=200)
        matrix = _build_correlated_returns_matrix()
        result = gate.evaluate_returns_matrix(matrix, n_blocks=8)
        assert result.status != "skipped"
        assert "method=cscv_matrix" in result.reason
        assert result.pbo_probability <= 0.5
        assert result.passed

    def test_anticorrelated_columns_high_pbo(self) -> None:
        """反相关/块轮换列 → PBO 较高，倾向拒绝。"""
        gate = BacktestOverfitGate(threshold=0.49, n_samples=200)
        matrix = _build_anticorrelated_returns_matrix()
        result = gate.evaluate_returns_matrix(matrix, n_blocks=8)
        assert result.status == "failed"
        assert result.pbo_probability >= 0.49
        assert not result.passed

    def test_insufficient_matrix_skipped(self) -> None:
        """矩阵不足（T 或 N）→ skipped。"""
        gate = BacktestOverfitGate()
        too_few_cols = [[0.01] for _ in range(100)]
        result_cols = gate.evaluate_returns_matrix(too_few_cols, n_blocks=8)
        assert result_cols.skipped
        assert "method=cscv_matrix" in result_cols.reason

        too_few_rows = [[0.01, 0.02, 0.03] for _ in range(10)]
        result_rows = gate.evaluate_returns_matrix(too_few_rows, n_blocks=8)
        assert result_rows.skipped

    def test_matrix_reproducible_with_fixed_seed(self) -> None:
        """矩阵 CSCV 固定种子可复现。"""
        gate = BacktestOverfitGate(n_samples=100)
        matrix = _build_correlated_returns_matrix(seed=7)
        r1 = gate.evaluate_returns_matrix(matrix, n_blocks=8)
        r2 = gate.evaluate_returns_matrix(matrix, n_blocks=8)
        assert r1.pbo_probability == r2.pbo_probability


class TestMinTRLDiagnostic:
    """MinTRL 诊断契约（不硬拒）。"""

    def test_sufficient_observations_passed(self) -> None:
        """T >= mintrl → passed。"""
        result = evaluate_mintrl(observed_sharpe=1.0, n_observations=500)
        assert result["status"] == "passed"
        assert result["passed"] is True
        assert result["mintrl"] > 0
        assert result["n_observations"] == 500

    def test_insufficient_observations_failed(self) -> None:
        """T < mintrl → failed。"""
        # SR=0.5 → mintrl≈16.4，T=10 不足
        result = evaluate_mintrl(observed_sharpe=0.5, n_observations=10)
        assert result["status"] == "failed"
        assert result["passed"] is False
        assert result["n_observations"] < result["mintrl"]

    def test_zero_sharpe_skipped(self) -> None:
        """SR≈0 → skipped。"""
        result = evaluate_mintrl(observed_sharpe=0.0, n_observations=252)
        assert result["status"] == "skipped"
        assert result["skipped"] is True


class TestHaircutSharpeDiagnostic:
    """Haircut Sharpe 诊断契约（Bonferroni / 期望最大噪声）。"""

    def test_high_sharpe_few_trials_passed(self) -> None:
        """高 SR、少试验 → haircut > 0，passed。"""
        result = evaluate_haircut_sharpe(
            observed_sharpe=2.0,
            n_trials=1,
            n_observations=252,
        )
        assert result["status"] == "passed"
        assert result["haircut_sharpe"] > 0
        assert result["method"] == "expected_max_noise"
        assert "method=expected_max_noise" in result["reason"]

    def test_low_sharpe_many_trials_failed(self) -> None:
        """低 SR、多试验 → haircut <= 0，failed。"""
        result = evaluate_haircut_sharpe(
            observed_sharpe=0.15,
            n_trials=100,
            n_observations=252,
        )
        assert result["status"] == "failed"
        assert result["haircut_sharpe"] <= 0
        assert result["n_trials"] == 100

    def test_insufficient_observations_skipped(self) -> None:
        """T < 2 → skipped。"""
        result = evaluate_haircut_sharpe(
            observed_sharpe=1.0,
            n_trials=1,
            n_observations=1,
        )
        assert result["status"] == "skipped"
        assert result["skipped"] is True


class TestMergeAndDiagnostics:
    """ADR-022 辅助函数：合并门 / 日收益矩 / diagnostic_to_dict。"""

    def test_evaluate_merge_gate_first_candidate(self) -> None:
        """尚无 current best 时合并门放行并建立基线。"""
        result = evaluate_merge_gate(0.6, None)
        assert result["passed"] is True
        assert result["current_best"] is None
        assert result["threshold"] == MERGE_THRESHOLD_DEFAULT
        assert "建立基线" in result["reason"]

    def test_evaluate_merge_gate_beats_current_best(self) -> None:
        """OOS mean 超过 best+threshold 时通过。"""
        result = evaluate_merge_gate(
            0.70,
            0.50,
            threshold=MERGE_THRESHOLD_DEFAULT,
        )
        assert result["passed"] is True
        assert ">" in result["reason"]

    def test_evaluate_merge_gate_below_threshold(self) -> None:
        """OOS mean 未超过 best+threshold 时拒绝。"""
        result = evaluate_merge_gate(0.52, 0.50, threshold=MERGE_THRESHOLD_DEFAULT)
        assert result["passed"] is False

    def test_daily_return_moments_from_floats(self) -> None:
        """足够样本的日收益序列应返回 (skew, kurtosis)。"""
        returns = [0.01, -0.005, 0.002, 0.003, -0.001, 0.004, 0.0, -0.002]
        moments = daily_return_moments(returns)
        assert moments is not None
        skew, kurt = moments
        assert isinstance(skew, float)
        assert isinstance(kurt, float)

    def test_daily_return_moments_insufficient_sample(self) -> None:
        """样本不足时返回 None。"""
        assert daily_return_moments([0.01, 0.02]) is None
        assert daily_return_moments(None) is None

    def test_diagnostic_to_dict_shape(self) -> None:
        """diagnostic_to_dict 统一 status/passed/skipped 字段。"""
        payload = diagnostic_to_dict(
            status="failed",
            reason="test",
            simplified=True,
            t_statistic=1.0,
        )
        assert payload["status"] == "failed"
        assert payload["passed"] is False
        assert payload["skipped"] is False
        assert payload["simplified"] is True
        assert payload["t_statistic"] == 1.0
