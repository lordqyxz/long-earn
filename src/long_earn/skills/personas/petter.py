"""彼得林奇大师 Persona — ADR-012 Phase 1

从 stock_analysis/agents/petter_analyst.py 迁移而来，
继承 BasePersona，通过 @PersonaRegistry.register 自动注册。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from long_earn.skills.personas.base import BasePersona
from long_earn.skills.personas.protocol import PersonaContext, PersonaResult
from long_earn.skills.personas.registry import PersonaRegistry

# Few-shot 示例（从原 petter_analyst.py 迁移）
EXAMPLES = [
    HumanMessage(content="分析沃尔玛的投资价值"),
    AIMessage(
        content=(
            "对于沃尔玛这类稳定增长型股票，我们关注其同店销售额增长、门店扩张计划和成本控制能力。"
            "PEG 比率适中，适合长期持有。"
        )
    ),
    HumanMessage(content="分析高通的投资价值"),
    AIMessage(
        content=(
            "对于高通这类快速成长型股票，我们评估其在 5G、芯片设计领域的技术领先地位，"
            "以及研发投入与收入比。虽然估值较高，但增长潜力巨大。"
        )
    ),
    HumanMessage(content="分析房地产信托基金的投资价值"),
    AIMessage(
        content=(
            "对于房地产信托基金 (REITs)，我们将其归类为缓慢增长型，"
            "重点关注租金收益率、物业组合质量和债务结构。适合追求稳定分红的投资者。"
        )
    ),
]


@PersonaRegistry.register
class PetterPersona(BasePersona):
    """彼得林奇视角的大师 Persona。"""

    name = "petter"
    display_name = "彼得·林奇"
    perspective = "PEG 成长投资"
    supported_modes = ("stock_analysis",)

    def __init__(self, llm) -> None:
        super().__init__(llm)
        self.examples = EXAMPLES

    def _do_analyze(self, context: PersonaContext) -> PersonaResult:
        """stock_analysis 模式：加载 petter/stock_analysis.md，调用 LLM。"""
        prompt = self._load_prompt("stock_analysis")
        messages = prompt.format_messages(
            stock_data=context.target,
            event_context=context.event_context,
            examples=self.examples,
        )
        response = self.llm.invoke(messages)
        return self._parse_result(response, "stock_analysis")
