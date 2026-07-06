"""大师智能节点 Protocol 定义 — ADR-012 Phase 1

定义 MasterPersona 协议、PersonaContext 输入与 PersonaResult 输出，
作为 stock_analysis / strategy_review / strategy_generate / result_synthesis
多模式大师节点的统一契约。
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel

PersonaMode = Literal[
    "stock_analysis",
    "strategy_review",
    "strategy_generate",
    "result_synthesis",
]


class PersonaContext(BaseModel):
    """大师节点调用上下文。

    Args:
        mode: 调用模式，决定加载哪个 prompt 与走哪条分析分支
        target: 分析目标（stock_analysis 模式下为 stock_data；
                strategy_* 模式下为策略描述等）
        backtest_result: 回测结果（strategy_review / result_synthesis 使用）
        event_context: 相关市场事件上下文（可空）
        available_tools: 可用工具列表（strategy_generate 使用）
    """

    mode: PersonaMode
    target: dict[str, Any]
    backtest_result: dict[str, Any] | None = None
    event_context: str = ""
    available_tools: list[str] = []


class PersonaResult(BaseModel):
    """大师节点输出。

    verdict 为最终结论（如 买入/持有/卖出，或策略采纳/驳回），
    rationale 为推理依据，raw_analysis 保留 LLM 原始文本。
    """

    verdict: str
    rationale: str
    weaknesses: list[str] = []
    suggestions: list[str] = []
    confidence: float = 0.0
    raw_analysis: str = ""


@runtime_checkable
class MasterPersona(Protocol):
    """大师智能节点协议。

    所有内置大师（巴菲特/查理芒格/费雪/彼得林奇）及未来扩展大师
    均需满足此协议：暴露 name/display_name/perspective 类属性，
    并实现 analyze(context) -> PersonaResult。
    """

    name: str
    display_name: str
    perspective: str

    def analyze(self, context: PersonaContext) -> PersonaResult: ...
