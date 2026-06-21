"""交易策略优化模块

独立的"交易策略优化"子系统：给定一个基线策略及其回测结果 + 改进建议，产出优化
策略、回测验证、验收门槛（业绩确实提升才接受），并记录演进谱系。

与 :mod:`long_earn.strategy_rd` 的关系：strategy_rd 的 optimize 循环是"研发期内
迭代"；本模块把"优化"抽成可独立调用、可注入（LLM / Fake）、可验收的模块，便于
e2e 测试与离线批量优化。

对外暴露：
- :class:`StrategyOptimizer` 协议 + :class:`LLMStrategyOptimizer` / :class:`FakeStrategyOptimizer`
- :class:`AcceptanceGate` —— 业绩验收门槛
- :class:`OptimizationPipeline` —— 优化→回测→验收 一体编排
- :func:`optimize_strategy` —— 单次便捷调用
"""

from long_earn.strategy_optimization.acceptance import AcceptanceGate, AcceptanceResult
from long_earn.strategy_optimization.optimizer import (
    FakeStrategyOptimizer,
    LLMStrategyOptimizer,
    StrategyOptimizer,
)
from long_earn.strategy_optimization.pipeline import (
    OptimizationOutcome,
    OptimizationPipeline,
    optimize_strategy,
)

__all__ = [
    "AcceptanceGate",
    "AcceptanceResult",
    "FakeStrategyOptimizer",
    "LLMStrategyOptimizer",
    "OptimizationOutcome",
    "OptimizationPipeline",
    "StrategyOptimizer",
    "optimize_strategy",
]
