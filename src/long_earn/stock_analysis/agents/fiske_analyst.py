from typing import TYPE_CHECKING, Any

from long_earn.core.prompt_loader import MarkdownPromptTemplate

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext


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
        # 使用新的提示词加载服务
        self.prompt = MarkdownPromptTemplate(
            "fiske_prompt.md",
            ["stock_data"],
            __file__,
        )

    def analyze(self, stock_data: dict[str, Any]) -> str:
        """分析股票"""
        # 格式化提示词
        formatted_prompt = self.prompt.format(stock_data=stock_data)

        # 调用LLM生成分析
        response = self.llm.invoke(formatted_prompt)

        return response.content if hasattr(response, "content") else str(response)
