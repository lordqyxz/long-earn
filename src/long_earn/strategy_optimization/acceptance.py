"""策略优化验收门槛。

判定一次优化是否"真的更好"——防止 LLM 把策略改成业绩更差或退化的版本却被当成
改进接受。验收基于回测指标的客观比较，不依赖 LLM 自评。

规则（全部满足才 accept）：
1. 优化版回测无 error；
2. 优化版 ``metrics_unreliable`` 为 False（含退化、算子失败、引擎撮合异常）；
3. 主指标（默认 sharpe_ratio）**严格优于**基线；
4. 基线 sharpe 缺失时（HTR 首次循环 ``previous_backtest`` 为空）：接受任何有有效
   sharpe 的策略作为初始基线（即使为负 sharpe）。这是为了让 HTR 能建立初始基线
   供后续循环比较——若要求首次循环即正 sharpe，在弱势市场下 HTR 永远无法接受
   任何策略，整个研发循环空转。

金融严谨性：用夏普（风险调整收益）而非裸收益率做主判据，避免"高收益但超高波动"
的劣化被误判为改进。容差 ``eps`` 防止数值噪声导致误判。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class AcceptanceResult:
    """验收结论。"""

    accepted: bool
    reason: str
    baseline_sharpe: float | None
    optimized_sharpe: float | None
    baseline_return: float | None
    optimized_return: float | None


class AcceptanceGate:
    """优化验收门槛。

    Args:
        primary_metric: 主判据指标名（默认 ``sharpe_ratio``）。
        eps: 严格优于的最小增量（避免数值噪声）。
    """

    def __init__(
        self,
        primary_metric: str = "sharpe_ratio",
        eps: float = 1e-6,
    ) -> None:
        self.primary_metric = primary_metric
        self.eps = eps

    def evaluate(
        self,
        baseline_backtest: dict[str, Any] | None,
        optimized_backtest: dict[str, Any],
    ) -> AcceptanceResult:
        # 1) 优化版回测必须成功
        if optimized_backtest.get("error"):
            return AcceptanceResult(
                False,
                f"优化版回测失败: {optimized_backtest['error']}",
                None,
                None,
                None,
                None,
            )

        # 2) 优化版指标必须可信（退化 / 算子失败 / 引擎 skip 等）
        if is_metrics_unreliable(optimized_backtest):
            diag = optimized_backtest.get("strategy_diagnostics", {}) or {}
            if diag.get("degenerate"):
                reason = "优化版策略退化（无真实交易）"
            elif diag.get("failed_factor_aliases") or diag.get("failed_step_labels"):
                reason = "优化版回测算子链失败，指标不可信"
            elif diag.get("engine_metrics_unreliable") or optimized_backtest.get(
                "metrics_unreliable"
            ):
                reason = "优化版回测撮合异常（大量跳过/部分成交），指标不可信"
            else:
                reason = "优化版回测指标不可信"
            return AcceptanceResult(
                False,
                reason,
                None,
                None,
                None,
                None,
            )

        b_sharpe = _metric(baseline_backtest, "sharpe_ratio")
        o_sharpe = _metric(optimized_backtest, "sharpe_ratio")
        b_ret = _metric(baseline_backtest, "total_return")
        o_ret = _metric(optimized_backtest, "total_return")

        # 3) 主指标严格优于（双 sharpe 存在时走严格提升门）
        if b_sharpe is not None and o_sharpe is not None:
            accepted = o_sharpe > b_sharpe + self.eps
            reason = (
                "sharpe 严格提升"
                if accepted
                else f"sharpe 未提升（{b_sharpe} -> {o_sharpe}）"
            )
            return AcceptanceResult(accepted, reason, b_sharpe, o_sharpe, b_ret, o_ret)

        # 基线 sharpe 缺失（HTR 首次循环 previous_backtest 为空）：
        # 接受任何有有效 sharpe 的策略作为初始基线（即使为负 sharpe）。
        # 否则弱势市场下所有策略都是负 sharpe，HTR 永远无法建立基线，
        # 整个研发循环空转（如 2026-07-26 run_20260726_174857 中 6 个节点
        # 全部因此被拒绝）。
        if o_sharpe is not None:
            accepted = b_ret is None or (o_ret is not None and o_ret > b_ret + self.eps)
            reason = (
                "基线无 sharpe，优化版作为初始基线接受（有有效回测指标）"
                if accepted
                else "基线无 sharpe 且优化版收益未优于基线"
            )
            return AcceptanceResult(accepted, reason, b_sharpe, o_sharpe, b_ret, o_ret)
        return AcceptanceResult(
            False,
            "基线无 sharpe 且优化版无有效 sharpe",
            b_sharpe,
            o_sharpe,
            b_ret,
            o_ret,
        )


def is_metrics_unreliable(backtest: dict[str, Any]) -> bool:
    """回测结果是否标记为指标不可信（顶层或 diagnostics 任一为 True）。

    包括：metrics_unreliable 标志、退化策略（无真实交易）、算子链失败。
    """
    if backtest.get("metrics_unreliable"):
        return True
    diag = backtest.get("strategy_diagnostics") or {}
    if diag.get("degenerate"):
        return True
    return bool(diag.get("metrics_unreliable"))


def _metric(backtest: dict[str, Any] | None, key: str) -> float | None:
    """从回测结果取指标，兼容扁平与嵌套 metrics 两种结构。"""
    if not backtest:
        return None
    if key in backtest and backtest[key] is not None:
        val = backtest[key]
        return float(val) if isinstance(val, (int, float)) else None
    metrics = backtest.get("metrics") or {}
    val = metrics.get(key)
    return float(val) if isinstance(val, (int, float)) else None
