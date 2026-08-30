"""统计验证门控（ADR-022 实现）。

用法分层（ADR-022）：

- **硬性门控（hard gate）**：Walk-Forward 稳定性 + held-out 相对 current best
- **诊断门控（diagnostic gate）**：DSR、PBO —— 必须显式 ``passed`` / ``failed`` /
  ``skipped``；缺料时 ``skipped``，不得静默视为通过

实现仍按 S1/S2/S3 编号；接入点以 ToG ``ResearchAgent.run_oos_gates`` 为准。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Literal

DiagnosticStatus = Literal["passed", "failed", "skipped"]

# ---------------------------------------------------------------------------
# S1: Walk-Forward 稳定性门（硬性门控）
# ---------------------------------------------------------------------------

_WORST_FOLD_SHARPE_FLOOR = -0.1
_FOLD_SHARPE_STD_CEILING = 0.8
_CONSISTENCY_RATIO_FLOOR = 0.67


@dataclass
class StabilityResult:
    """Walk-Forward 稳定性检验结论。"""

    passed: bool
    reason: str
    worst_fold_sharpe: float
    fold_sharpe_std: float
    consistency_ratio: float
    fold_sharpes: list[float]


class WalkForwardStabilityGate:
    """Walk-Forward 折间稳定性门（硬性门控）。"""

    def __init__(
        self,
        worst_fold_floor: float = _WORST_FOLD_SHARPE_FLOOR,
        std_ceiling: float = _FOLD_SHARPE_STD_CEILING,
        consistency_floor: float = _CONSISTENCY_RATIO_FLOOR,
    ) -> None:
        self.worst_fold_floor = worst_fold_floor
        self.std_ceiling = std_ceiling
        self.consistency_floor = consistency_floor

    def evaluate(self, fold_results: list[dict[str, Any]]) -> StabilityResult:
        """评估 Walk-Forward 折间稳定性。"""
        n_folds = len(fold_results)
        sharpes = _extract_fold_sharpes(fold_results)

        if n_folds > 0 and len(sharpes) < n_folds:
            return StabilityResult(
                passed=False,
                reason=(f"有效折数不足（{len(sharpes)}/{n_folds} 折有 test sharpe）"),
                worst_fold_sharpe=min(sharpes) if sharpes else 0.0,
                fold_sharpe_std=float(_safe_std(sharpes)),
                consistency_ratio=(
                    sum(1 for s in sharpes if s > 0) / len(sharpes) if sharpes else 0.0
                ),
                fold_sharpes=sharpes,
            )

        if not sharpes:
            return StabilityResult(
                passed=False,
                reason="无有效 fold sharpe 数据",
                worst_fold_sharpe=0.0,
                fold_sharpe_std=0.0,
                consistency_ratio=0.0,
                fold_sharpes=[],
            )

        n = len(sharpes)
        worst = min(sharpes)
        std = float(_safe_std(sharpes))
        positive_count = sum(1 for s in sharpes if s > 0)
        consistency = positive_count / n

        if worst <= self.worst_fold_floor:
            reason = (
                f"最差折 sharpe={worst:.2f} <= {self.worst_fold_floor}"
                "（窗口极度不一致）"
            )
            passed = False
        elif std > self.std_ceiling:
            reason = (
                f"折间 sharpe 标准差={std:.2f} > {self.std_ceiling}"
                "（方差过大，策略对特定窗口敏感）"
            )
            passed = False
        elif consistency < self.consistency_floor:
            reason = (
                f"正 sharpe 折数占比={consistency:.2f} < {self.consistency_floor}"
                "（一致性不足 2/3）"
            )
            passed = False
        else:
            reason = (
                f"通过（worst={worst:.2f}, std={std:.2f}, "
                f"consistency={consistency:.2f}）"
            )
            passed = True

        return StabilityResult(
            passed=passed,
            reason=reason,
            worst_fold_sharpe=worst,
            fold_sharpe_std=std,
            consistency_ratio=consistency,
            fold_sharpes=sharpes,
        )


# ---------------------------------------------------------------------------
# S2: Deflated Sharpe Ratio（诊断门控）
# ---------------------------------------------------------------------------

_DSR_THRESHOLD_DEFAULT = 1.96
_DSR_MIN_OBSERVATIONS = 2
_CSCV_ENUM_LIMIT = 10
# Pearson 峰度：正态 = 3.0
_NORMAL_KURTOSIS = 3.0


@dataclass
class DeflatedSharpeResult:
    """DSR 检验结论（诊断门控）。"""

    status: DiagnosticStatus
    reason: str
    t_statistic: float
    expected_max_noise: float
    sr_standard_error: float
    simplified: bool = True

    @property
    def passed(self) -> bool:
        """兼容旧调用：仅 status==passed 为 True。"""
        return self.status == "passed"

    @property
    def skipped(self) -> bool:
        return self.status == "skipped"


class DeflatedSharpeGate:
    """Deflated Sharpe Ratio 诊断门（Bailey & López de Prado 2014）。

    - 缺观测数等必要输入 → ``skipped``（不得静默当通过）
    - 未提供 skew/kurt → ``simplified=True`` 的简化版
    - 提供 skew/kurt → 完整 PSR 分母校正
    """

    def __init__(self, threshold: float = _DSR_THRESHOLD_DEFAULT) -> None:
        self.threshold = threshold

    def evaluate(
        self,
        observed_sharpe: float,
        n_trials: int,
        n_observations: int,
        *,
        skew: float | None = None,
        kurtosis: float | None = None,
    ) -> DeflatedSharpeResult:
        """评估 Deflated Sharpe Ratio。

        Args:
            observed_sharpe: 观测 OOS sharpe。
            n_trials: 有效试验数（优先 N_eff）。
            n_observations: 回测天数。
            skew: 日收益偏度；None 则走简化版。
            kurtosis: 日收益 Pearson 峰度（正态=3）；None 则走简化版。
        """
        if n_observations < _DSR_MIN_OBSERVATIONS:
            return DeflatedSharpeResult(
                status="skipped",
                reason=f"回测天数不足（n={n_observations} < 2），跳过 DSR",
                t_statistic=0.0,
                expected_max_noise=0.0,
                sr_standard_error=0.0,
                simplified=True,
            )

        simplified = skew is None or kurtosis is None
        sr_se = 1.0 / math.sqrt(n_observations)

        if not simplified:
            # Bailey 2014：非正态下 SR 方差放大
            assert skew is not None and kurtosis is not None
            denom = (
                1.0
                - skew * observed_sharpe
                + ((kurtosis - 1.0) / 4.0) * observed_sharpe**2
            )
            if denom <= 0:
                return DeflatedSharpeResult(
                    status="skipped",
                    reason=(
                        f"非正态校正分母非正（skew={skew:.3f}, "
                        f"kurt={kurtosis:.3f}），跳过 DSR"
                    ),
                    t_statistic=0.0,
                    expected_max_noise=0.0,
                    sr_standard_error=sr_se,
                    simplified=False,
                )
            sr_se = sr_se * math.sqrt(denom)

        n = max(n_trials, 1)
        expected_max_noise = math.sqrt(2.0 * math.log(n)) if n > 1 else 0.0
        t_stat = (observed_sharpe - expected_max_noise * sr_se) / sr_se

        if t_stat > self.threshold:
            status: DiagnosticStatus = "passed"
            reason = (
                f"通过（t={t_stat:.2f} > {self.threshold}，"
                f"N={n_trials}, T={n_observations}, simplified={simplified}）"
            )
        else:
            status = "failed"
            reason = (
                f"多重检验校正后不显著（t={t_stat:.2f} <= {self.threshold}，"
                f"N={n_trials}, T={n_observations}, "
                f"E[max_N]={expected_max_noise:.3f}, simplified={simplified}）"
            )

        return DeflatedSharpeResult(
            status=status,
            reason=reason,
            t_statistic=t_stat,
            expected_max_noise=expected_max_noise,
            sr_standard_error=sr_se,
            simplified=simplified,
        )


# ---------------------------------------------------------------------------
# S3: PBO（诊断门控）
# ---------------------------------------------------------------------------

_PBO_THRESHOLD_DEFAULT = 0.5
_CSCV_N_SAMPLES_DEFAULT = 1000
_MIN_STRATEGIES_FOR_PBO = 2
_MIN_BLOCKS_FOR_MATRIX_CSCV = 2
_SHARPE_EPS = 1e-12
_Z_ALPHA_95 = 1.96

# held-out 合并相对提升阈值（与历史 HTR 默认对齐）
MERGE_THRESHOLD_DEFAULT = 0.05


@dataclass
class PBOResult:
    """PBO 检验结论（诊断门控）。"""

    status: DiagnosticStatus
    reason: str
    pbo_probability: float
    n_strategies: int
    n_samples: int

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    @property
    def skipped(self) -> bool:
        return self.status == "skipped"


class BacktestOverfitGate:
    """PBO 诊断门（Bailey et al. 2017 CSCV）。"""

    def __init__(
        self,
        threshold: float = _PBO_THRESHOLD_DEFAULT,
        n_samples: int = _CSCV_N_SAMPLES_DEFAULT,
    ) -> None:
        self.threshold = threshold
        self.n_samples = n_samples

    def evaluate(
        self,
        is_sharpes: list[float],
        oos_sharpes: list[float],
    ) -> PBOResult:
        """评估 PBO；策略数不足或长度不一致时 ``skipped``。"""
        n = len(is_sharpes)
        if n < _MIN_STRATEGIES_FOR_PBO or len(oos_sharpes) != n:
            return PBOResult(
                status="skipped",
                reason=(
                    f"候选矩阵不足（n={n}, oos_len={len(oos_sharpes)}，"
                    f"需 >= {_MIN_STRATEGIES_FOR_PBO} 且等长），跳过 PBO"
                ),
                pbo_probability=0.0,
                n_strategies=n,
                n_samples=0,
            )

        pbo = _compute_pbo_cscv(is_sharpes, oos_sharpes, self.n_samples)
        if pbo <= self.threshold:
            status: DiagnosticStatus = "passed"
            reason = (
                f"通过（PBO={pbo:.3f} <= {self.threshold}，"
                f"N={n}, samples={self.n_samples}, method=pair_legacy）"
            )
        else:
            status = "failed"
            reason = (
                f"过拟合概率过高（PBO={pbo:.3f} > {self.threshold}，"
                f"N={n}, samples={self.n_samples}, method=pair_legacy）"
            )

        return PBOResult(
            status=status,
            reason=reason,
            pbo_probability=pbo,
            n_strategies=n,
            n_samples=self.n_samples,
        )

    def evaluate_returns_matrix(
        self,
        returns_matrix: list[list[float]],
        *,
        n_blocks: int = 8,
    ) -> PBOResult:
        """标准 CSCV PBO（T×N 收益矩阵，行=时间、列=策略）。"""
        if not returns_matrix:
            return _pbo_matrix_skipped("收益矩阵为空", n_strategies=0)

        n_cols = len(returns_matrix[0])
        if any(len(row) != n_cols for row in returns_matrix):
            return _pbo_matrix_skipped(
                "收益矩阵列数不一致",
                n_strategies=n_cols,
            )

        t_rows = len(returns_matrix)
        if n_cols < _MIN_STRATEGIES_FOR_PBO:
            return _pbo_matrix_skipped(
                f"策略列不足（N={n_cols} < {_MIN_STRATEGIES_FOR_PBO}）",
                n_strategies=n_cols,
            )
        if t_rows < _MIN_BLOCKS_FOR_MATRIX_CSCV * n_blocks:
            return _pbo_matrix_skipped(
                f"时间行不足（T={t_rows} < {2 * n_blocks}）",
                n_strategies=n_cols,
            )
        if n_blocks % 2 != 0:
            return _pbo_matrix_skipped(
                f"n_blocks 须为偶数（got {n_blocks}）",
                n_strategies=n_cols,
            )

        block_size = t_rows // n_blocks
        usable_t = block_size * n_blocks
        trimmed = [row[:n_cols] for row in returns_matrix[:usable_t]]

        blocks: list[list[list[float]]] = []
        for b in range(n_blocks):
            start = b * block_size
            end = start + block_size
            blocks.append([row[:] for row in trimmed[start:end]])

        half = n_blocks // 2
        all_combos = list(combinations(range(n_blocks), half))
        rng = random.Random(42)
        if len(all_combos) > self.n_samples:
            sampled_combos = rng.sample(all_combos, self.n_samples)
            actual_samples = self.n_samples
        else:
            sampled_combos = all_combos
            actual_samples = len(all_combos)

        below_median_count = 0
        for combo in sampled_combos:
            is_set = set(combo)
            is_rows = [
                row for i, blk in enumerate(blocks) if i in is_set for row in blk
            ]
            oos_rows = [
                row for i, blk in enumerate(blocks) if i not in is_set for row in blk
            ]
            is_sharpes = [
                _column_sharpe(_col_values(is_rows, c)) for c in range(n_cols)
            ]
            oos_sharpes = [
                _column_sharpe(_col_values(oos_rows, c)) for c in range(n_cols)
            ]
            best_is_col = max(range(n_cols), key=lambda c: is_sharpes[c])
            oos_median = _median(oos_sharpes)
            if oos_sharpes[best_is_col] < oos_median:
                below_median_count += 1

        pbo = below_median_count / actual_samples if actual_samples > 0 else 0.0

        if pbo <= self.threshold:
            status: DiagnosticStatus = "passed"
            reason = (
                f"通过（PBO={pbo:.3f} <= {self.threshold}，"
                f"T={usable_t}, N={n_cols}, blocks={n_blocks}, "
                f"samples={actual_samples}, method=cscv_matrix）"
            )
        else:
            status = "failed"
            reason = (
                f"过拟合概率过高（PBO={pbo:.3f} > {self.threshold}，"
                f"T={usable_t}, N={n_cols}, blocks={n_blocks}, "
                f"samples={actual_samples}, method=cscv_matrix）"
            )

        return PBOResult(
            status=status,
            reason=reason,
            pbo_probability=pbo,
            n_strategies=n_cols,
            n_samples=actual_samples,
        )


def evaluate_mintrl(
    observed_sharpe: float,
    n_observations: int,
    *,
    skew: float = 0.0,
    kurtosis: float = 3.0,
    confidence: float = 0.95,
) -> dict[str, Any]:
    """MinTRL 诊断（Bailey；相对 SR*=0，不硬拒）。"""
    if n_observations < 1:
        return diagnostic_to_dict(
            status="skipped",
            reason="观测数不足，跳过 MinTRL",
            simplified=True,
            mintrl=0.0,
            n_observations=n_observations,
            observed_sharpe=observed_sharpe,
        )

    sr = observed_sharpe
    if abs(sr) < _SHARPE_EPS:
        return diagnostic_to_dict(
            status="skipped",
            reason=f"Sharpe 近零（|SR|={abs(sr):.2e}），跳过 MinTRL",
            simplified=skew == 0.0 and kurtosis == _NORMAL_KURTOSIS,
            mintrl=0.0,
            n_observations=n_observations,
            observed_sharpe=observed_sharpe,
        )

    z_alpha = _Z_ALPHA_95
    variance_factor = 1.0 - skew * sr + ((kurtosis - 1.0) / 4.0) * sr**2
    mintrl = 1.0 + variance_factor * (z_alpha / sr) ** 2

    if mintrl < 1.0:
        return diagnostic_to_dict(
            status="skipped",
            reason=(
                f"MinTRL 非正（mintrl={mintrl:.3f}，skew={skew:.3f}，"
                f"kurt={kurtosis:.3f}），跳过"
            ),
            simplified=skew == 0.0 and kurtosis == _NORMAL_KURTOSIS,
            mintrl=mintrl,
            n_observations=n_observations,
            observed_sharpe=observed_sharpe,
        )

    if n_observations >= mintrl:
        status: DiagnosticStatus = "passed"
        reason = (
            f"通过（T={n_observations} >= mintrl={mintrl:.1f}，"
            f"SR={sr:.3f}, confidence={confidence}）"
        )
    else:
        status = "failed"
        reason = (
            f"样本不足（T={n_observations} < mintrl={mintrl:.1f}，"
            f"SR={sr:.3f}, confidence={confidence}）"
        )

    return diagnostic_to_dict(
        status=status,
        reason=reason,
        simplified=skew == 0.0 and kurtosis == _NORMAL_KURTOSIS,
        mintrl=mintrl,
        n_observations=n_observations,
        observed_sharpe=observed_sharpe,
        confidence=confidence,
    )


def evaluate_haircut_sharpe(
    observed_sharpe: float,
    n_trials: int,
    n_observations: int,
    *,
    alpha: float = 0.05,
) -> dict[str, Any]:
    """Haircut Sharpe 诊断（Bonferroni / 期望最大噪声折减，不硬拒）。"""
    if n_observations < _DSR_MIN_OBSERVATIONS:
        return diagnostic_to_dict(
            status="skipped",
            reason=f"回测天数不足（n={n_observations} < 2），跳过 haircut",
            simplified=True,
            haircut_sharpe=0.0,
            observed_sharpe=observed_sharpe,
            n_trials=n_trials,
            n_observations=n_observations,
            alpha=alpha,
            method="expected_max_noise",
        )

    se = 1.0 / math.sqrt(n_observations)
    n = max(n_trials, 1)
    noise_penalty = math.sqrt(2.0 * math.log(n)) * se
    haircut = observed_sharpe - noise_penalty

    if haircut > 0:
        status: DiagnosticStatus = "passed"
        reason = (
            f"通过（haircut={haircut:.4f} > 0，SR={observed_sharpe:.3f}，"
            f"N={n_trials}, T={n_observations}, method=expected_max_noise）"
        )
    else:
        status = "failed"
        reason = (
            f"折减后 Sharpe 非正（haircut={haircut:.4f} <= 0，"
            f"SR={observed_sharpe:.3f}，N={n_trials}, T={n_observations}, "
            f"method=expected_max_noise）"
        )

    return diagnostic_to_dict(
        status=status,
        reason=reason,
        simplified=True,
        haircut_sharpe=haircut,
        observed_sharpe=observed_sharpe,
        n_trials=n_trials,
        n_observations=n_observations,
        sr_standard_error=se,
        noise_penalty=noise_penalty,
        alpha=alpha,
        method="expected_max_noise",
    )


def diagnostic_to_dict(
    *,
    status: DiagnosticStatus,
    reason: str,
    simplified: bool | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """统一诊断门 JSON 片段。"""
    out: dict[str, Any] = {
        "status": status,
        "passed": status == "passed",
        "skipped": status == "skipped",
        "reason": reason,
    }
    if simplified is not None:
        out["simplified"] = simplified
    out.update(extra)
    return out


def mean_fold_sharpe(fold_results: list[dict[str, Any]]) -> float | None:
    """跨折平均 test sharpe；无数据返回 None。"""
    sharpes = _extract_fold_sharpes(fold_results)
    if not sharpes:
        return None
    return sum(sharpes) / len(sharpes)


def evaluate_merge_gate(
    oos_mean_sharpe: float,
    current_best: float | None,
    *,
    threshold: float = MERGE_THRESHOLD_DEFAULT,
) -> dict[str, Any]:
    """held-out 相对 current best 的合并硬性门控。"""
    baseline = current_best if current_best is not None else float("-inf")
    needed = baseline + threshold if current_best is not None else float("-inf")
    passed = oos_mean_sharpe > needed if current_best is not None else True
    if current_best is None:
        reason = (
            f"尚无 current best，以 OOS mean sharpe={oos_mean_sharpe:.4f} 建立基线候选"
        )
        passed = True
    elif passed:
        reason = f"OOS mean={oos_mean_sharpe:.4f} > best={current_best:.4f}+{threshold}"
    else:
        reason = (
            f"OOS mean={oos_mean_sharpe:.4f} <= best={current_best:.4f}+{threshold}"
        )
    return {
        "passed": passed,
        "reason": reason,
        "oos_mean_sharpe": oos_mean_sharpe,
        "current_best": current_best,
        "threshold": threshold,
    }


_MIN_RETURNS_FOR_MOMENTS = 8
_MOMENT_VAR_EPS = 1e-18


def daily_return_moments(
    daily_returns: list[dict[str, Any]] | list[float] | None,
) -> tuple[float, float] | None:
    """从日收益序列估计 (skew, Pearson kurtosis)；样本不足返回 None。"""
    if not daily_returns:
        return None
    values: list[float] = []
    for item in daily_returns:
        if isinstance(item, (int, float)):
            values.append(float(item))
        elif isinstance(item, dict) and "value" in item:
            try:
                values.append(float(item["value"]))
            except (TypeError, ValueError):
                continue
    if len(values) < _MIN_RETURNS_FOR_MOMENTS:
        return None
    n = len(values)
    mean = sum(values) / n
    centered = [x - mean for x in values]
    m2 = sum(x * x for x in centered) / n
    if m2 <= _MOMENT_VAR_EPS:
        return None
    m3 = sum(x**3 for x in centered) / n
    m4 = sum(x**4 for x in centered) / n
    skew = m3 / (m2**1.5)
    kurt = m4 / (m2 * m2)  # Pearson
    return skew, kurt


def _pbo_matrix_skipped(reason: str, *, n_strategies: int) -> PBOResult:
    return PBOResult(
        status="skipped",
        reason=f"{reason}，跳过 PBO（method=cscv_matrix）",
        pbo_probability=0.0,
        n_strategies=n_strategies,
        n_samples=0,
    )


def _col_values(matrix_rows: list[list[float]], col: int) -> list[float]:
    return [row[col] for row in matrix_rows]


def _column_sharpe(returns: list[float]) -> float:
    """列收益 Sharpe（mean/std）；std=0 时返回 -inf。"""
    if not returns:
        return float("-inf")
    mean = sum(returns) / len(returns)
    if len(returns) < _DSR_MIN_OBSERVATIONS:
        return float("-inf")
    std = _population_std(returns)
    if std <= _SHARPE_EPS:
        return float("-inf")
    return mean / std


def _population_std(values: list[float]) -> float:
    n = len(values)
    if n < 1:
        return 0.0
    mean = sum(values) / n
    var = sum((x - mean) ** 2 for x in values) / n
    return math.sqrt(var)


def _compute_pbo_cscv(
    is_sharpes: list[float],
    oos_sharpes: list[float],
    n_samples: int,
) -> float:
    """CSCV 采样计算 PBO 概率。"""
    n = len(is_sharpes)
    pairs = [(i, is_sharpes[i], oos_sharpes[i]) for i in range(n)]
    observations: list[tuple[int, float, bool]] = []
    for sid, is_v, oos_v in pairs:
        observations.append((sid, is_v, True))
        observations.append((sid, oos_v, False))

    rng = random.Random(42)
    below_median_count = 0
    actual_samples = (
        min(n_samples, math.comb(2 * n, n)) if n <= _CSCV_ENUM_LIMIT else n_samples
    )

    for _ in range(actual_samples):
        indices = list(range(2 * n))
        rng.shuffle(indices)
        is_group_idx = set(indices[:n])
        is_obs = [observations[i] for i in is_group_idx]
        is_best = max(is_obs, key=lambda x: x[1])
        is_best_sid = is_best[0]

        oos_counterpart = None
        for i in range(2 * n):
            if i in is_group_idx:
                continue
            obs = observations[i]
            if obs[0] == is_best_sid:
                oos_counterpart = obs[1]
                break
        if oos_counterpart is None:
            continue

        oos_group_obs = [
            observations[i][1] for i in range(2 * n) if i not in is_group_idx
        ]
        if not oos_group_obs:
            continue
        if oos_counterpart < _median(oos_group_obs):
            below_median_count += 1

    return below_median_count / actual_samples if actual_samples > 0 else 0.0


def _extract_fold_sharpes(fold_results: list[dict[str, Any]]) -> list[float]:
    """从 fold_results 提取每折 test sharpe。"""
    sharpes: list[float] = []
    for f in fold_results:
        if not isinstance(f, dict):
            continue
        # 顶层 sharpe（部分测试夹具）
        top = f.get("sharpe_ratio")
        if isinstance(top, (int, float)):
            sharpes.append(float(top))
            continue
        test = f.get("test", {}) or f.get("test_metrics", {}) or {}
        val = test.get("sharpe_ratio")
        if val is not None and isinstance(val, (int, float)):
            sharpes.append(float(val))
    return sharpes


def _safe_std(values: list[float]) -> float:
    """样本标准差（n-1 分母），n < 2 时返回 0。"""
    n = len(values)
    if n < _DSR_MIN_OBSERVATIONS:
        return 0.0
    mean = sum(values) / n
    var = sum((x - mean) ** 2 for x in values) / (n - 1)
    return math.sqrt(var)


def _median(values: list[float]) -> float:
    """中位数。"""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0
