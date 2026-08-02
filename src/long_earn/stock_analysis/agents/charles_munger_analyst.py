from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage, HumanMessage

from long_earn.core.chat_prompt_loader import MarkdownChatPromptTemplate

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext


# Few-shot 示例（从原 charles_munger_prompt.md 抽离，转为 Q&A 对）
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


class CharlesMungerAnalyst:
    """查理芒格视角的股票分析智能体

    参考 LangGraph Runtime 实践：
    1. 依赖通过 context 传递
    2. 支持测试时注入 Mock
    """

    def __init__(self, context: "RuntimeContext"):
        """初始化查理芒格分析师

        Args:
            context: 运行时上下文
        """
        self.context = context
        self.llm = context.require_llm().get_llm()
        self.logger = context.logger
        # 使用多消息聊天提示词加载服务（ADR-011 Phase 4）
        self.prompt = MarkdownChatPromptTemplate(
            "charles_munger_prompt.md",
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
