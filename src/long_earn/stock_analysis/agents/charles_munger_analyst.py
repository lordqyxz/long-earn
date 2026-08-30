from typing import TYPE_CHECKING, Any

from long_earn.core.chat_prompt_loader import MarkdownChatPromptTemplate
from long_earn.skills.personas.charles_munger import EXAMPLES

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext


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
