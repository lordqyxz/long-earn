"""主智能体 (ADR-016)

用 LangGraph create_react_agent 实现的 ReAct 智能体，
负责任务分解、工具调度、结果整合。

替代旧 agent.py 的路由器架构，具备：
- 任务分解能力（复合请求拆分为多子任务）
- 跨子图协作（策略研发 + 股票分析协作）
- 结果反思与整合
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from long_earn.core.prompt_loader import MarkdownPromptTemplate
from long_earn.event_inference import create_event_inference_subgraph
from long_earn.stock_analysis.subgraph import create_stock_analysis_subgraph
from long_earn.strategy_rd.research_agent import ResearchAgent
from long_earn.tools.kimi_web_search import kimi_web_search

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext
    from long_earn.services import LoggerService, MonitoringService

# ReAct 循环递归上限（每次工具调用消耗 2 步：LLM 决策 + 工具执行）
_DEFAULT_RECURSION_LIMIT = 50


class MasterAgent:
    """主智能体 (ADR-016 / ADR-018)

    ReAct 智能体，负责任务分解、工具调度、结果整合。
    策略研发委托 ToG ResearchAgent（ADR-018），不再直调 HTR 子图。

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

        # 构建工具
        tools = self._build_tools()

        # 创建 ReAct agent
        self._agent = create_react_agent(
            model=llm,
            tools=tools,
            prompt=system_prompt,
        )

    def _build_tools(self) -> list[Any]:
        """构建工具集（6 个任务工具）

        每个工具是子图或服务的薄封装，捕获 context 中的服务实例。
        """
        return [
            self._make_research_strategy_tool(),
            self._make_analyze_stock_tool(),
            self._make_infer_events_tool(),
            self._make_retrieve_memory_tool(),
            self._make_web_search_tool(),
            self._make_summarize_tool(),
        ]

    def _make_research_strategy_tool(self) -> Any:
        """策略研发工具 — 委托 ToG ResearchAgent（ADR-018）"""
        logger = self._logger
        monitoring = self._monitoring
        research_agent = self._research_agent

        @tool
        def research_strategy(idea: str, constraints: str = "") -> str:
            """策略研发：委托 Think-on-Graph ResearchAgent 探索图 + 回测证据，
            返回最佳策略摘要、指标与探索路径。

            Args:
                idea: 策略研发想法或方向描述
                constraints: 约束条件（可选，如股票池、行业偏好、风险偏好等）

            Returns:
                策略研发结果摘要，包含策略 YAML、回测指标和探索路径
            """
            with monitoring.track("research_strategy"):
                query = idea if not constraints else f"{idea} (约束: {constraints})"
                logger.info(f"策略研发工具调用(ToG): {query}")
                try:
                    result = research_agent.invoke(idea, constraints)
                    return _format_strategy_result(result)
                except Exception as e:
                    logger.error(f"策略研发失败: {e}")
                    return f"策略研发执行失败: {e}"

        return research_strategy

    def _make_analyze_stock_tool(self) -> Any:
        """股票分析工具"""
        logger = self._logger
        monitoring = self._monitoring
        subgraph = self._stock_analysis_subgraph

        @tool
        def analyze_stock(query: str, symbols: str = "") -> str:
            """股票分析：委托股票分析子图进行多视角分析
            （巴菲特/芒格/彼得林奇/费雪/资金流向），返回综合分析结论。

            Args:
                query: 股票分析查询（如股票名称、代码或分析方向）
                symbols: 特定股票代码（可选，如 600519）

            Returns:
                股票分析结果摘要
            """
            with monitoring.track("analyze_stock"):
                full_query = query if not symbols else f"{query} (股票: {symbols})"
                logger.info(f"股票分析工具调用: {full_query}")
                try:
                    result = subgraph.invoke({"query": full_query})
                    return _format_stock_result(result)
                except Exception as e:
                    logger.error(f"股票分析失败: {e}")
                    return f"股票分析执行失败: {e}"

        return analyze_stock

    def _make_infer_events_tool(self) -> Any:
        """事件推理工具"""
        logger = self._logger
        monitoring = self._monitoring
        subgraph = self._event_inference_subgraph

        @tool
        def infer_events(query: str) -> str:
            """事件推理：委托事件推理子图提取新闻事件并推理其对市场的影响。

            Args:
                query: 事件查询（如新闻内容、热点话题等）

            Returns:
                事件推理结果摘要
            """
            with monitoring.track("infer_events"):
                logger.info(f"事件推理工具调用: {query}")
                try:
                    result = subgraph.invoke({"query": query})
                    return _format_event_result(result)
                except Exception as e:
                    logger.error(f"事件推理失败: {e}")
                    return f"事件推理执行失败: {e}"

        return infer_events

    def _make_retrieve_memory_tool(self) -> Any:
        """记忆检索工具"""
        logger = self._logger
        monitoring = self._monitoring
        memory = self.context.memory

        @tool
        def retrieve_memory(query: str, k: int = 3) -> str:
            """检索记忆：从历史策略经验和知识库中检索与查询相关的内容。

            Args:
                query: 检索查询
                k: 返回结果数量（默认 3）

            Returns:
                检索到的记忆内容
            """
            with monitoring.track("retrieve_memory"):
                logger.info(f"记忆检索工具调用: {query}, k={k}")
                try:
                    results = memory.search(query, k=k)
                    if not results:
                        return "未检索到相关记忆内容"
                    return "\n---\n".join(results)
                except Exception as e:
                    logger.error(f"记忆检索失败: {e}")
                    return f"记忆检索失败: {e}"

        return retrieve_memory

    def _make_web_search_tool(self) -> Any:
        """网络搜索工具"""
        logger = self._logger
        monitoring = self._monitoring

        @tool
        def web_search(query: str) -> str:
            """网络搜索：使用 Kimi API 进行实时网络搜索，获取最新信息。

            Args:
                query: 搜索关键词

            Returns:
                搜索结果摘要
            """
            with monitoring.track("web_search"):
                logger.info(f"网络搜索工具调用: {query}")
                try:
                    results = kimi_web_search(query)
                    if not results:
                        return "未找到搜索结果"
                    formatted: list[str] = []
                    for r in results:
                        title = r.get("title", "")
                        content = r.get("content", "")
                        formatted.append(f"[{title}]\n{content}")
                    return "\n---\n".join(formatted)
                except Exception as e:
                    logger.error(f"网络搜索失败: {e}")
                    return f"网络搜索失败: {e}"

        return web_search

    def _make_summarize_tool(self) -> Any:
        """结果整合工具"""

        @tool
        def summarize(
            strategy_result: str = "",
            stock_result: str = "",
            event_result: str = "",
        ) -> str:
            """整合多工具结果：将多个工具的返回结果整合为结构化摘要。

            在调用多个工具后使用此工具整合结果。

            Args:
                strategy_result: 策略研发工具的返回结果（可选）
                stock_result: 股票分析工具的返回结果（可选）
                event_result: 事件推理工具的返回结果（可选）

            Returns:
                结构化的整合结果
            """
            parts: list[str] = []
            if strategy_result:
                parts.append(f"## 策略研发结果\n\n{strategy_result}")
            if stock_result:
                parts.append(f"## 股票分析结果\n\n{stock_result}")
            if event_result:
                parts.append(f"## 事件推理结果\n\n{event_result}")
            return "\n\n---\n\n".join(parts) if parts else "无结果可整合"

        return summarize

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


