"""统计过拟合门（ADR-015）。

三道统计门，按调用顺序：

1. ``WalkForwardStabilityGate`` —— Walk-Forward 折间稳定性。
   防单策略窗口不一致（Q1/Q2 极度不一致）。
2. ``DeflatedSharpeGate`` —— Deflated Sharpe Ratio（Bailey & López de Prado 2014）。
   multiple testing 校正，防 N 轮 HTR 中"取最优"的 selection bias。
3. ``BacktestOverfitGate`` —— PBO 概率（Bailey et al. 2017 CSCV）。
   多策略集合过拟合概率检验。

三道门互补：

- S1 防单策略窗口不稳定（worst fold / std / 一致性）
- S2 防单策略 sharpe 不显著（multiple testing）
- S3 防多策略集合 selection bias

接入点：``htr_subgraph._evaluate_oos_and_merge``，S1/S2 串联在 OOS 平均 sharpe
通过后追加调用；S3 需要历史候选 sharpe 列表，在 HTR _decide_node 中调用。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# S1: Walk-Forward 稳定性门
# ---------------------------------------------------------------------------

# 默认阈值（经验值，可配置）
_WORST_FOLD_SHARPE_FLOOR = -0.1  # 最差折允许微小负值（容忍噪声）
_FOLD_SHARPE_STD_CEILING = 0.8  # 折间 sharpe 标准差上限
_CONSISTENCY_RATIO_FLOOR = 0.67  # 正 sharpe 折数占比下限（2/3）


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
    """Walk-Forward 折间稳定性门（ADR-015 S1）。

    检查 Walk-Forward OOS 各折 sharpe 的分布稳定性，拒绝窗口不一致的策略。
    三道硬性条件，任一不满足即拒绝：

    1. ``min(sharpes) > -0.1``：最差折允许微小负值（容忍噪声）
    2. ``std(sharpes) < 0.8``：折间方差过大说明策略对特定窗口敏感
    3. ``sum(s > 0) / n >= 0.67``：正 sharpe 折数占比 ≥ 2/3（一致性）

    Args:
        worst_fold_floor: 最差折 sharpe 下限（默认 -0.1）。
        std_ceiling: 折间 sharpe 标准差上限（默认 0.8）。
        consistency_floor: 正 sharpe 折数占比下限（默认 0.67）。
    """

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
        """评估 Walk-Forward 折间稳定性。

        Args:
            fold_results: ``run_oos`` 返回的 ``fold_results`` 列表，每个元素
                形如 ``{"fold_id": int, "train": {...}, "test": {sharpe_ratio: float}}``。

        Returns:
            StabilityResult：``passed=False`` 时 ``reason`` 说明哪个条件未满足。
        """
        sharpes = _extract_fold_sharpes(fold_results)

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
        # 样本标准差（n-1 分母）。n=1 时 std=0（单折无法判断稳定性，放行）
        std = float(_safe_std(sharpes))
        positive_count = sum(1 for s in sharpes if s > 0)
        consistency = positive_count / n

        # 三道硬性条件
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
# S2: Deflated Sharpe Ratio 门
# ---------------------------------------------------------------------------

# 95% 置信（单边检验）
_DSR_THRESHOLD_DEFAULT = 1.96
# DSR 所需最少回测天数
_DSR_MIN_OBSERVATIONS = 2
# PBO 劣于中位数判定阈值（rank < 0.5 视为劣于中位数）
_PBO_BELOW_MEDIAN_THRESHOLD = 0.5
# CSCV 枚举上限（n > 10 时 math.comb 过大，转纯 Monte Carlo）
_CSCV_ENUM_LIMIT = 10


@dataclass
class DeflatedSharpeResult:
    """DSR 检验结论。"""

    passed: bool
    reason: str
    t_statistic: float
    expected_max_noise: float
    sr_standard_error: float


class DeflatedSharpeGate:
    """Deflated Sharpe Ratio 门（ADR-015 S2）。

    实现 Bailey & López de Prado (2014) DSR 的简化版——只做 multiple testing
    校正（``E[max_N]`` 项），不含 skew/kurt 校正（OOS fold_results 不含日收益序列，
    无法估计偏度峰度）。

    公式：

    ```
    SR_se = 1 / sqrt(T)                              # T = 回测天数
    E[max_N] = sqrt(2 * ln(N))                       # N = 多重检验次数
    t_stat = (SR_observed - E[max_N] * SR_se) / SR_se
    ```

    ``t_stat > threshold`` 才通过——即观测 sharpe 在扣除"取 N 次最优的噪声期望"
    后仍然显著大于 0。

    Args:
        threshold: t 统计量阈值（默认 1.96，即 95% 单边置信）。
    """

    def __init__(self, threshold: float = _DSR_THRESHOLD_DEFAULT) -> None:
        self.threshold = threshold

    def evaluate(
        self,
        observed_sharpe: float,
        n_trials: int,
        n_observations: int,
    ) -> DeflatedSharpeResult:
        """评估 Deflated Sharpe Ratio。

        Args:
            observed_sharpe: 观测到的 OOS sharpe（跨折平均）。
            n_trials: HTR 累积尝试的策略数（多重检验次数）。
            n_observations: 回测天数（用于估计 sharpe 标准误）。

        Returns:
            DeflatedSharpeResult：``passed=False`` 时说明该 sharpe 在多重检验
            校正后不显著。
        """
        if n_observations < _DSR_MIN_OBSERVATIONS:
            return DeflatedSharpeResult(
                passed=False,
                reason=f"回测天数不足（n={n_observations} < 2）",
                t_statistic=0.0,
                expected_max_noise=0.0,
                sr_standard_error=0.0,
            )

        # SR 标准误（假设 i.i.d. 正态收益，Bailey 2014 公式简化版）
        sr_se = 1.0 / math.sqrt(n_observations)

        # 多重检验校正：N 个独立标准正态噪声的最大期望
        # E[max_N] ≈ sqrt(2 * ln(N))，N <= 1 时无校正
        n = max(n_trials, 1)
        expected_max_noise = math.sqrt(2.0 * math.log(n)) if n > 1 else 0.0

        # Deflated t-statistic
        t_stat = (observed_sharpe - expected_max_noise * sr_se) / sr_se

        passed = t_stat > self.threshold
        if passed:
            reason = (
                f"通过（t={t_stat:.2f} > {self.threshold}，"
                f"N={n_trials}, T={n_observations}）"
            )
        else:
            reason = (
                f"多重检验校正后不显著（t={t_stat:.2f} <= {self.threshold}，"
                f"N={n_trials}, T={n_observations}，"
                f"E[max_N]={expected_max_noise:.3f}）"
            )

        return DeflatedSharpeResult(
            passed=passed,
            reason=reason,
            t_statistic=t_stat,
            expected_max_noise=expected_max_noise,
            sr_standard_error=sr_se,
        )


# ---------------------------------------------------------------------------
# S3: PBO 概率门（CSCV）
# ---------------------------------------------------------------------------

# PBO 拒绝阈值（过拟合概率超过 50% 即拒绝）
_PBO_THRESHOLD_DEFAULT = 0.5
# CSCV 默认采样数（C(2N, N) 可能巨大，Monte Carlo 采样）
_CSCV_N_SAMPLES_DEFAULT = 1000
# PBO 计算所需的最少策略数（< 2 无法计算）
_MIN_STRATEGIES_FOR_PBO = 2


@dataclass
class PBOResult:
    """PBO 检验结论。"""

    passed: bool
    reason: str
    pbo_probability: float
    n_strategies: int
    n_samples: int


class BacktestOverfitGate:
    """PBO 概率门（ADR-015 S3）。

    实现 Bailey et al. (2017) 的 Combinatorial Symmetric Cross-Validation (CSCV)
    采样版——把 IS/OOS sharpe 配对，对称重排组合，计算"IS 最优策略在 OOS 表现
    劣于中位数"的概率。

    ``PBO > 0.5`` 拒绝（过拟合概率超过 50%）。

    Args:
        threshold: PBO 拒绝阈值（默认 0.5）。
        n_samples: CSCV Monte Carlo 采样数（默认 1000）。
    """

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
        """评估 PBO 概率。

        Args:
            is_sharpes: N 个策略在训练集的 sharpe 列表。
            oos_sharpes: N 个策略在测试集的 sharpe 列表（与 is_sharpes 一一对应）。

        Returns:
            PBOResult：``passed=False`` 时说明这组策略集合存在过拟合嫌疑。
        """
        n = len(is_sharpes)
        if n < _MIN_STRATEGIES_FOR_PBO or len(oos_sharpes) != n:
            return PBOResult(
                passed=True,  # 样本不足时不阻塞，由 S1/S2 把关
                reason=f"策略数不足（n={n} < {_MIN_STRATEGIES_FOR_PBO}），跳过 PBO 检验",
                pbo_probability=0.0,
                n_strategies=n,
                n_samples=0,
            )

        pbo = _compute_pbo_cscv(is_sharpes, oos_sharpes, self.n_samples)
        passed = pbo <= self.threshold
        if passed:
            reason = (
                f"通过（PBO={pbo:.3f} <= {self.threshold}，"
                f"N={n}, samples={self.n_samples}）"
            )
        else:
            reason = (
                f"过拟合概率过高（PBO={pbo:.3f} > {self.threshold}，"
                f"N={n}, samples={self.n_samples}）"
            )

        return PBOResult(
            passed=passed,
            reason=reason,
            pbo_probability=pbo,
            n_strategies=n,
            n_samples=self.n_samples,
        )


def _compute_pbo_cscv(
    is_sharpes: list[float],
    oos_sharpes: list[float],
    n_samples: int,
) -> float:
    """CSCV 采样计算 PBO 概率。

    算法（Bailey et al. 2017 简化版）：

    1. 每个策略有 IS/OOS 两个 sharpe（共 2N 个观测）
    2. 采样 S 个随机切分，每次把 2N 个观测随机分 N 个为 IS、N 个为 OOS
    3. 对每次切分：找 IS 最优的观测，看它在原配对的 OOS 中是否处于下半区
    4. PBO = 处于下半区的次数 / S

    严格 CSCV 跟踪 strategy identity（IS 最优观测对应的策略 ID）。
    本实现用 rank 近似：若 IS 最优观测在 OOS 中的对应 sharpe 低于 OOS 中位数，
    则计为一次过拟合。

    Args:
        is_sharpes: N 个策略的训练集 sharpe
        oos_sharpes: N 个策略的测试集 sharpe（一一对应）
        n_samples: Monte Carlo 采样次数
    """
    n = len(is_sharpes)
    # 配对：(strategy_id, is_sharpe, oos_sharpe)
    pairs = [(i, is_sharpes[i], oos_sharpes[i]) for i in range(n)]

    # 2N 个观测，每个标注 (strategy_id, value, is_is)
    observations: list[tuple[int, float, bool]] = []
    for sid, is_v, oos_v in pairs:
        observations.append((sid, is_v, True))
        observations.append((sid, oos_v, False))

    rng = random.Random(42)  # 固定种子，可复现
    below_median_count = 0
    actual_samples = (
        min(n_samples, math.comb(2 * n, n)) if n <= _CSCV_ENUM_LIMIT else n_samples
    )

    for _ in range(actual_samples):
        # 随机切分 2N 个观测为 IS 组 / OOS 组
        indices = list(range(2 * n))
        rng.shuffle(indices)
        is_group_idx = indices[:n]

        # 提取 IS 组的观测
        is_obs = [observations[i] for i in is_group_idx]
        # 找 IS 最优观测及其 strategy_id
        is_best = max(is_obs, key=lambda x: x[1])
        is_best_sid = is_best[0]

        # 该策略在 OOS 组中的 sharpe（若被选入 IS 组则取其 IS 值的配对 OOS）
        # CSCV 严格定义：IS 最优策略在另一半切分（OOS 组）中的表现
        # 若该策略的某个观测在 IS 组，则其另一个观测必在 OOS 组
        oos_counterpart = None
        for i in range(2 * n):
            if i in is_group_idx:
                continue
            obs = observations[i]
            if obs[0] == is_best_sid:
                oos_counterpart = obs[1]
                break

        if oos_counterpart is None:
            continue  # 理论上不会发生

        # 收集 OOS 组所有 sharpe
        oos_group_obs = [
            observations[i][1] for i in range(2 * n) if i not in is_group_idx
        ]
        if not oos_group_obs:
            continue

        # IS 最优策略在 OOS 组中的 rank
        oos_median_val = _median(oos_group_obs)
        if oos_counterpart < oos_median_val:
            below_median_count += 1

    return below_median_count / actual_samples if actual_samples > 0 else 0.0


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _extract_fold_sharpes(fold_results: list[dict[str, Any]]) -> list[float]:
    """从 fold_results 提取每折 test sharpe。

    兼容 ParallelRunner（``fold.test.sharpe_ratio``）和旧 core.py
    （``fold.test_metrics.sharpe_ratio``）两种格式。
    """
    sharpes: list[float] = []
    for f in fold_results:
        if not isinstance(f, dict):
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
