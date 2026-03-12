"""主图
分析用户查询，根据意图路由到不同的子图进行处理。
"""

import json
import os

from langchain_core.prompts import PromptTemplate
from langchain_ollama import OllamaEmbeddings
from langchain_qdrant import QdrantVectorStore
from langgraph.graph import END, START, StateGraph
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

from long_earn.state import State
from long_earn.stock_analysis.subgraph import create_stock_analysis_subgraph
from long_earn.strategy_rd.subgraph import create_strategy_rd_subgraph
from long_earn.utils import llm_factory
from long_earn.utils.llm_factory import create_llm
from long_earn.utils.logger import LOGGER

client = QdrantClient(os.getenv("QDRANT_URL", ":memory:"))
embeddings = OllamaEmbeddings(model=os.getenv("EMBEDDING_MODEL", "qwen3-embedding:0.6b"))
vector_size = len(embeddings.embed_query("sample text"))

if not client.collection_exists("test"):
    client.create_collection(
        collection_name="test",
        vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
    )
vector_store = QdrantVectorStore(
    client=client,
    collection_name="test",
    embedding=embeddings,
)


def create_main_agent():
    """创建主图智能体"""
    # 创建LLM实例用于路由决策
    llm = llm_factory.create_llm(llm_type="ollama", model_name="qwen3.5:cloud")

    # 创建子图
    strategy_rd_subgraph = create_strategy_rd_subgraph()
    stock_analysis_subgraph = create_stock_analysis_subgraph()

    # 定义主图
    graph = StateGraph(State)

    # 定义节点
    def start_node(state: State):
        """开始节点"""
        return {"status": "started"}

    def intent_analyze_node(state: State):
        """用户意图分析节点 - 使用LLM进行智能路由"""
        user_query = state["user_query"]

        # 检查user_query是否为空
        if not user_query or not user_query.strip():
            LOGGER.error("用户查询为空")
            return {"route": "unknown", "error": "用户查询为空"}

        LOGGER.info(f"开始分析用户意图: {user_query}")

        routing_prompt = PromptTemplate(
            input_variables=["user_query"],
            template="""请分析以下用户查询，确定用户意图并选择最合适的子图进行路由。   

用户查询: {user_query}

可用的子图:
1. strategy_rd (策略研究) - 用于投资策略研究、投资思路分析、策略制定等
2. stock_analysis (股票分析) - 用于具体股票分析、股票代码查询、公司基本面分析等

请根据以下JSON格式输出路由决策:
{{
    "route": "strategy_rd" 或 "stock_analysis",
    "reason": "简短的路由理由"
}}

只输出JSON，不要其他内容。""",
        )

        try:
            response = llm.invoke(routing_prompt.format(user_query=user_query))
            routing_decision = response.content.strip()
            LOGGER.debug(f"LLM响应: {repr(routing_decision)}")

            route = "unknown"
            reason = ""

            # 路由判断函数
            def decide_route(user_query):
                if (
                    "策略" in user_query
                    or "思路" in user_query
                    or "投资策略" in user_query
                ):
                    return ("strategy_rd", "关键词匹配: 策略相关")
                elif (
                    "股票" in user_query or "分析" in user_query or "公司" in user_query
                ):
                    return ("stock_analysis", "关键词匹配: 股票/公司分析相关")
                else:
                    return ("unknown", "无法确定路由")

            # 尝试解析JSON
            try:
                # 清理响应内容，移除可能的前缀
                LOGGER.debug(f"响应: {routing_decision}")
                # 尝试直接解析
                decision = json.loads(routing_decision)
                route = decision.get("route", "").strip()
                reason = decision.get("reason", "")
                LOGGER.debug(f"JSON解析成功，路由: {route}, 理由: {reason}")

                # 验证route值是否有效
                if route not in ["strategy_rd", "stock_analysis"]:
                    LOGGER.warning(f"解析得到无效路由值: {route}，使用关键词匹配")
                    route, reason = decide_route(user_query)

            except json.JSONDecodeError as e:
                LOGGER.exception(f"JSON解析失败: {str(e)}，使用关键词匹配")
                # 解析失败，使用关键词匹配
                route, reason = decide_route(user_query)
            except Exception as e:
                LOGGER.error(f"JSON处理异常: {str(e)}，使用关键词匹配")
                # 其他异常，使用关键词匹配
                route, reason = decide_route(user_query)

            LOGGER.info(f"路由决策: {route}，理由: {reason}")
            return {"route": route, "routing_reason": reason}

        except Exception as e:
            LOGGER.error(f"意图分析异常: {str(e)}")
            if "策略" in user_query:
                LOGGER.info("异常情况下使用关键词匹配: 策略相关")
                return {
                    "route": "strategy_rd",
                    "routing_reason": "关键词匹配: 策略相关",
                }
            elif "股票" in user_query:
                LOGGER.info("异常情况下使用关键词匹配: 股票相关")
                return {
                    "route": "stock_analysis",
                    "routing_reason": "关键词匹配: 股票相关",
                }
            else:
                return {"route": "unknown", "error": str(e)}

    def strategy_rd_node(state: State):
        """策略研究子图节点"""
        user_query = state.get("user_query", "")
        LOGGER.info(f"执行策略研究子图: {user_query}")
        result = strategy_rd_subgraph.invoke({"query": user_query})
        LOGGER.debug(f"策略研究结果: {result}")
        return {"strategy_result": result}

    def stock_analysis_node(state: State):
        """股票分析子图节点"""
        user_query = state.get("user_query", "")
        LOGGER.info(f"执行股票分析子图: {user_query}")
        result = stock_analysis_subgraph.invoke({"query": user_query})  # type: ignore
        LOGGER.debug(f"股票分析结果: {result}")
        return {"stock_analysis_result": result}

    def summarize_node(state: State):
        """汇总节点 - 使用LLM生成友好的客户返回结果"""
        strategy_result = state.get("strategy_result")
        stock_analysis_result = state.get("stock_analysis_result")
        routing_reason = state.get("routing_reason", "")
        user_query = state.get("user_query", "")
        
        LOGGER.info(f"执行汇总节点，路由类型: {routing_reason}")

        if not strategy_result and not stock_analysis_result:
            LOGGER.warning("无结果可汇总")
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
路由类型: {routing_reason}

