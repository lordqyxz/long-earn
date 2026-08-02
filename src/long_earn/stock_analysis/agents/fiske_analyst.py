from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage, HumanMessage

from long_earn.core.chat_prompt_loader import MarkdownChatPromptTemplate

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext


# Few-shot 示例（从原 fiske_prompt.md 抽离，转为 Q&A 对）
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


class FiskeAnalyst:
    """费雪视角的股票分析智能体

    参考 LangGraph Runtime 实践：
    1. 依赖通过 context 传递
    2. 支持测试时注入 Mock
    """

    def __init__(self, context: "RuntimeContext"):
        """初始化费雪分析师

        Args:
            context: 运行时上下文
        """
        self.context = context
        self.llm = context.require_llm().get_llm()
        self.logger = context.logger
        # 使用多消息聊天提示词加载服务（ADR-011 Phase 4）
        self.prompt = MarkdownChatPromptTemplate(
            "fiske_prompt.md",
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
