"""费雪大师 Persona — ADR-012 Phase 1

从 stock_analysis/agents/fiske_analyst.py 迁移而来，
继承 BasePersona，通过 @PersonaRegistry.register 自动注册。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from long_earn.skills.personas.base import BasePersona
from long_earn.skills.personas.protocol import PersonaContext, PersonaResult
from long_earn.skills.personas.registry import PersonaRegistry

# Few-shot 示例（从原 fiske_analyst.py 迁移）
EXAMPLES = [
    HumanMessage(content="分析半导体公司的投资价值"),
    AIMessage(
        content=(
            "对于半导体公司，我们重点考察其研发投入占营收比例、专利数量、"
            "技术代差优势以及下游需求增长趋势。高研发投入通常预示着未来的竞争优势。"
        )
    ),
    HumanMessage(content="分析生物制药公司的投资价值"),
    AIMessage(
        content=(
            "对于生物制药公司，我们关注其在研管线丰富程度、临床试验进展、"
            "监管审批预期以及专利保护期。创新药的成功率虽低，但一旦成功回报巨大。"
        )
    ),
    HumanMessage(content="分析电动车制造商的投资价值"),
    AIMessage(
        content=(
            "对于电动车制造商，我们评估其电池技术先进性、产能扩张计划、"
            "品牌认知度以及充电网络布局。技术领先和规模效应是关键。"
        )
    ),
]


@PersonaRegistry.register
class FiskePersona(BasePersona):
    """费雪视角的大师 Persona。"""

    name = "fiske"
    display_name = "菲利普·费雪"
    perspective = "成长股投资"
    supported_modes = ("stock_analysis",)

    def __init__(self, llm) -> None:
        super().__init__(llm)
        self.examples = EXAMPLES

    def _do_analyze(self, context: PersonaContext) -> PersonaResult:
        """stock_analysis 模式：加载 fiske/stock_analysis.md，调用 LLM。"""
        prompt = self._load_prompt("stock_analysis")
        messages = prompt.format_messages(
            stock_data=context.target,
            event_context=context.event_context,
            examples=self.examples,
        )
        response = self.llm.invoke(messages)
        return self._parse_result(response, "stock_analysis")
