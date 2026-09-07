"""主智能体 (ADR-016 / ADR-024)

用 langchain.agents.create_agent 实现的 ReAct 智能体，
负责任务分解、工具调度、结果整合。

工具集按 ADR-024 §C 分层（``query_*`` 只读查询 / ``run_*`` 长时子图任务），
由 :mod:`long_earn.master_agent_tools` 构建。

会话主循环按 ADR-024 §A：消息历史经 MemorySaver checkpointer 持久化，
``invoke(query, thread_id)`` 多轮复用；``close_session`` 将摘要沉淀入
MemoryService（Substance KNOWLEDGE 形态），实现跨会话积累。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from long_earn.core.prompt_loader import MarkdownPromptTemplate
from long_earn.event_inference import create_event_inference_subgraph
from long_earn.master_agent_tools import build_master_tools
from long_earn.stock_analysis.subgraph import create_stock_analysis_subgraph
from long_earn.strategy_rd.research_agent import ResearchAgent

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.runnables import RunnableConfig

    from long_earn.config import RuntimeContext
    from long_earn.services import LoggerService, MonitoringService

# ReAct 循环递归上限（每次工具调用消耗 2 步：LLM 决策 + 工具执行）
_DEFAULT_RECURSION_LIMIT = 50

# 缺省会话线程标识（ADR-024 §A）
_DEFAULT_THREAD_ID = "default"


class MasterAgent:
    """主智能体 (ADR-016 / ADR-018 / ADR-024 §A / §C)

    ReAct 智能体，负责任务分解、工具调度、结果整合。
    策略研发委托 ToG ResearchAgent（ADR-018）；工具按 query_*/run_*
    两组前缀分层（ADR-024 §C）；消息历史经 checkpointer 按线程持久化，
    多轮 invoke 复用同一会话（ADR-024 §A）。

    用法::

        context = initialize_context()
        agent = MasterAgent(context)
        result = agent.invoke("分析茅台", thread_id="t1")
        followup = agent.invoke("它的竞争对手呢？", thread_id="t1")
        closed = agent.close_session("t1")  # 摘要沉淀入记忆
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

        # 获取 LLM（close_session 摘要生成复用）
        self._llm: BaseChatModel = context.require_llm().get_llm()

        # 构建工具（ADR-024 §C 分层工具集）
        tools = self._build_tools()

        # 会话状态持久化（ADR-024 §A：起步 MemorySaver，后续接 PG）
        self._checkpointer = MemorySaver()

        # 创建 ReAct agent（langchain.agents.create_agent，
        # langgraph.prebuilt.create_react_agent 已于 LangGraph V1.0 废弃）
        self._agent = create_agent(
            model=self._llm,
            tools=tools,
            system_prompt=system_prompt,
            checkpointer=self._checkpointer,
        )

    def _build_tools(self) -> list[Any]:
        """构建工具集（ADR-024 §C/§D：6 个 query_* + 3 个 run_* 后台任务）"""
        return build_master_tools(
            self.context,
            research_agent=self._research_agent,
            stock_analysis_subgraph=self._stock_analysis_subgraph,
            event_inference_subgraph=self._event_inference_subgraph,
        )

    def invoke(
        self, user_query: str, thread_id: str = _DEFAULT_THREAD_ID
    ) -> dict[str, Any]:
        """调用主智能体（多轮复用同一 thread 的消息历史，ADR-024 §A）

        Args:
            user_query: 用户查询
            thread_id: 会话线程标识；同一 thread_id 的连续 invoke
                共享消息历史

        Returns:
            包含 summary（最终回复）和 messages（ReAct 对话历史）的字典
        """
        self._logger.info(f"主智能体开始处理: {user_query}")

        config: RunnableConfig = {
            "recursion_limit": _DEFAULT_RECURSION_LIMIT,
            "configurable": {"thread_id": thread_id},
        }

        try:
            result = self._agent.invoke(
                {"messages": [HumanMessage(content=user_query)]},
                config=config,
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

    def close_session(self, thread_id: str = _DEFAULT_THREAD_ID) -> dict[str, Any]:
        """结束会话 — 摘要沉淀入 MemoryService 并清理线程状态（ADR-024 §A）

        摘要由 LLM 从会话历史压缩生成；生成失败时回退为最终回复，
        不静默吞异常。摘要落库后删除 thread 的 checkpointer 状态，
        同一 thread_id 的后续 invoke 将从空历史开始。

        Args:
            thread_id: 会话线程标识

        Returns:
            包含 summary（摘要文本）、substance_id（物质 ID）与
            turns（用户消息数）的字典；无历史会话返回空摘要
        """
        messages = self._session_messages(thread_id)
        if not messages:
            self._logger.warning(f"会话无历史，跳过摘要沉淀: {thread_id}")
            return {"summary": "", "substance_id": "", "turns": 0}

        turns = sum(isinstance(m, HumanMessage) for m in messages)
        summary = self._summarize_session(messages)
        substance_id = self.context.memory.save_session_summary(
            thread_id=thread_id,
            summary=summary,
            turns=turns,
        )
        self._checkpointer.delete_thread(thread_id)
        self._logger.info(f"会话已关闭并沉淀: {thread_id} → {substance_id}")
        return {"summary": summary, "substance_id": substance_id, "turns": turns}

    def _session_messages(self, thread_id: str) -> list[BaseMessage]:
        """读取 thread 当前累积的消息历史（checkpointer 视图）。"""
        state = self._agent.get_state({"configurable": {"thread_id": thread_id}})
        if not state.values:
            return []
        return list(state.values.get("messages", []))

    def _summarize_session(self, messages: list[BaseMessage]) -> str:
        """LLM 压缩会话历史为摘要；失败回退最终 AI 回复。"""
        transcript = "\n".join(
            f"{'用户' if isinstance(m, HumanMessage) else '助手'}: {m.content}"
            for m in messages
            if isinstance(m, (HumanMessage, AIMessage)) and m.content
        )
        prompt = MarkdownPromptTemplate(
            "master_agent_session_summary.md",
            caller_file=__file__,
        ).format(transcript=transcript)
        try:
            response = self._llm.invoke(prompt)
            text = str(response.content).strip()
            if text:
                return text
        except Exception as e:
            self._logger.warning(f"会话摘要生成失败，回退最终回复: {e}")
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and msg.content:
                return str(msg.content)
        return ""


def create_master_agent_graph() -> Any:
    """LangGraph CLI / langgraph.json 编译入口。

    Returns:
        已编译的 MasterAgent ReAct 图
    """
    from long_earn.context_init import initialize_context  # noqa: PLC0415

    context = initialize_context()
    return MasterAgent(context)._agent
