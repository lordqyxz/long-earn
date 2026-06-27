"""HTR 六步循环子图（ADR-010 Phase 2）。

Observe → Ideate → Select → Dispatch → Executor → Backpropagate → Decide

Phase 2 串行模式：dispatch 只选 1 个假设，executor 内部复用现有 optimize→develop→backtest→refine 逻辑。
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any

from langgraph.graph import END, START, StateGraph

from long_earn.strategy_rd.agents.strategy_develop_agent import StrategyDevelopAgent
from long_earn.strategy_rd.agents.strategy_research_agent import StrategyResearchAgent
from long_earn.strategy_rd.hypothesis_tree import (
    HypothesisTree,
    NodeStatus,
)
from long_earn.strategy_rd.state import State
from long_earn.strategy_rd.tree_store import HypothesisTreeStore

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext
    from long_earn.services import BacktestService, LoggerService

HTR_MAX_CYCLES = 10
HTR_MAX_DEPTH = 3
HTR_BRANCHING_FACTOR = 3
HTR_MERGE_THRESHOLD = 0.05


def _init_tree_node(
    state: State,
    logger: LoggerService,
) -> dict:
    """初始化假设树。"""
    query = state.get("query", "")
    tree = HypothesisTree()
    tree.init_root(hypothesis=query, strategy_ref="")

    if logger:
        logger.info(f"[HTR] 初始化假设树 run_id={tree.run_id}")

    return {
        "hypothesis_tree": tree.serialize(),
        "run_id": tree.run_id,
        "current_best_node_id": "root",
        "selected_leaves": [],
        "executor_results": [],
        "oos_threshold": HTR_MERGE_THRESHOLD,
        "oos_n_splits": 3,
        "iteration": 0,
    }


def _observe_node(
    state: State,
    research_agent: StrategyResearchAgent,
    logger: LoggerService,  # noqa: ARG001
) -> dict:
    """观察阶段 — 分析当前研究状态。"""
    tree_data = state.get("hypothesis_tree", {}) or {}
    tree = HypothesisTree.deserialize(tree_data)

    # 构造树快照供 LLM 观察
    best = tree.best_node() or tree.root
    frontier = tree.frontier()

    snapshot = {
        "current_best": best.hypothesis if best else "无",
        "frontier": "\n".join(f"- {n.hypothesis}" for n in frontier) or "无",
        "ancestor_insights": (best.insight if best else "") or "无",
        "pruned_directions": "无",  # Phase 2 简化
    }

    observations = research_agent.observe(snapshot)
    return {"result": str(observations.get("next_focus", ""))}


def _ideate_node(
    state: State,
    research_agent: StrategyResearchAgent,
    logger: LoggerService,  # noqa: ARG001
) -> dict:
    """假设生成 — 基于观察结果生成改进假设。"""
    tree_data = state.get("hypothesis_tree", {}) or {}
    tree = HypothesisTree.deserialize(tree_data)

    parent = tree.best_node() or tree.root
    parent_hypothesis = parent.hypothesis if parent else ""

    # 从 state 获取上一轮的观察结果
    observations_raw = state.get("result", "")
    observations: dict[str, Any] = (
        {"next_focus": observations_raw} if isinstance(observations_raw, str) else observations_raw
    )

    hypotheses = research_agent.ideate(
        observations=observations,
        parent_hypothesis=parent_hypothesis,
        branching_factor=HTR_BRANCHING_FACTOR,
    )

    return {"improvement_suggestions": [h.get("hypothesis", "") for h in hypotheses]}


def _select_node(
    state: State,
    research_agent: StrategyResearchAgent,
    logger: LoggerService,
) -> dict:
    """选择阶段 — 从假设中选择最优的进行验证。"""
    tree_data = state.get("hypothesis_tree", {}) or {}
    tree = HypothesisTree.deserialize(tree_data)

    # 从 ideate 的结果构造假设列表
    suggestions = state.get("improvement_suggestions", []) or []
    hypotheses = [
        {"hypothesis": s, "direction": ""} for s in suggestions
    ]

    selected = research_agent.select(hypotheses, max_select=1)

    # 将选中的假设添加到树中
    parent = tree.best_node() or tree.root
    parent_id = parent.id if parent else "root"

    selected_ids: list[str] = []
    for h in selected:
        node_id = tree.add_child(
            parent_id=parent_id,
            hypothesis=h.get("hypothesis", ""),
            direction=h.get("direction", ""),
        )
        selected_ids.append(node_id)

    if logger:
        logger.info(f"[HTR-选择] 选中 {len(selected_ids)} 个假设添加到树")

    return {
        "hypothesis_tree": tree.serialize(),
        "selected_leaves": selected_ids,
    }


def _dispatch_node(
    state: State,
    logger: LoggerService,
) -> dict:
    """分发阶段 — Phase 2 串行模式，直接传递到 executor。"""
    selected = state.get("selected_leaves", []) or []
    if logger:
        logger.info(f"[HTR-分发] 分发 {len(selected)} 个假设到 executor")
    return {"executor_results": []}


def _executor_node(
    state: State,
    research_agent: StrategyResearchAgent,
    develop_agent: StrategyDevelopAgent,
    backtest_service: BacktestService,
    logger: LoggerService,
) -> dict:
    """执行器 — 对选中的假设执行 optimize→develop→backtest→refine 循环。"""
    tree_data = state.get("hypothesis_tree", {}) or {}
    tree = HypothesisTree.deserialize(tree_data)
    selected = state.get("selected_leaves", []) or []

    results: list[dict[str, Any]] = []
    for node_id in selected:
        node = tree.get_node(node_id)
        if node is None:
            continue

        node.status = NodeStatus.RUNNING

        # 复用现有 optimize 逻辑
        strategy = state.get("strategy", {}) or {}
        suggestions = [node.hypothesis]
        previous_backtest = state.get("backtest_result", {})

        try:
            optimized = research_agent.optimize_strategy(
                strategy=strategy,
                improvement_suggestions=suggestions,
                previous_backtest=previous_backtest,
            )

            # develop → backtest
            strategy_yaml = develop_agent.develop_strategy(optimized)
            backtest_result = backtest_service.run(
                strategy_yaml=strategy_yaml,
                start_date="",
                end_date="",
            )

            dev_score = float(backtest_result.get("sharpe_ratio", 0))

            tree.update_evidence(
                node_id=node_id,
                dev_score=dev_score,
                backtest_result=backtest_result,
                insight=f"dev sharpe={dev_score:.2f}",
            )

            results.append({
                "node_id": node_id,
                "dev_score": dev_score,
                "backtest_result": backtest_result,
                "strategy_yaml": strategy_yaml,
            })

            if logger:
                logger.info(
                    f"[HTR-执行] 节点 {node_id} dev_score={dev_score:.2f}"
                )

        except Exception as e:
            node.status = NodeStatus.FAILED
            if logger:
                logger.error(f"[HTR-执行] 节点 {node_id} 失败: {e}")
            results.append({
                "node_id": node_id,
                "error": str(e),
            })

    return {
        "hypothesis_tree": tree.serialize(),
        "executor_results": results,
        "backtest_result": results[0].get("backtest_result", {}) if results else {},
        "strategy_yaml": results[0].get("strategy_yaml", "") if results else "",
    }


def _backpropagate_node(
    state: State,
    research_agent: StrategyResearchAgent,
    logger: LoggerService,
) -> dict:
    """反向传播 — 将实验结果抽象为洞察并传播到父节点。"""
    tree_data = state.get("hypothesis_tree", {}) or {}
    tree = HypothesisTree.deserialize(tree_data)
    results = state.get("executor_results", []) or []

    for r in results:
        node_id = r.get("node_id", "")
        node = tree.get_node(node_id)
        if node is None:
            continue

        parent = tree.get_node(node.parent_id) if node.parent_id else None
        if parent is None:
            continue

        insight_result = research_agent.backpropagate_insights(
            parent_hypothesis=parent.hypothesis,
            child_results=results,
        )

        insight_text = insight_result.get("insight", "") if isinstance(insight_result, dict) else ""
        if insight_text:
            node.insight = insight_text
            tree.backpropagate_insight(node_id)

    if logger:
        logger.info("[HTR-反向传播] 洞察已传播")

    return {"hypothesis_tree": tree.serialize()}


def _decide_node(
    state: State,
    research_agent: StrategyResearchAgent,
    logger: LoggerService,
) -> dict:
    """决策阶段 — 决定 merge/continue/stop。"""
    tree_data = state.get("hypothesis_tree", {}) or {}
    tree = HypothesisTree.deserialize(tree_data)
    iteration = state.get("iteration", 0)

    best = tree.best_node()
    current_best_oos = best.oos_score if best else None

    # 找本轮最佳 dev 候选
    results = state.get("executor_results", []) or []
    best_dev = max((r.get("dev_score", 0) for r in results), default=0.0)

    tree_state = {
        "node_count": tree.node_count,
        "max_depth": max((n.depth for n in tree.all_nodes()), default=0),
        "current_best_oos": current_best_oos,
        "best_dev_score": best_dev,
        "best_oos_score": None,  # Phase 3 加入 OOS
        "cycles_used": iteration,
        "max_cycles": HTR_MAX_CYCLES,
    }

    action = research_agent.decide(tree_state)

    # 安全兜底：达到最大周期或深度时强制停止
    if iteration >= HTR_MAX_CYCLES or tree_state["max_depth"] >= HTR_MAX_DEPTH:
        action = "stop"

    if logger:
        logger.info(f"[HTR-决策] action={action}, iteration={iteration}")

    next_iteration = iteration + 1
    return {"iteration": next_iteration, "result": action}


def _decide_cond(state: State) -> str:
    """决策路由：merge → save_tree → END; continue → observe; stop → save_tree → END。"""
    action = state.get("result", "continue")
    if action == "continue":
        return "observe"
    return "save_tree"


def create_htr_subgraph(context: RuntimeContext):
    """创建 HTR 六步循环子图。

    Observe → Ideate → Select → Dispatch → Executor → Backpropagate → Decide
    →(continue)→ Observe → ...
    →(merge/stop)→ SaveTree → END
    """
    research_agent = StrategyResearchAgent(context=context)
    develop_agent = StrategyDevelopAgent(context=context)

    logger = context.logger
    backtest_service = context.require_backtest()
    _ = context.require_memory()  # 确保记忆可用

    workflow = StateGraph(State)

    workflow.add_node("init_tree", partial(_init_tree_node, logger=logger))
    workflow.add_node("observe", partial(_observe_node, research_agent=research_agent, logger=logger))
    workflow.add_node("ideate", partial(_ideate_node, research_agent=research_agent, logger=logger))
    workflow.add_node("select", partial(_select_node, research_agent=research_agent, logger=logger))
    workflow.add_node("dispatch", partial(_dispatch_node, logger=logger))
    workflow.add_node(
        "executor",
        partial(
            _executor_node,
            research_agent=research_agent,
            develop_agent=develop_agent,
            backtest_service=backtest_service,
            logger=logger,
        ),
    )
    workflow.add_node(
        "backpropagate",
        partial(_backpropagate_node, research_agent=research_agent, logger=logger),
    )
    workflow.add_node("decide", partial(_decide_node, research_agent=research_agent, logger=logger))
    workflow.add_node("save_tree", partial(_save_tree_node, logger=logger))

    workflow.add_edge(START, "init_tree")
    workflow.add_edge("init_tree", "observe")
    workflow.add_edge("observe", "ideate")
    workflow.add_edge("ideate", "select")
    workflow.add_edge("select", "dispatch")
    workflow.add_edge("dispatch", "executor")
    workflow.add_edge("executor", "backpropagate")
    workflow.add_edge("backpropagate", "decide")

    workflow.add_conditional_edges(
        "decide",
        _decide_cond,
        {"observe": "observe", "save_tree": "save_tree"},
    )

    workflow.add_edge("save_tree", END)

    return workflow.compile()


def _save_tree_node(
    state: State,
    logger: LoggerService,
) -> dict:
    """保存假设树到磁盘。"""
    tree_data = state.get("hypothesis_tree", {}) or {}
    tree = HypothesisTree.deserialize(tree_data)

    store = HypothesisTreeStore()
    store.save(tree)

    if logger:
        logger.info(f"[HTR] 假设树已保存: {tree.run_id} ({tree.node_count} 节点)")

    return {}
