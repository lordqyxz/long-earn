from langgraph.graph import END, START, StateGraph

from .agents.strategy_develop_agent import StrategyDevelopAgent
from .agents.strategy_rd_supervisor import StrategyRdSupervisor
from .agents.strategy_research_agent import StrategyResearchAgent
from .state import State


def create_strategy_rd_subgraph():
    """创建策略研究子图 - Reflexion 模式"""
    research_agent = StrategyResearchAgent()
    supervisor = StrategyRdSupervisor()
    develop_agent = StrategyDevelopAgent()

    workflow = StateGraph(State)

    def init_iteration(state):
        """初始化迭代计数器"""
        current_iteration = state.get("iteration", 0)
        return {"iteration": current_iteration}

    def research_node(state):
        """研究节点 - 生成初始策略"""
        query = state.get("query", "")
        strategy = research_agent.research_strategy(query)
        return {"strategy": strategy}

    def develop_node(state):
        """开发节点 - 将策略转化为代码"""
        strategy = state.get("strategy", {})
        code = develop_agent.develop_strategy(strategy)
        return {"strategy_code": code}

    def backtest_node(state):
        """回测节点 - 执行回测"""
        strategy_code = state.get("strategy_code", "")

        mock_backtest_result = {
            "total_return": 0.08,
            "annual_return": 0.12,
            "max_drawdown": 0.15,
            "sharpe_ratio": 0.8,
            "win_rate": 0.55,
        }

        return {"backtest_result": mock_backtest_result}

    def reflection_node(state):
        """反思节点 - 分析回测结果并生成改进建议"""
        strategy = state.get("strategy", {})
        backtest_result = state.get("backtest_result", {})

        reflection_result = research_agent.reflect(strategy, backtest_result)

        return {
            "reflection": reflection_result.get("reflection", ""),
            "improvement_suggestions": reflection_result.get(
                "improvement_suggestions", []
            ),
        }

    def optimize_node(state):
        """优化节点 - 根据改进建议优化策略"""
        strategy = state.get("strategy", {})
        improvement_suggestions = state.get("improvement_suggestions", [])

        if isinstance(improvement_suggestions, str):
            improvement_suggestions = [improvement_suggestions]

        optimized_strategy = research_agent.optimize_strategy(
            strategy, improvement_suggestions
        )

        return {"optimized_strategy": optimized_strategy}

    def develop_optimized_node(state):
        """开发优化后的策略代码"""
        optimized_strategy = state.get("optimized_strategy", {})
        code = develop_agent.develop_strategy(optimized_strategy)
        return {"optimized_strategy_code": code}

    def backtest_optimized_node(state):
        """回测优化后的策略"""
        optimized_strategy_code = state.get("optimized_strategy_code", "")

        mock_backtest_result = {
            "total_return": 0.12,
            "annual_return": 0.18,
            "max_drawdown": 0.10,
            "sharpe_ratio": 1.2,
            "win_rate": 0.60,
        }

        return {"backtest_result": mock_backtest_result}

    def supervisor_node(state):
        """监督器节点 - 决定是否继续迭代"""
        current_iteration = state.get("iteration", 0)
        max_iterations = state.get("max_iterations", 3)

        strategy = state.get("strategy", {})
        backtest_result = state.get("backtest_result", {})
        reflection = state.get("reflection", "")
        improvement_suggestions = state.get("improvement_suggestions", [])

        if isinstance(improvement_suggestions, list):
            improvement_suggestions = "\n".join(improvement_suggestions)

        should_continue = supervisor.should_continue(
            iteration=current_iteration,
            max_iterations=max_iterations,
            strategy=strategy,
            backtest_result=backtest_result,
            reflection=reflection,
            improvement_suggestions=improvement_suggestions,
        )

        next_iteration = current_iteration + 1

        return {"should_continue": should_continue, "iteration": next_iteration}

    workflow.add_node("init", init_iteration)
    workflow.add_node("research", research_node)
    workflow.add_node("develop", develop_node)
    workflow.add_node("backtest", backtest_node)
    workflow.add_node("reflection", reflection_node)
    workflow.add_node("optimize", optimize_node)
    workflow.add_node("develop_optimized", develop_optimized_node)
    workflow.add_node("backtest_optimized", backtest_optimized_node)
    workflow.add_node("supervisor", supervisor_node)

    workflow.add_edge(START, "init")
    workflow.add_edge("init", "research")
    workflow.add_edge("research", "develop")
    workflow.add_edge("develop", "backtest")
    workflow.add_edge("backtest", "reflection")
    workflow.add_edge("reflection", "supervisor")

    workflow.add_conditional_edges(
        "supervisor",
        lambda state: "optimize" if state.get("should_continue", False) else "end",
        {"optimize": "optimize", "end": END},
    )

    workflow.add_edge("optimize", "develop_optimized")
    workflow.add_edge("develop_optimized", "backtest_optimized")
    workflow.add_edge("backtest_optimized", "reflection")

    # from langgraph.checkpoint.sqlite import SqliteSaver
    # import sqlite3

    # conn = sqlite3.connect("checkpoint.db", check_same_thread=False)
    # checkpointer = SqliteSaver(conn)

    return workflow.compile()
