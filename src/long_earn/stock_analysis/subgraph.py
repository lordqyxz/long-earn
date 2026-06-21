import json
import time
from typing import TYPE_CHECKING, Any

from langgraph.graph import END, START, StateGraph

from long_earn.stock_analysis.agents.buffett_analyst import BuffettAnalyst
from long_earn.stock_analysis.agents.charles_munger_analyst import CharlesMungerAnalyst
from long_earn.stock_analysis.agents.extract_prompt import extract_prompt
from long_earn.stock_analysis.agents.fiske_analyst import FiskeAnalyst
from long_earn.stock_analysis.agents.fund_flow_analyst import FundFlowAnalyst
from long_earn.stock_analysis.agents.petter_analyst import PetterAnalyst
from long_earn.stock_analysis.state import StockAnalysisState

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext


MAX_RETRIES = 3
BASE_DELAY = 1.0


def _retry_with_exponential_backoff(
    func: Any,
    *args: Any,
    max_retries: int = MAX_RETRIES,
    base_delay: float = BASE_DELAY,
    logger: Any | None = None,
    **kwargs: Any,
) -> tuple[Any, int]:
    """指数退避重试装饰器/函数

    Args:
        func: 要重试的函数
        *args: 函数位置参数
        max_retries: 最大重试次数
        base_delay: 基础延迟时间（秒）
        logger: 可选的日志记录器
        **kwargs: 函数关键字参数

    Returns:
        (函数返回值, 最终重试次数)
    """
    last_exception: Exception | None = None

    for attempt in range(max_retries):
        try:
            result = func(*args, **kwargs)
            if attempt > 0 and logger:
                logger.info(f"{func.__name__} 在第 {attempt + 1} 次尝试后成功")
            return result, attempt
        except Exception as e:
            last_exception = e
            if logger:
                logger.warning(f"{func.__name__} 第 {attempt + 1} 次尝试失败: {e!s}")

            if attempt < max_retries - 1:
                delay = base_delay * (2**attempt)
                if logger:
                    logger.info(f"等待 {delay:.1f} 秒后重试...")
                time.sleep(delay)

    if logger and last_exception:
        logger.error(
            f"{func.__name__} 全部 {max_retries} 次尝试均失败: {last_exception!s}"
        )

    return {"error": str(last_exception) if last_exception else "未知错误"}, max_retries


def get_stock_data(
    state: StockAnalysisState, context: "RuntimeContext"
) -> StockAnalysisState:
    """获取股票数据，带重试机制

    Args:
        state: 状态
        context: 运行时上下文
    """
    logger = context.logger
    stock_service = context.require_stock()
    stock_code = state.get("stock_code", "")
    stock_name = state.get("stock_name", "")
    current_retry_count = state.get("retry_count", 0)

    if not stock_code and not stock_name:
        llm_service = context.require_llm()
        query = state.get("query", "")
        formatted_prompt = extract_prompt.format(query=query)
        response = llm_service.invoke(formatted_prompt)
        response_content = (
            response.content if hasattr(response, "content") else str(response)
        )

        try:
            extraction_result = json.loads(response_content)
            stock_name = extraction_result.get("stock_name", "")
            stock_code = extraction_result.get("stock_code", "")
        except json.JSONDecodeError:
            stock_name = ""
            stock_code = ""

    if stock_name and not stock_code:
        stock_code = stock_service.get_stock_code_by_name(stock_name)

    stock_info, info_retries = _retry_with_exponential_backoff(
        stock_service.get_stock_data,
        stock_code,
        logger=logger,
        max_retries=MAX_RETRIES,
        base_delay=BASE_DELAY,
    )
    stock_financial_metrics, metrics_retries = _retry_with_exponential_backoff(
        stock_service.get_financial_metrics,
        stock_code,
        logger=logger,
        max_retries=MAX_RETRIES,
        base_delay=BASE_DELAY,
    )
    price_history, price_retries = _retry_with_exponential_backoff(
        stock_service.get_price_history,
        stock_code,
        logger=logger,
        max_retries=MAX_RETRIES,
        base_delay=BASE_DELAY,
    )

    total_retries = info_retries + metrics_retries + price_retries
    if logger and total_retries > 0:
        logger.info(f"股票 {stock_code} 数据获取完成，总重试次数: {total_retries}")

    stock_data = {
        "stock_info": stock_info,
        "stock_financial_metrics": stock_financial_metrics,
        "price_history": price_history,
    }

    if isinstance(stock_info, dict) and "error" in stock_info:
        stock_data["error"] = stock_info["error"]
    elif (
        isinstance(stock_financial_metrics, dict) and "error" in stock_financial_metrics
    ):
        stock_data["error"] = stock_financial_metrics["error"]

    return {
        "stock_data": stock_data,
        "retry_count": current_retry_count + total_retries,
        "stock_code": stock_code,
        "stock_name": stock_name,
    }


