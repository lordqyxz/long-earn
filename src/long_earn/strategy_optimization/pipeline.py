"""策略优化流水线 —— 优化→回测→验收 一体编排。

:class:`OptimizationPipeline` 把"交易策略优化"串成可独立调用、可注入的闭环：
基线策略 → 优化器产出优化策略 → 回测服务验证 → 验收门槛判定 → 接受/拒绝 + 谱系。

可注入 :class:`StrategyOptimizer` 与 :class:`BacktestService`，故 e2e 测试可用
Fake 优化器 + mock 回测服务做确定性验证，不依赖真实 LLM / 数据源。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from long_earn.strategy_optimization.acceptance import AcceptanceGate, AcceptanceResult
from long_earn.strategy_optimization.optimizer import StrategyOptimizer

if TYPE_CHECKING:
    from long_earn.services import BacktestService, LoggerService


@dataclass
class OptimizationOutcome:
    """一次优化的完整结果。"""

    accepted: bool
    optimized_strategy: dict[str, Any] | None
    optimized_backtest: dict[str, Any] | None
    acceptance: AcceptanceResult
    lineage_depth: int = 0


class OptimizationPipeline:
    """策略优化流水线。

    Args:
        optimizer: 策略优化器（LLM / Fake）。
        backtest_service: 回测服务，用于验证优化策略。
        gate: 验收门槛。
        logger: 可选日志。
    """

    def __init__(
        self,
        optimizer: StrategyOptimizer,
        backtest_service: BacktestService,
        gate: AcceptanceGate | None = None,
        logger: LoggerService | None = None,
    ) -> None:
        self.optimizer = optimizer
        self.backtest_service = backtest_service
        self.gate = gate or AcceptanceGate()
        self.logger = logger

    def run(
        self,
        base_strategy: dict[str, Any],
        base_strategy_yaml: str,
        improvement_suggestions: list[str],
        baseline_backtest: dict[str, Any] | None = None,
    ) -> OptimizationOutcome:
        """执行一轮优化。

        Args:
            base_strategy: 基线策略字典。
            base_strategy_yaml: 优化策略的 YAML 编译产物（由 develop 节点产出）；
                本流水线假设调用方已把优化策略编译为 YAML。若为空则用基线 YAML 回测。
            improvement_suggestions: 改进建议。
            baseline_backtest: 基线回测结果（验收对比基准）。

        Returns:
            :class:`OptimizationOutcome`。
        """
        # 1) 优化产出新策略
        optimized = self.optimizer.optimize(
            base_strategy, improvement_suggestions, baseline_backtest
        )
        if self.logger:
            self.logger.info(
                f"[strategy_opt] 优化产出: {optimized.get('strategy_name', '?')}"
            )

        # 2) 回测验证优化版（用传入的 YAML；若空则跳过回测，直接拒）
        yaml_to_run = base_strategy_yaml or ""
        if not yaml_to_run:
            return OptimizationOutcome(
                accepted=False,
                optimized_strategy=optimized,
                optimized_backtest=None,
                acceptance=AcceptanceResult(
                    False, "缺少优化策略 YAML，无法回测", None, None, None, None
                ),
                lineage_depth=_lineage_depth(optimized),
            )
        optimized_backtest = self.backtest_service.run(strategy_yaml=yaml_to_run)

        # 3) 验收
        result = self.gate.evaluate(baseline_backtest, optimized_backtest)
        if self.logger:
            self.logger.info(
                f"[strategy_opt] 验收: accepted={result.accepted} ({result.reason})"
            )

        return OptimizationOutcome(
            accepted=result.accepted,
            optimized_strategy=optimized,
            optimized_backtest=optimized_backtest,
            acceptance=result,
            lineage_depth=_lineage_depth(optimized),
        )


def _lineage_depth(strategy: dict[str, Any]) -> int:
    return len(strategy.get("evolution_lineage", []) or [])


def optimize_strategy(  # noqa: PLR0913
    *,
    base_strategy: dict[str, Any],
    base_strategy_yaml: str,
    improvement_suggestions: list[str],
    optimizer: StrategyOptimizer,
    backtest_service: BacktestService,
    baseline_backtest: dict[str, Any] | None = None,
    gate: AcceptanceGate | None = None,
) -> OptimizationOutcome:
    """单次优化便捷调用（无 logger）。"""
    pipeline = OptimizationPipeline(optimizer, backtest_service, gate)
    return pipeline.run(
        base_strategy, base_strategy_yaml, improvement_suggestions, baseline_backtest
    )
