"""策略优化器协议与实现。

:class:`StrategyOptimizer` 把"基线策略 + 改进建议 → 优化策略"抽成可注入接口：
- 生产用 :class:`LLMStrategyOptimizer`（委托由调用方注入的策略研究委托，
  如 ``strategy_rd`` 的 ``StrategyResearchAgent.optimize_strategy``）；
- 测试用 :class:`FakeStrategyOptimizer`（确定性改写，不依赖真实 LLM）。

优化器只负责"产出优化策略"，不判业绩——验收由 :class:`AcceptanceGate` 负责。
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any, Protocol


class StrategyOptimizer(Protocol):
    """策略优化器协议。"""

    def optimize(
        self,
        base_strategy: dict[str, Any],
        improvement_suggestions: list[str],
        previous_backtest: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """产出优化后的策略字典。

        Args:
            base_strategy: 基线策略（含 strategy_name/factors/signals 等）。
            improvement_suggestions: 改进建议列表。
            previous_backtest: 基线策略的回测结果（供优化器参考当前业绩）。

        Returns:
            优化后的策略字典；应保留 ``evolution_lineage`` 谱系。
        """
        ...


class StrategyResearchDelegate(Protocol):
    """策略研究委托协议。

    :class:`LLMStrategyOptimizer` 依赖的最小优化契约：由调用方注入具体实现
    （如 ``strategy_rd`` 的 ``StrategyResearchAgent``）。策略优化上下文只面向
    本协议编程，不直接依赖策略研发上下文，从而消除上下文循环依赖。
    """

    def optimize_strategy(
        self,
        strategy: dict[str, Any],
        improvement_suggestions: list[str],
        previous_backtest: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """根据改进建议产出优化后的策略字典。

        Args:
            strategy: 当前策略字典（含 strategy_name/factors/signals 等）。
            improvement_suggestions: 改进建议列表。
            previous_backtest: 基线策略的回测结果（供优化参考当前业绩）。

        Returns:
            优化后的策略字典；应保留 ``evolution_lineage`` 谱系。
        """
        ...


class LLMStrategyOptimizer:
    """生产优化器：委托调用方注入的策略研究委托。

    构造时由调用方传入满足 :class:`StrategyResearchDelegate` 协议的委托
    （生产环境为 ``strategy_rd`` 的 ``StrategyResearchAgent``）。本类不直接
    导入 ``strategy_rd``，从而保持 ``strategy_rd -> strategy_optimization``
    的单向依赖方向（消除上下文循环依赖）。
    """

    def __init__(self, delegate: StrategyResearchDelegate) -> None:
        self._delegate = delegate

    def optimize(
        self,
        base_strategy: dict[str, Any],
        improvement_suggestions: list[str],
        previous_backtest: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._delegate.optimize_strategy(
            base_strategy, improvement_suggestions, previous_backtest
        )


class FakeStrategyOptimizer:
    """测试用确定性优化器：按预置规则改写策略。

    默认行为：复制基线，把 strategy_name 加 ``_opt`` 后缀，追加一条
    improvement 到 description，并记录 lineage。可注入 ``mutator`` 做自定义改写。
    """

    def __init__(
        self,
        mutator: Callable[[dict[str, Any], list[str]], dict[str, Any]] | None = None,
    ) -> None:
        self._mutator = mutator
        self.call_count = 0

    def optimize(
        self,
        base_strategy: dict[str, Any],
        improvement_suggestions: list[str],
        previous_backtest: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.call_count += 1
        if self._mutator is not None:
            return self._mutator(copy.deepcopy(base_strategy), improvement_suggestions)
        optimized = copy.deepcopy(base_strategy)
        optimized["strategy_name"] = (
            f"{base_strategy.get('strategy_name', 'Strategy')}_opt"
        )
        suggestions_text = "; ".join(improvement_suggestions) or "通用优化"
        optimized["description"] = (
            f"{base_strategy.get('description', '')} [优化: {suggestions_text}]"
        ).strip()
        optimized["optimized"] = True
        lineage = list(base_strategy.get("evolution_lineage", []) or [])
        lineage.append(
            {
                "from": base_strategy.get("strategy_name", "unknown"),
                "suggestions_count": len(improvement_suggestions),
                "had_backtest": previous_backtest is not None,
            }
        )
        optimized["evolution_lineage"] = lineage
        return optimized