策略研究结果:
{strategy_result}

股票分析结果:
{stock_analysis_result}

针对股票分析结果，总结各个视角下最佳买入区间，以表格样式汇总给我。
""",
        )

        try:
            response = llm.invoke(
                summarize_prompt.format(
                    user_query=user_query,
                    strategy_result=strategy_result or "无",
                    stock_analysis_result=stock_analysis_result or "无",
                    routing_reason=routing_reason,
                )
            )
            summary = response.content.strip()
            LOGGER.debug(f"汇总结果: {summary}")
            return {"summary": summary}
        except Exception as e:
            LOGGER.error(f"汇总异常: {str(e)}")
            # 降级处理：拼接原始结果
            summary = ""
            if strategy_result:
                summary += f"策略研究结果: {strategy_result}\n"
            if stock_analysis_result:
                summary += f"股票分析结果: {stock_analysis_result}\n"
            LOGGER.debug(f"降级处理汇总结果: {summary}")
            return {
                "summary": summary if summary else "处理过程中出现异常，请稍后再试。"
            }

    def route_decision(state: State):
        """路由决策函数 - 根据route字段返回目标节点"""
        route = state.get("route", "unknown")
        LOGGER.debug(f"路由决策: {route}")
        if route == "strategy_rd":
            return "strategy_rd"
        elif route == "stock_analysis":
            return "stock_analysis"
        else:
            return "summarize"

    graph.add_node("start", start_node)
    graph.add_node("intent_analyze", intent_analyze_node)
    graph.add_node("strategy_rd", strategy_rd_node)
    graph.add_node("stock_analysis", stock_analysis_node)
    graph.add_node("summarize", summarize_node)

    graph.add_edge(START, "start")
    graph.add_edge("start", "intent_analyze")
    graph.add_conditional_edges(
        "intent_analyze",
        route_decision,
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


main_agent = create_main_agent()
