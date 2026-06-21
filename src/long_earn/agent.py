"""主图
分析用户查询，根据意图路由到不同的子图进行处理。
"""

import json
from functools import partial
from typing import TYPE_CHECKING, Any

from langchain_core.prompts import PromptTemplate
from langgraph.graph import END, START, StateGraph

from long_earn.core.llm_utils import parse_llm_json
from long_earn.services import LLMService, LoggerService, MonitoringService
from long_earn.state import State
from long_earn.stock_analysis.subgraph import create_stock_analysis_subgraph
from long_earn.strategy_rd.subgraph import create_strategy_rd_subgraph

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext


def _decide_route(
    user_query: str,
    strategy_kw: tuple[str, ...],
    stock_kw: tuple[str, ...],
) -> tuple[str, str]:
    """根据关键词决定路由"""
    if any(kw in user_query for kw in strategy_kw):
        return ("strategy_rd", "关键词匹配：策略相关")
    elif any(kw in user_query for kw in stock_kw):
        return ("stock_analysis", "关键词匹配：股票/公司分析相关")
    else:
        return ("unknown", "无法确定路由")


def _start_node(state: State) -> dict:  # noqa: ARG001
    """开始节点"""
    return {"status": "started"}


def _intent_analyze_node(
    state: State,
    llm_service: LLMService,
    logger: LoggerService,
    monitoring: MonitoringService,
    context: "RuntimeContext",
) -> dict:
    """用户意图分析节点 - 使用 LLM 进行智能路由"""
    with monitoring.track("intent_analyze"):
        user_query = state["user_query"]

        if not user_query or not user_query.strip():
            logger.error("用户查询为空")
            return {"route": "unknown", "error": "用户查询为空"}

        logger.info(f"开始分析用户意图：{user_query}")

        routing_prompt = PromptTemplate(
            input_variables=["user_query"],
            template="""请分析以下用户查询，确定用户意图并选择最合适的子图进行路由。

用户查询：{user_query}

可用的子图:
1. strategy_rd (策略研究) - 用于投资策略研究、投资思路分析、策略制定等
2. stock_analysis (股票分析) - 用于具体股票分析、股票代码查询、公司基本面分析等

请根据以下 JSON 格式输出路由决策:
{{
    "route": "strategy_rd" 或 "stock_analysis",
    "reason": "简短的路由理由"
}}

只输出 JSON，不要其他内容。""",
        )

        try:
            response = llm_service.invoke(
                routing_prompt.format(user_query=user_query),
                format="json",
            )
            if hasattr(response, "usage_metadata") and response.usage_metadata:
                monitoring.track_tokens(response.usage_metadata)
            routing_decision = response.content.strip()
            logger.debug(f"LLM 响应：{routing_decision!r}")

            route = "unknown"
            reason = ""

            config = context.config
            strategy_keywords = config.strategy_keywords if config else ()
            stock_analysis_keywords = config.stock_analysis_keywords if config else ()

            try:
                logger.debug(f"响应：{routing_decision}")
                decision = parse_llm_json(routing_decision)
                route = decision.get("route", "").strip()
                reason = decision.get("reason", "")
                logger.debug(f"JSON 解析成功，路由：{route}, 理由：{reason}")

                if route not in ["strategy_rd", "stock_analysis"]:
                    logger.warning(f"解析得到无效路由值：{route}，使用关键词匹配")
                    route, reason = _decide_route(
                        user_query, strategy_keywords, stock_analysis_keywords
                    )

            except json.JSONDecodeError as e:
                logger.exception(f"JSON 解析失败：{e!s}，使用关键词匹配")
                route, reason = _decide_route(
                    user_query, strategy_keywords, stock_analysis_keywords
                )
            except Exception as e:
                logger.error(f"JSON 处理异常：{e!s}，使用关键词匹配")
                route, reason = _decide_route(
                    user_query, strategy_keywords, stock_analysis_keywords
                )

            logger.info(f"路由决策：{route}，理由：{reason}")
            return {"route": route, "routing_reason": reason}

        except Exception as e:
            logger.error(f"意图分析异常：{e!s}")
            strategy_keywords = (
                context.config.strategy_keywords if context.config else ()
            )
            stock_analysis_keywords = (
                context.config.stock_analysis_keywords if context.config else ()
            )
            if any(kw in user_query for kw in strategy_keywords):
                logger.info("异常情况下使用关键词匹配：策略相关")
                return {
                    "route": "strategy_rd",
                    "routing_reason": "关键词匹配：策略相关",
                }
            elif any(kw in user_query for kw in stock_analysis_keywords):
                logger.info("异常情况下使用关键词匹配：股票相关")
                return {
                    "route": "stock_analysis",
                    "routing_reason": "关键词匹配：股票相关",
                }
            else:
                return {"route": "unknown", "error": str(e)}