def route_stock_data(state):
    """路由函数：检查股票数据是否包含错误并决定是否重试"""
    stock_data = state.get("stock_data", {})
    retry_count = state.get("retry_count", 0)
    max_retries = 3

    if "error" in stock_data and retry_count < max_retries:
        return "get_stock_data"
    elif "error" in stock_data:
        return "error_handler"
    else:
        return [
            "petter_analysis",
            "charles_munger_analysis",
            "buffett_analysis",
            "fiske_analysis",
            "fund_flow_analysis",
        ]


def petter_analysis_node(state, context: "RuntimeContext"):
    """彼得林奇视角分析"""
    petter_analyst = PetterAnalyst(context=context)
    analysis = petter_analyst.analyze(state.get("stock_data", {}))
    return {"petter_analysis": analysis}


def charles_munger_analysis_node(state, context: "RuntimeContext"):
    """查理芒格视角分析"""
    charles_munger_analyst = CharlesMungerAnalyst(context=context)
    analysis = charles_munger_analyst.analyze(state.get("stock_data", {}))
    return {"charles_munger_analysis": analysis}


def buffett_analysis_node(state, context: "RuntimeContext"):
    """巴菲特视角分析"""
    buffett_analyst = BuffettAnalyst(context=context)
    analysis = buffett_analyst.analyze(state.get("stock_data", {}))
    return {"buffett_analysis": analysis}


def fiske_analysis_node(state, context: "RuntimeContext"):
    """费雪视角分析"""
    fiske_analyst = FiskeAnalyst(context=context)
    analysis = fiske_analyst.analyze(state.get("stock_data", {}))
    return {"fiske_analysis": analysis}


def fund_flow_analysis_node(state, context: "RuntimeContext"):
    """资金流向视角分析（ciccwm 独占数据；不可用时由 prompt 走数据缺失分支）"""
    analyst = FundFlowAnalyst(context=context)
    analysis = analyst.analyze(state.get("stock_data", {}))
    return {"fund_flow_analysis": analysis}


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
    if state.get("fund_flow_analysis"):
        summary += f"资金流向视角：{state['fund_flow_analysis']}\n"
    return {"summary": summary, "result": summary}


def create_stock_analysis_subgraph(context: "RuntimeContext"):
    """创建股票分析子图

    Args:
        context: 运行时上下文
    """
    # 初始化智能体
    workflow = StateGraph(StockAnalysisState)
    workflow.add_node("get_stock_data", lambda state: get_stock_data(state, context))
    workflow.add_node(
        "petter_analysis", lambda state: petter_analysis_node(state, context)
    )
    workflow.add_node(
        "charles_munger_analysis",
        lambda state: charles_munger_analysis_node(state, context),
    )
    workflow.add_node(
        "buffett_analysis", lambda state: buffett_analysis_node(state, context)
    )
    workflow.add_node(
        "fiske_analysis", lambda state: fiske_analysis_node(state, context)
    )
    workflow.add_node(
        "fund_flow_analysis", lambda state: fund_flow_analysis_node(state, context)
    )
    workflow.add_node("summarize", summarize_node)
    workflow.add_node("error_handler", error_handler_node)

    workflow.add_edge(START, "get_stock_data")
    workflow.add_conditional_edges(
        "get_stock_data",
        route_stock_data,
        {
            "get_stock_data": "get_stock_data",
            "petter_analysis": "petter_analysis",
            "charles_munger_analysis": "charles_munger_analysis",
            "buffett_analysis": "buffett_analysis",
            "fiske_analysis": "fiske_analysis",
            "fund_flow_analysis": "fund_flow_analysis",
            "error_handler": "error_handler",
        },
    )

    # 从五个并行节点汇聚到汇总节点
    workflow.add_edge("petter_analysis", "summarize")
    workflow.add_edge("charles_munger_analysis", "summarize")
    workflow.add_edge("buffett_analysis", "summarize")
    workflow.add_edge("fiske_analysis", "summarize")
    workflow.add_edge("fund_flow_analysis", "summarize")
    workflow.add_edge("summarize", END)
    workflow.add_edge("error_handler", END)

    return workflow.compile()
