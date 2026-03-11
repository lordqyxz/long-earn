from langgraph.graph import StateGraph, START, END
from .state import State
from .agents.petter_analyst import PetterAnalyst
from .agents.charles_munger_analyst import CharlesMungerAnalyst
from .agents.buffett_analyst import BuffettAnalyst
from .agents.fiske_analyst import FiskeAnalyst

def create_stock_analysis_subgraph():
    """创建股票分析子图"""
    # 初始化智能体
    petter_analyst = PetterAnalyst()
    charles_munger_analyst = CharlesMungerAnalyst()
    buffett_analyst = BuffettAnalyst()
    fiske_analyst = FiskeAnalyst()
    
    workflow = StateGraph(State)
    
    def get_stock_data(state):
        """获取股票数据，带重试机制"""
        from ..tools.akshare import get_stock_data as akshare_get_stock_data
        import time
        
        # 获取重试次数，如果没有则默认为3次
        retry_count = state.get('retry_count', 0)
        max_retries = 3
        
        stock_code = state.get('stock_code', '600519')  # 默认贵州茅台
        stock_data = akshare_get_stock_data(stock_code)
        
        # 检查是否包含错误且是否还有重试机会
        if 'error' in stock_data and retry_count < max_retries:
            print(f"获取股票数据失败，正在进行第 {retry_count + 1} 次重试...")
            time.sleep(1)  # 等待1秒后重试
            # 返回相同的状态，但增加重试计数
            return {"stock_data": stock_data, "retry_count": retry_count + 1}
        
        return {"stock_data": stock_data, "retry_count": retry_count}
    
    def route_stock_data(state):
        """路由函数：检查股票数据是否包含错误并决定是否重试"""
        stock_data = state.get('stock_data', {})
        retry_count = state.get('retry_count', 0)
        max_retries = 3
        
        # 如果有错误且还有重试机会，则重试
        if 'error' in stock_data and retry_count < max_retries:
            return 'get_stock_data'  # 循环回获取数据节点进行重试
        elif 'error' in stock_data:
            # 如果有错误但已达到最大重试次数，则转到错误处理
            return 'error_handler'
        else:
            # 如果没有错误，则继续正常流程，启动并行分析
            return 'parallel_analysis_start'
    
    def parallel_analysis_start_node(state):
        """并行分析起始节点，触发四个分析师并行执行"""
        # 此节点只是作为一个触发点，实际的并行执行由图结构控制
        return {}
    
    def petter_analysis_node(state):
        """彼得林奇视角分析"""
        analysis = petter_analyst.analyze(state.get('stock_data', {}))
        return {"petter_analysis": analysis}
    
    def charles_munger_analysis_node(state):
        """查理芒格视角分析"""
        analysis = charles_munger_analyst.analyze(state.get('stock_data', {}))
        return {"charles_munger_analysis": analysis}
    
    def buffett_analysis_node(state):
        """巴菲特视角分析"""
        analysis = buffett_analyst.analyze(state.get('stock_data', {}))
        return {"buffett_analysis": analysis}
    
    def fiske_analysis_node(state):
        """费雪视角分析"""
        analysis = fiske_analyst.analyze(state.get('stock_data', {}))
        return {"fiske_analysis": analysis}
    
    def error_handler_node(state):
        """错误处理节点"""
        stock_data = state.get('stock_data', {})
        error_message = stock_data.get('error', '未知错误')
        error_result = f"股票数据分析失败：{error_message}"
        return {"result": error_result, "error": error_result}
    
    def summarize_node(state):
        """汇总分析结果"""
        summary = "股票分析汇总：\n"
        if state.get('petter_analysis'):
            summary += f"彼得林奇视角：{state['petter_analysis']}\n"
        if state.get('charles_munger_analysis'):
            summary += f"查理芒格视角：{state['charles_munger_analysis']}\n"
        if state.get('buffett_analysis'):
            summary += f"巴菲特视角：{state['buffett_analysis']}\n"
        if state.get('fiske_analysis'):
            summary += f"费雪视角：{state['fiske_analysis']}\n"
        return {"summary": summary, "result": summary}
    
    workflow.add_node('get_stock_data', get_stock_data)
    workflow.add_node('parallel_analysis_start', parallel_analysis_start_node)
    workflow.add_node('petter_analysis', petter_analysis_node)
    workflow.add_node('charles_munger_analysis', charles_munger_analysis_node)
    workflow.add_node('buffett_analysis', buffett_analysis_node)
    workflow.add_node('fiske_analysis', fiske_analysis_node)
    workflow.add_node('summarize', summarize_node)
    workflow.add_node('error_handler', error_handler_node)
    
    workflow.add_edge(START, 'get_stock_data')
    workflow.add_conditional_edges(
        'get_stock_data',
        route_stock_data,
        {
            'parallel_analysis_start': 'parallel_analysis_start',
            'error_handler': 'error_handler'
        }
    )
    # 并行执行四个分析节点
    workflow.add_edge('parallel_analysis_start', 'petter_analysis')
    workflow.add_edge('parallel_analysis_start', 'charles_munger_analysis')
    workflow.add_edge('parallel_analysis_start', 'buffett_analysis')
    workflow.add_edge('parallel_analysis_start', 'fiske_analysis')
    
    # 从四个并行节点汇聚到汇总节点
    # LangGraph会在所有前置节点完成后自动执行后续节点
    workflow.add_edge('petter_analysis', 'summarize')
    workflow.add_edge('charles_munger_analysis', 'summarize')
    workflow.add_edge('buffett_analysis', 'summarize')
    workflow.add_edge('fiske_analysis', 'summarize')
    workflow.add_edge('summarize', END)
    workflow.add_edge('error_handler', END)
    
    from langgraph.checkpoint.sqlite import SqliteSaver
    import sqlite3
    
    conn = sqlite3.connect("checkpoint.db", check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    
    return workflow.compile(checkpointer=checkpointer)