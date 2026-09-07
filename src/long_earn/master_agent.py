"""主智能体 (ADR-016 / ADR-024)

用 LangGraph create_react_agent 实现的 ReAct 智能体，
负责任务分解、工具调度、结果整合。

工具集按 ADR-024 §C 分层（``query_*`` 只读查询 / ``run_*`` 长时子图任务），
由 :mod:`long_earn.master_agent_tools` 构建。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage
from langgraph.prebuilt import create_react_agent

from long_earn.core.prompt_loader import MarkdownPromptTemplate
from long_earn.event_inference import create_event_inference_subgraph
from long_earn.master_agent_tools import build_master_tools
from long_earn.stock_analysis.subgraph import create_stock_analysis_subgraph
from long_earn.strategy_rd.research_agent import ResearchAgent

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext
    from long_earn.services import LoggerService, MonitoringService

# ReAct 循环递归上限（每次工具调用消耗 2 步：LLM 决策 + 工具执行）
_DEFAULT_RECURSION_LIMIT = 50


class MasterAgent:
    """主智能体 (ADR-016 / ADR-018 / ADR-024 §C)

    ReAct 智能体，负责任务分解、工具调度、结果整合。
    策略研发委托 ToG ResearchAgent（ADR-018）；工具按 query_*/run_*
    两组前缀分层（ADR-024 §C）。

    用法::

        context = initialize_context()
        agent = MasterAgent(context)
        result = agent.invoke("分析茅台并给我一个适合它的策略")
        print(result["summary"])
    """

    def __init__(self, context: RuntimeContext):
        """初始化主智能体

        Args:
            context: 运行时上下文（DI 容器）
        """
        self.context = context
        self._logger: LoggerService = context.logger
        self._monitoring: MonitoringService = context.monitoring

        # ADR-018：策略研发 = ResearchAgent；分析 / 事件仍为领域子图工具
        self._research_agent = ResearchAgent(context)
        self._stock_analysis_subgraph = create_stock_analysis_subgraph(context)
        self._event_inference_subgraph = create_event_inference_subgraph(context)

        # 加载 system prompt
        prompt_template = MarkdownPromptTemplate(
            "master_agent_prompt.md",
            caller_file=__file__,
        )
        system_prompt = prompt_template.format()

        # 获取 LLM
        llm = context.require_llm().get_llm()

        # 构建工具（ADR-024 §C 分层工具集）
        tools = self._build_tools()

        # 创建 ReAct agent
        self._agent = create_react_agent(
            model=llm,
            tools=tools,
            prompt=system_prompt,
        )

    def _build_tools(self) -> list[Any]:
        """构建工具集（ADR-024 §C：5 个 query_* + 3 个 run_*）"""
        return build_master_tools(
            self.context,
            research_agent=self._research_agent,
            stock_analysis_subgraph=self._stock_analysis_subgraph,
            event_inference_subgraph=self._event_inference_subgraph,
        )

    def invoke(self, user_query: str) -> dict[str, Any]:
        """调用主智能体

        Args:
            user_query: 用户查询

        Returns:
            包含 summary（最终回复）和 messages（ReAct 对话历史）的字典
        """
        self._logger.info(f"主智能体开始处理: {user_query}")

        try:
            result = self._agent.invoke(
                {"messages": [("user", user_query)]},
                config={"recursion_limit": _DEFAULT_RECURSION_LIMIT},
            )
        except Exception as e:
            self._logger.error(f"主智能体执行异常: {e}")
            return {"summary": f"处理过程中出现异常: {e}", "messages": []}

        messages = result.get("messages", [])
        final_answer = ""
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and msg.content:
                final_answer = msg.content
                break

        self._logger.info("主智能体处理完成")
        return {"summary": final_answer, "messages": messages}


def create_master_agent_graph() -> Any:
    """LangGraph CLI / langgraph.json 编译入口。

    Returns:
        已编译的 MasterAgent ReAct 图
    """
    from long_earn.context_init import initialize_context  # noqa: PLC0415

    context = initialize_context()
    return MasterAgent(context)._agent