# ── 子图结果格式化辅助函数 ──────────────────────────────────────────


def _format_strategy_result(result: dict[str, Any]) -> str:
    """格式化策略研发子图结果为可读字符串"""
    parts: list[str] = []

    # 最终结果文本
    final_result = result.get("result")
    if final_result:
        parts.append(final_result)

    # 策略名称和 YAML
    name = result.get("strategy_name")
    yaml = result.get("strategy_yaml") or result.get("optimized_strategy_yaml")
    if name:
        parts.append(f"策略名称: {name}")
    if yaml:
        parts.append(f"策略 YAML:\n{yaml}")

    # 回测结果
    backtest = result.get("backtest_result")
    if backtest and isinstance(backtest, dict):
        metrics = backtest.get("metrics", backtest)
        if isinstance(metrics, dict):
            key_metrics: list[str] = []
            for k in (
                "total_return",
                "annual_return",
                "sharpe_ratio",
                "max_drawdown",
                "win_rate",
            ):
                if k in metrics:
                    key_metrics.append(f"  {k}: {metrics[k]}")
            if key_metrics:
                parts.append("回测指标:\n" + "\n".join(key_metrics))

    if not parts:
        return json.dumps(result, ensure_ascii=False, default=str)[:2000]

    return "\n\n".join(parts)


def _format_stock_result(result: dict[str, Any]) -> str:
    """格式化股票分析子图结果为可读字符串"""
    summary = result.get("summary")
    if summary:
        return summary

    error = result.get("error")
    if error:
        return f"分析错误: {error}"

    return json.dumps(result, ensure_ascii=False, default=str)[:2000]


def _format_event_result(result: dict[str, Any]) -> str:
    """格式化事件推理子图结果为可读字符串"""
    summary = result.get("summary")
    if summary:
        return summary

    # 尝试提取事件列表
    events = result.get("events")
    if events and isinstance(events, list):
        parts: list[str] = []
        for event in events[:10]:
            if isinstance(event, dict):
                title = event.get("title", "")
                impact = event.get("impact", "")
                parts.append(f"[{title}] 影响: {impact}")
            elif isinstance(event, str):
                parts.append(event)
        return "\n".join(parts) if parts else "未提取到事件"

    return json.dumps(result, ensure_ascii=False, default=str)[:2000]