def _strategy_rd_node(
    state: State,
    subgraph: Any,
    logger: LoggerService,
    monitoring: MonitoringService,
) -> dict:
    """策略研究子图节点"""
    with monitoring.track("strategy_rd"):
        user_query = state.get("user_query", "")
        logger.info(f"执行策略研究子图：{user_query}")
        result = subgraph.invoke({"query": user_query})
        logger.debug(f"策略研究结果：{result}")
        return {"strategy_result": result}


def _stock_analysis_node(
    state: State,
    subgraph: Any,
    logger: LoggerService,
    monitoring: MonitoringService,
) -> dict:
    """股票分析子图节点"""
    with monitoring.track("stock_analysis"):
        user_query = state.get("user_query", "")
        logger.info(f"执行股票分析子图：{user_query}")
        result = subgraph.invoke({"query": user_query})
        logger.debug(f"股票分析结果：{result}")
        return {"stock_analysis_result": result}


def _summarize_node(
    state: State,
    llm_service: LLMService,
    logger: LoggerService,
    monitoring: MonitoringService,
) -> dict:
    """汇总节点 - 使用 LLM 生成友好的客户返回结果"""
    with monitoring.track("summarize"):
        strategy_result = state.get("strategy_result")
        stock_analysis_result = state.get("stock_analysis_result")
        routing_reason = state.get("routing_reason", "")
        user_query = state.get("user_query", "")

        logger.info(f"执行汇总节点，路由类型：{routing_reason}")

        if not strategy_result and not stock_analysis_result:
            logger.warning("无结果可汇总")
            return {"summary": "抱歉，我无法处理您的请求，请稍后再试。"}

        summarize_prompt = PromptTemplate(
            input_variables=[
                "user_query",
                "strategy_result",
                "stock_analysis_result",
                "routing_reason",
            ],
            template="""请根据以下研究结果生成一段证据详实，保持原有文本专业性的基础上、友好的回复，直接面向客户，综合概述技术细节。如果某部分结果为空，请忽略该部分。
用户原始问题：{user_query}
路由类型：{routing_reason}

策略研究结果:
{strategy_result}

股票分析结果:
{stock_analysis_result}

针对股票分析结果，总结各个视角下最佳买入区间，以表格样式汇总给我。
""",
        )

        try:
            response = llm_service.invoke(
                summarize_prompt.format(
                    user_query=user_query,
                    strategy_result=strategy_result or "无",
                    stock_analysis_result=stock_analysis_result or "无",
                    routing_reason=routing_reason,
                )
            )
            summary = response.content.strip()
            logger.debug(f"汇总结果：{summary}")
            return {"summary": summary}
        except Exception as e:
            logger.error(f"汇总异常：{e!s}")
            summary = ""
            if strategy_result:
                summary += f"策略研究结果：{strategy_result}\n"
            if stock_analysis_result:
                summary += f"股票分析结果：{stock_analysis_result}\n"
            logger.debug(f"降级处理汇总结果：{summary}")
            return {
                "summary": (summary if summary else "处理过程中出现异常，请稍后再试。")
            }


def _route_decision(state: State, logger: LoggerService) -> str:
    """路由决策函数 - 根据 route 字段返回目标节点"""
    route = state.get("route", "unknown")
    logger.debug(f"路由决策：{route}")
    if route == "strategy_rd":
        return "strategy_rd"
    elif route == "stock_analysis":
        return "stock_analysis"
    else:
        return "summarize"


def create_main_agent(context: "RuntimeContext"):
    """创建主图智能体

    Args:
        context: 运行时上下文
    """
    llm_service = context.require_llm()
    logger = context.logger
    monitoring = context.monitoring

    strategy_rd_subgraph = create_strategy_rd_subgraph(context)
    stock_analysis_subgraph = create_stock_analysis_subgraph(context)

    graph = StateGraph(State)

    graph.add_node("start", _start_node)
    graph.add_node(
        "intent_analyze",
        partial(
            _intent_analyze_node,
            llm_service=llm_service,
            logger=logger,
            monitoring=monitoring,
            context=context,
        ),
    )
    graph.add_node(
        "strategy_rd",
        partial(
            _strategy_rd_node,
            subgraph=strategy_rd_subgraph,
            logger=logger,
            monitoring=monitoring,
        ),
    )
    graph.add_node(
        "stock_analysis",
        partial(
            _stock_analysis_node,
            subgraph=stock_analysis_subgraph,
            logger=logger,
            monitoring=monitoring,
        ),
    )
    graph.add_node(
        "summarize",
        partial(
            _summarize_node,
            llm_service=llm_service,
            logger=logger,
            monitoring=monitoring,
        ),
    )

    graph.add_edge(START, "start")
    graph.add_edge("start", "intent_analyze")
    graph.add_conditional_edges(
        "intent_analyze",
        partial(_route_decision, logger=logger),
        {
            "strategy_rd": "strategy_rd",
            "stock_analysis": "stock_analysis",
            "summarize": "summarize",
        },
    )
    graph.add_edge("strategy_rd", "summarize")
    graph.add_edge("stock_analysis", "summarize")
    graph.add_edge("summarize", END)

    return graph.compile()
