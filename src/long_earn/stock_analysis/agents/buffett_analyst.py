from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage, HumanMessage

from long_earn.core.chat_prompt_loader import MarkdownChatPromptTemplate

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext


# Few-shot 示例（从原 buffett_prompt.md 抽离，转为 Q&A 对）
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


class BuffettAnalyst:
    """巴菲特视角的股票分析智能体

    参考 LangGraph Runtime 实践：
    1. 依赖通过 context 传递
    2. 支持测试时注入 Mock
    """

    def __init__(self, context: "RuntimeContext"):
        """初始化巴菲特分析师

        Args:
            context: 运行时上下文
        """
        self.context = context
        self.llm = context.require_llm().get_llm()
        self.logger = context.logger

        # 使用多消息聊天提示词加载服务（ADR-011 Phase 4）
        self.prompt = MarkdownChatPromptTemplate(
            template_file="buffett_prompt.md",
            caller_file=__file__,
        )
        self.examples = EXAMPLES

    def analyze(
        self, stock_data: dict[str, Any], event_context: str = ""
    ) -> str:
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
