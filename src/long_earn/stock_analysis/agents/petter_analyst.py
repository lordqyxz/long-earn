from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage, HumanMessage

from long_earn.core.chat_prompt_loader import MarkdownChatPromptTemplate

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext


# Few-shot 示例（从原 petter_prompt.md 抽离，转为 Q&A 对）
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


class PetterAnalyst:
    """彼得林奇视角的股票分析智能体

    参考 LangGraph Runtime 实践：
    1. 依赖通过 context 传递
    2. 支持测试时注入 Mock
    """

    def __init__(self, context: "RuntimeContext"):
        """初始化彼得林奇分析师

        Args:
            context: 运行时上下文
        """
        self.context = context
        self.llm = context.require_llm().get_llm()
        self.logger = context.logger
        # 使用多消息聊天提示词加载服务（ADR-011 Phase 4）
        self.prompt = MarkdownChatPromptTemplate(
            "petter_prompt.md",
            caller_file=__file__,
        )
        self.examples = EXAMPLES

    def analyze(self, stock_data: dict[str, Any], event_context: str = "") -> str:
        """分析股票

        Args:
            stock_data: 股票数据
            event_context: 相关市场事件上下文（ADR-007 Phase 3，可空）
        """
        # 渲染多消息聊天模板：[SystemMessage, ...examples, HumanMessage]
        messages = self.prompt.format_messages(
            stock_data=stock_data,
            event_context=event_context,
            examples=self.examples,
        )

        # 调用LLM生成分析（底层 ChatModel 原生支持消息列表）
        response = self.llm.invoke(messages)

        return response.content if hasattr(response, "content") else str(response)
