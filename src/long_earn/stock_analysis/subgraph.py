import json
import os

from langchain_core.prompts import PromptTemplate
from langgraph.constants import Send
from langgraph.graph import END, START, StateGraph

from long_earn.stock_analysis.agents.buffett_analyst import BuffettAnalyst
from long_earn.stock_analysis.agents.charles_munger_analyst import CharlesMungerAnalyst
from long_earn.stock_analysis.agents.fiske_analyst import FiskeAnalyst
from long_earn.stock_analysis.agents.petter_analyst import PetterAnalyst
from long_earn.stock_analysis.state import StockAnalysisState
from long_earn.tools.get_stock_info import (
    get_financial_metrics,
    get_price_history,
    get_stock_code_by_name,
)
from long_earn.tools.get_stock_info import get_stock_data as akshare_get_stock_data
from long_earn.utils.llm_factory import create_llm

# 定义提示词模板
extract_prompt = PromptTemplate(
    template="""请从以下用户查询中提取股票名称：

用户查询：{query}

请以JSON格式返回结果，包含以下字段：
- stock_name: 提取的股票名称（如果有）
- stock_code: 提取的股票代码（如果有）
如果无法提取，则返回空字符串。
""",
    input_variables=["query"],
)


def get_stock_data(state: StockAnalysisState) -> StockAnalysisState:
    """获取股票数据，带重试机制"""

    # 首先尝试从状态中获取股票代码
    stock_code = state.get("stock_code", "")
    stock_name = state.get("stock_name", "")
    # 如果没有股票代码，尝试从查询中提取
    if not stock_code:
        if not stock_name:
            # 创建LLM实例
            llm = create_llm(
                llm_type=os.getenv("LLM_TYPE", "ollama"),
                model_name=os.getenv("LLM_MODEL", "qwen3.5:cloud"),
            )
            query = state.get("query", "")
            formatted_prompt = extract_prompt.format(query=query)
            response = llm.invoke(formatted_prompt)
            response_content = (
                response.content if hasattr(response, "content") else str(response)
            )

            # 解析LLM响应
            try:
                extraction_result = json.loads(response_content)
                stock_name = extraction_result.get("stock_name", "")
                stock_code = extraction_result.get("stock_code", "")
            except json.JSONDecodeError:
                stock_name = ""
                stock_code = ""
    if stock_name and not stock_code:
        stock_code = get_stock_code_by_name(stock_name)

    stock_info = akshare_get_stock_data(stock_code)
    stock_financial_metrics = get_financial_metrics(stock_code)
    price_history = get_price_history(stock_code)
    stock_data = {
        "stock_info": stock_info,
        "stock_financial_metrics": stock_financial_metrics,
        "price_history": price_history,
    }
    return {
        "stock_data": stock_data,
        "retry_count": 0,
        "stock_code": stock_code,
        "stock_name": stock_name,
    }


def route_stock_data(state):
    """路由函数：检查股票数据是否包含错误并决定是否重试"""
    stock_data = state.get("stock_data", {})
    retry_count = state.get("retry_count", 0)
    max_retries = 3

    # 如果有错误且还有重试机会，则重试
    if "error" in stock_data and retry_count < max_retries:
        return "get_stock_data"  # 循环回获取数据节点进行重试
    elif "error" in stock_data:
        # 如果有错误但已达到最大重试次数，则转到错误处理
        return "error_handler"
    else:
        # 如果没有错误，则继续正常流程，启动并行分析
        return "parallel_analysis_start"


def petter_analysis_node(state):
    """彼得林奇视角分析"""
    petter_analyst = PetterAnalyst()
    analysis = petter_analyst.analyze(state.get("stock_data", {}))
    return {"petter_analysis": analysis}


def charles_munger_analysis_node(state):
    """查理芒格视角分析"""
    charles_munger_analyst = CharlesMungerAnalyst()
    analysis = charles_munger_analyst.analyze(state.get("stock_data", {}))
    return {"charles_munger_analysis": analysis}


def buffett_analysis_node(state):
    """巴菲特视角分析"""
    buffett_analyst = BuffettAnalyst()
    analysis = buffett_analyst.analyze(state.get("stock_data", {}))
    return {"buffett_analysis": analysis}


def fiske_analysis_node(state):
    """费雪视角分析"""
    fiske_analyst = FiskeAnalyst()
    analysis = fiske_analyst.analyze(state.get("stock_data", {}))
    return {"fiske_analysis": analysis}


def error_handler_node(state):
    """错误处理节点"""
    stock_data = state.get("stock_data", {})
    error_message = stock_data.get("error", "未知错误")
    error_result = f"股票数据分析失败：{error_message}"
    return {"result": error_result, "error": error_result}


def summarize_node(state):
    """汇总分析结果"""
    summary = "股票分析汇总：\n"
    if state.get("petter_analysis"):
        summary += f"彼得林奇视角：{state['petter_analysis']}\n"
    if state.get("charles_munger_analysis"):
        summary += f"查理芒格视角：{state['charles_munger_analysis']}\n"
    if state.get("buffett_analysis"):
        summary += f"巴菲特视角：{state['buffett_analysis']}\n"
    if state.get("fiske_analysis"):
        summary += f"费雪视角：{state['fiske_analysis']}\n"
    return {"summary": summary, "result": summary}


def parallel_analysis_start_node(state):
    """并行分析起始节点，使用 Send API 触发四个分析师真正并行执行"""
    # 使用 Send API 实现真正的并行执行
    return [
        Send("petter_analysis", state),
        Send("charles_munger_analysis", state),
        Send("buffett_analysis", state),
        Send("fiske_analysis", state),
    ]


def create_stock_analysis_subgraph():
    """创建股票分析子图"""
    # 初始化智能体
    workflow = StateGraph(StockAnalysisState)
    workflow.add_node("get_stock_data", get_stock_data)
    workflow.add_node("parallel_analysis_start", parallel_analysis_start_node)
    workflow.add_node("petter_analysis", petter_analysis_node)
    workflow.add_node("charles_munger_analysis", charles_munger_analysis_node)
    workflow.add_node("buffett_analysis", buffett_analysis_node)
    workflow.add_node("fiske_analysis", fiske_analysis_node)
    workflow.add_node("summarize", summarize_node)
    workflow.add_node("error_handler", error_handler_node)

    workflow.add_edge(START, "get_stock_data")
    workflow.add_conditional_edges(
        "get_stock_data",
        route_stock_data,
        {
            "parallel_analysis_start": "parallel_analysis_start",
            "error_handler": "error_handler",
        },
    )
    # 使用 add_conditional_edges 实现真正的并行
    workflow.add_conditional_edges(
        "parallel_analysis_start",
        lambda x: x,  # 直接返回 Send 列表
        ["petter_analysis", "charles_munger_analysis", "buffett_analysis", "fiske_analysis"],
    )

    # 从四个并行节点汇聚到汇总节点（使用空条件边，等待所有节点完成）
    workflow.add_edge("petter_analysis", "summarize")
    workflow.add_edge("charles_munger_analysis", "summarize")
    workflow.add_edge("buffett_analysis", "summarize")
    workflow.add_edge("fiske_analysis", "summarize")
    workflow.add_edge("summarize", END)
    workflow.add_edge("error_handler", END)

    return workflow.compile()
