"""巴菲特大师 Persona — ADR-012 Phase 1

从 stock_analysis/agents/buffett_analyst.py 迁移而来，
继承 BasePersona，通过 @PersonaRegistry.register 自动注册。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from long_earn.skills.personas.base import BasePersona
from long_earn.skills.personas.protocol import PersonaContext, PersonaResult
from long_earn.skills.personas.registry import PersonaRegistry

# Few-shot 示例（从原 buffett_analyst.py 迁移）
EXAMPLES = [
    HumanMessage(content="分析可口可乐的投资价值"),
    AIMessage(
        content=(
            "对于可口可乐这样的消费品牌，我们关注其全球品牌影响力、定价权和稳定的现金流。"
            "即使在经济衰退期间，消费者仍会购买这些必需品，这构成了强大的护城河。"
        )
    ),
    HumanMessage(content="分析银行股的投资价值"),
    AIMessage(
        content=(
            "对于银行股，我们重点关注资产质量、净息差和风险管理能力。"
            "优秀的银行能够在控制风险的同时获得稳定收益。"
        )
    ),
    HumanMessage(content="分析科技公司的投资价值"),
    AIMessage(
        content=(
            "对于科技公司，我们评估其技术壁垒、市场占有率和创新持续性。"
            "像苹果这样的公司不仅有强大的品牌，还有生态系统锁定效应。"
        )
    ),
]


@PersonaRegistry.register
class BuffettPersona(BasePersona):
    """巴菲特视角的大师 Persona。"""

    name = "buffett"
    display_name = "沃伦·巴菲特"
    perspective = "价值投资"
    supported_modes = ("stock_analysis",)

    def __init__(self, llm) -> None:
        super().__init__(llm)
        self.examples = EXAMPLES

    def _do_analyze(self, context: PersonaContext) -> PersonaResult:
        """stock_analysis 模式：加载 buffett/stock_analysis.md，调用 LLM。"""
        prompt = self._load_prompt("stock_analysis")
        messages = prompt.format_messages(
            stock_data=context.target,
            event_context=context.event_context,
            examples=self.examples,
        )
        response = self.llm.invoke(messages)
        return self._parse_result(response, "stock_analysis")
