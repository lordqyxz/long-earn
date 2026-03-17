from langgraph.graph import END, START, StateGraph

from ..tools.backtest import run_backtest
from ..tools.store import save_experience
from .agents.strategy_develop_agent import StrategyDevelopAgent
from .agents.strategy_rd_supervisor import StrategyRdSupervisor
from .agents.strategy_research_agent import StrategyResearchAgent
from .state import State


MAX_CODE_REFINES = 3


def create_strategy_rd_subgraph():
    """创建策略研究子图 - Reflexion 模式 with 代码修复"""
    research_agent = StrategyResearchAgent()
    supervisor = StrategyRdSupervisor()
    develop_agent = StrategyDevelopAgent()

    workflow = StateGraph(State)

    def init_iteration(state):
        """初始化迭代计数器"""
        current_iteration = state.get("iteration", 0)
        develop_agent.clear_error_history()
        return {"iteration": current_iteration}

    def research_node(state):
        """研究节点 - 生成初始策略"""
        query = state.get("query", "")
        strategy = research_agent.research_strategy(query)
        return {
            "strategy": strategy,
            "strategy_name": strategy.get("strategy_name", "CustomStrategy"),
            "design_rationale": strategy.get("description", ""),
        }

    def develop_node(state):
        """开发节点 - 将策略转化为代码"""
        strategy = state.get("strategy", {})
        code = develop_agent.develop_strategy(strategy)
        return {"strategy_code": code, "code_valid": False}

    def backtest_node(state):
        """回测节点 - 执行回测"""
        strategy_code = state.get("strategy_code", "")

        if not strategy_code:
            return {"backtest_result": {"error": "策略代码为空"}, "code_valid": False}

        backtest_result = run_backtest(strategy_code=strategy_code)

        if backtest_result is None:
            return {"backtest_result": {"error": "回测失败"}, "code_valid": False}
        
        if backtest_result.get("error"):
            return {"backtest_result": backtest_result, "code_valid": False}

        return {"backtest_result": backtest_result, "code_valid": True}

    def refine_node(state):
        """代码修复节点 - 根据错误修复代码"""
        strategy = state.get("strategy", {})
        strategy_code = state.get("strategy_code", "")
        backtest_result = state.get("backtest_result", {})
        error_message = backtest_result.get("error", "Unknown error")
        
        refine_count = len(develop_agent.get_error_history())
        if refine_count >= MAX_CODE_REFINES:
            LOGGER.warning(f"已达到最大修复次数 ({MAX_CODE_REFINES})")
            return {"code_valid": False}

        refined_code = develop_agent.refine_code(
            strategy=strategy,
            error_message=error_message,
            failed_code=strategy_code,
        )
        
        return {"strategy_code": refined_code, "code_valid": False}

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
            "explored_paths": reflection_result.get("explored_paths"),
            "selected_direction": reflection_result.get("selected_direction"),
            "tot_enabled": reflection_result.get("tot_enabled", False),
            "primary_issue": reflection_result.get("primary_issue"),
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
        return {"optimized_strategy_code": code, "code_valid": False}

    def backtest_optimized_node(state):
        """回测优化后的策略"""
        optimized_strategy_code = state.get("optimized_strategy_code", "")

        if not optimized_strategy_code:
            return {"backtest_result": {"error": "优化后的策略代码为空"}, "code_valid": False}

        backtest_result = run_backtest(strategy_code=optimized_strategy_code)

        if backtest_result is None:
            return {"backtest_result": {"error": "回测失败"}, "code_valid": False}
        
        if backtest_result.get("error"):
            return {"backtest_result": backtest_result, "code_valid": False}

        return {"backtest_result": backtest_result, "code_valid": True}

    def save_experience_node(state):
        """保存经验节点 - 将成功的策略保存到知识库"""
        strategy_name = state.get("strategy_name", "CustomStrategy")
        design_rationale = state.get("design_rationale", "")
        strategy_code = state.get("strategy_code", "")
        backtest_result = state.get("backtest_result", {})
        reflection = state.get("reflection", "")
        error_history = develop_agent.get_error_history()
        
        success = save_experience(
            strategy_code=strategy_code,
            strategy_name=strategy_name,
            design_rationale=design_rationale,
            backtest_result=backtest_result,
            reflection=reflection,
            error_history=error_history if error_history else None,
        )
        
        return {"experience_saved": success}

    def supervisor_node(state):
        """监督器节点 - 决定是否继续迭代"""
        current_iteration = state.get("iteration", 0)
        max_iterations = state.get("max_iterations", 3)

        strategy = state.get("strategy", {})
        backtest_result = state.get("backtest_result", {})
        reflection = state.get("reflection", "")
        improvement_suggestions = state.get("improvement_suggestions", [])

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
    workflow.add_node("refine", refine_node)
    workflow.add_node("reflection", reflection_node)
    workflow.add_node("optimize", optimize_node)
    workflow.add_node("develop_optimized", develop_optimized_node)
    workflow.add_node("backtest_optimized", backtest_optimized_node)
    workflow.add_node("save_experience", save_experience_node)
    workflow.add_node("supervisor", supervisor_node)

    workflow.add_edge(START, "init")
    workflow.add_edge("init", "research")
    workflow.add_edge("research", "develop")
    workflow.add_edge("develop", "backtest")
    
    workflow.add_edge("backtest", "refine")
    workflow.add_edge("refine", "backtest")
    
    workflow.add_conditional_edges(
        "backtest",
        lambda state: "reflection" if state.get("code_valid", False) else "refine",
        {"refine": "refine", "reflection": "reflection"},
    )
    
    workflow.add_conditional_edges(
        "refine",
        lambda state: "backtest" if len(develop_agent.get_error_history()) < MAX_CODE_REFINES else "reflection",
        {"backtest": "backtest", "reflection": "reflection"},
    )
    
    workflow.add_edge("reflection", "save_experience")
    workflow.add_edge("save_experience", "supervisor")

    workflow.add_conditional_edges(
        "supervisor",
        lambda state: "optimize" if state.get("should_continue", False) else "end",
        {"optimize": "optimize", "end": END},
    )

    workflow.add_edge("optimize", "develop_optimized")
    workflow.add_edge("develop_optimized", "backtest_optimized")
    
    workflow.add_conditional_edges(
        "backtest_optimized",
        lambda state: "reflection" if state.get("code_valid", False) else "refine",
        {"refine": "refine", "reflection": "reflection"},
    )

    return workflow.compile()
