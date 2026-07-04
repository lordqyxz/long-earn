"""查理芒格大师 Persona — ADR-012 Phase 1

从 stock_analysis/agents/charles_munger_analyst.py 迁移而来，
继承 BasePersona，通过 @PersonaRegistry.register 自动注册。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from long_earn.skills.personas.base import BasePersona
from long_earn.skills.personas.protocol import PersonaContext, PersonaResult
from long_earn.skills.personas.registry import PersonaRegistry

# Few-shot 示例（从原 charles_munger_analyst.py 迁移）
EXAMPLES = [
    HumanMessage(content="分析沃尔玛的投资价值"),
    AIMessage(
        content=(
            "从心理学角度看，沃尔玛利用消费者的价格敏感性；"
            "从经济学角度看，规模经济形成成本优势；"
            "从工程学角度看，供应链管理系统是关键杠杆。"
        )
    ),
    HumanMessage(content="分析互联网平台的投资价值"),
    AIMessage(
        content=(
            "从网络效应（数学模型）看，平台价值随用户增加而指数级增长；"
            "从心理学角度看，用户粘性形成行为惯性；"
            "从生物学角度看，平台生态系统的适应性。"
        )
    ),
    HumanMessage(content="分析制造业企业的投资价值"),
    AIMessage(
        content=(
            "从物理学角度看，生产效率提升有惯性；"
            "从工程学角度看，自动化是系统优化的关键；"
            "从经济学角度看，成本结构决定了盈利能力。"
        )
    ),
]


@PersonaRegistry.register
class CharlesMungerPersona(BasePersona):
    """查理芒格视角的大师 Persona。"""

    name = "charles_munger"
    display_name = "查理·芒格"
    perspective = "多学科思维模型"
    supported_modes = ("stock_analysis",)

    def __init__(self, llm) -> None:
        super().__init__(llm)
        self.examples = EXAMPLES

    def _do_analyze(self, context: PersonaContext) -> PersonaResult:
        """stock_analysis 模式：加载 charles_munger/stock_analysis.md，调用 LLM。"""
        prompt = self._load_prompt("stock_analysis")
        messages = prompt.format_messages(
            stock_data=context.target,
            event_context=context.event_context,
            examples=self.examples,
        )
        response = self.llm.invoke(messages)
        return self._parse_result(response, "stock_analysis")
