"""HTR 六步循环子图（ADR-010 Phase 2）。

Observe → Ideate → Select → Dispatch → Executor → Backpropagate → Decide

Phase 2 串行模式：dispatch 只选 1 个假设，executor 内部复用现有 optimize→develop→backtest→refine 逻辑。
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING, Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

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
    from long_earn.ontology import Connector
    from long_earn.services import BacktestService, LoggerService, MemoryService

# ADR-014 任务2：HTR 节点用 ConceptQuery 调 Connector 做图谱关联增强
# ADR-009：算子缺口检测 + 自主研发新算子（gap_detector + operator_dev 接入 HTR）
from long_earn.backtest.operators._loader import list_operators
from long_earn.ontology import ConceptQuery
from long_earn.operator_dev.spec import (
    OperatorSpec,
    OperatorSpecPriority,
)
from long_earn.operator_dev.subgraph import (
    create_operator_dev_subgraph,
)

HTR_MAX_CYCLES = 10
HTR_MAX_DEPTH = 3
HTR_BRANCHING_FACTOR = 3
HTR_MERGE_THRESHOLD = 0.05

# ADR-014 任务4：默认 universe 与股票数量上限
# 默认 main_board+gem（沪深除科创板所有标的），与 DSL 默认值保持一致
_DEFAULT_UNIVERSE = "main_board+gem"
_FINANCIAL_BRIEF_MAX_SYMBOLS = 50

# ADR-009：算子缺口关键词映射（keyword → (op_name, category, intent)）
# 扫描 reflection / improvement_suggestions / insight 文本，命中关键词且算子目录暂缺时
# 产出 OperatorSpec 写入 OperatorBacklog，由 operator_dev 节点消费研发新算子。
_GAP_KEYWORD_MAP: dict[str, tuple[str, str, str]] = {
    "动量": ("momentum", "factor", "计算价格动量（区间收益率）"),
    "rsi": ("rsi", "technical", "RSI 超买超卖相对强弱指标"),
    "macd": ("macd", "technical", "MACD 指标移动平均收敛发散"),
    "布林": ("bollinger", "technical", "布林带上下轨计算"),
    "止盈": ("take_profit", "filter", "动态止盈条件过滤"),
    "止损": ("stop_loss", "filter", "动态止损条件过滤"),
    "成交量": ("volume_weighted", "factor", "成交量加权因子"),
    "波动率": ("realized_volatility", "factor", "已实现波动率计算"),
    "换手率": ("turnover", "factor", "换手率因子"),
    "均线": ("ma", "technical", "移动平均线（MA）"),
    "趋势": ("trend_filter", "filter", "趋势过滤（跌破均线空仓）"),
    "市场状态": ("market_regime", "filter", "市场状态识别（牛熊判定）"),
}


def _parse_universe_from_yaml(strategy_yaml: str) -> str:
    """从 strategy_yaml 解析 universe.type（如 'main_board+gem'）。

    解析失败时返回默认 universe。
    """
    if not strategy_yaml:
        return _DEFAULT_UNIVERSE
    # 简单行扫描，避免引入 yaml 依赖
    in_universe = False
    for line in strategy_yaml.splitlines():
        stripped = line.strip()
        if stripped.startswith("universe:"):
            in_universe = True
            continue
        if in_universe:
            if stripped.startswith("type:"):
                val = stripped[5:].strip().strip("\"'")
                if val:
                    return val
            elif stripped and not stripped.startswith("#"):
                # 离开 universe 块
                in_universe = False
    return _DEFAULT_UNIVERSE


def _fetch_universe_financial_brief(
    connector: Connector | None,
    universe: str,
    aspect: str = "盈利能力",
) -> str:
    """通过 Connector 查 universe 成分股 + 财务面板，返回紧凑摘要文本。

    ADR-014 任务4：让 HTR 节点能把 xtquant 财务数据的统计摘要注入 LLM prompt，
    辅助观察 / 决策。两次 Connector 调用：
    1. subject=universe, aspect="成分股" → 取股票列表
    2. subject="sym1,sym2,...", aspect=aspect → 取财务面板

    返回紧凑文本（mean/median/min/max 摘要），失败返回 "无"。
    """
    if connector is None:
        return "无"
    try:
        # 1. 查成分股
        universe_result = connector.get_concept(
            ConceptQuery(subject=universe, aspect="成分股")
        )
        symbols = (
            universe_result.data
            if isinstance(universe_result.data, list)
            else []
        )
        if not symbols:
            return f"无（universe={universe} 未取到成分股）"
        symbols = symbols[:_FINANCIAL_BRIEF_MAX_SYMBOLS]
        symbols_str = ",".join(symbols)

        # 2. 查财务面板
        result = connector.get_concept(
            ConceptQuery(
                subject=symbols_str,
                aspect=aspect,
                time="2024Q1~2024Q4",
            )
        )
        data = result.data
        if not hasattr(data, "shape") or data.shape[0] == 0:
            return f"无（{universe} 共 {len(symbols)} 只，但 {aspect} 面板为空）"

        # 3. 压缩为统计摘要
        # polars DataFrame：列含 symbol/timestamp + 财务字段
        numeric_cols = [
            c
            for c in data.columns
            if c not in ("symbol", "timestamp", "report_date", "announce_date")
        ]
        lines = [
            f"{aspect} 摘要（{universe} 前 {len(symbols)} 只，最新季度）:"
        ]
        for col in numeric_cols[:6]:
            try:
                vals = data[col].drop_nulls()
                if len(vals) > 0:
                    mean_v = float(vals.mean())
                    median_v = float(vals.median())
                    lines.append(
                        f"  {col}: mean={mean_v:.2f}, median={median_v:.2f}"
                    )
            except Exception:
                continue
        if len(lines) == 1:
            return f"无（{universe} {aspect} 面板无可统计数值列）"
        return "\n".join(lines)
    except Exception as e:
        return f"无（查询失败: {e}）"


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
    connector: Connector | None,
    logger: LoggerService,  # noqa: ARG001
) -> dict:
    """观察阶段 — 分析当前研究状态。

    ADR-014 任务2：注入 Connector 时，用图谱关联增强观察上下文
    （当前最佳假设的关联概念/历史失败案例），LLM 拿到结构化图谱视角。
    """
    tree_data = state.get("hypothesis_tree", {}) or {}
    tree = HypothesisTree.deserialize(tree_data)

    # 构造树快照供 LLM 观察
    best = tree.best_node() or tree.root
    frontier = tree.frontier()

    # ADR-014 阶段 E：查询已剪枝方向（替代旧硬编码 "无"）
    pruned_nodes = [n for n in tree.all_nodes() if n.status == NodeStatus.PRUNED]
    pruned_directions = "\n".join(f"- {n.hypothesis}" for n in pruned_nodes) or "无"

    # ADR-014 任务2：图谱关联增强（当前最佳假设的关联概念）
    related_concepts = "无"
    if connector is not None and best and best.hypothesis:
        try:

            result = connector.get_concept(ConceptQuery(
                subject=best.hypothesis,
                aspect="研究上下文",
            ))
            if result.related_nodes:
                related_concepts = "\n".join(
                    f"- {n.label} ({n.domain.value})" for n in result.related_nodes[:5]
                )
        except Exception:
            # 图谱查询失败不阻塞主流程
            pass

    # ADR-014 任务4：从 strategy_yaml 解析 universe，查 Connector 财务面板摘要
    # 让 LLM 看到 xtquant 财务数据的统计分布，辅助判断策略是否在盈利强的股票池上运行
    strategy_yaml = state.get("strategy_yaml", "") or ""
    universe = _parse_universe_from_yaml(strategy_yaml)
    financial_brief = _fetch_universe_financial_brief(connector, universe)
    if financial_brief != "无":
        if related_concepts == "无":
            related_concepts = financial_brief
        else:
            related_concepts = f"{related_concepts}\n{financial_brief}"

    snapshot = {
        "current_best": best.hypothesis if best else "无",
        "frontier": "\n".join(f"- {n.hypothesis}" for n in frontier) or "无",
        "ancestor_insights": (best.insight if best else "") or "无",
        "pruned_directions": pruned_directions,
        "related_concepts": related_concepts,
    }

    observations = research_agent.observe(snapshot)
    return {"result": str(observations.get("next_focus", ""))}


def _ideate_node(
    state: State,
    research_agent: StrategyResearchAgent,
    memory: MemoryService,
    connector: Connector | None,
    logger: LoggerService,
) -> dict:
    """假设生成 — 基于观察结果 + 历史树洞察（hot-start）生成改进假设。

    ADR-014 任务2：注入 Connector 时，用图谱按策略族检索相似经验
    （替代纯文本 TF-IDF），增强 child_insights 的结构化关联。
    """
    tree_data = state.get("hypothesis_tree", {}) or {}
    tree = HypothesisTree.deserialize(tree_data)

    parent = tree.best_node() or tree.root
    parent_hypothesis = parent.hypothesis if parent else ""

    # 从 state 获取上一轮的观察结果
    observations_raw = state.get("result", "")
    observations: dict[str, Any] = (
        {"next_focus": observations_raw}
        if isinstance(observations_raw, str)
        else observations_raw
    )

    # Hot-start: 检索历史假设树洞察
    child_insights = ""
    try:
        past_trees = memory.search_hypothesis_trees(
            query=parent_hypothesis or "策略优化", k=2
        )
        if past_trees:
            child_insights = "\n".join(
                f"- {t.get('best_direction', '')}: {t.get('best_insight', '')[:100]}"
                for t in past_trees
            )
    except Exception as e:
        if logger:
            logger.warning(f"[HTR-ideate] 历史树检索失败: {e}")

    # ADR-014 任务2：图谱按策略族检索相似经验（增强 child_insights）
    if connector is not None and parent_hypothesis:
        try:

            exp_result = connector.get_concept(ConceptQuery(
                subject=parent_hypothesis,
                aspect="动量族",  # 默认动量族，可根据假设内容扩展
                constraints={"k": 3},
            ))
            if isinstance(exp_result.data, list) and exp_result.data:
                graph_insights = "\n".join(
                    f"- [图谱] {e.get('name', '')}: sharpe={e.get('sharpe', '?')}"
                    for e in exp_result.data
                )
                if child_insights:
                    child_insights = f"{child_insights}\n{graph_insights}"
                else:
                    child_insights = graph_insights
        except Exception as e:
            if logger:
                logger.warning(f"[HTR-ideate] 图谱经验检索失败: {e}")

    # ADR-014 任务4：注入 universe 财务面板摘要，辅助 LLM 生成改进假设
    strategy_yaml = state.get("strategy_yaml", "") or ""
    universe = _parse_universe_from_yaml(strategy_yaml)
    financial_brief = _fetch_universe_financial_brief(connector, universe)
    if financial_brief != "无":
        if child_insights:
            child_insights = f"{child_insights}\n{financial_brief}"
        else:
            child_insights = financial_brief

    hypotheses = research_agent.ideate(
        observations=observations,
        parent_hypothesis=parent_hypothesis,
        child_insights=child_insights,
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
    hypotheses = [{"hypothesis": s, "direction": ""} for s in suggestions]

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
) -> dict | list[Send]:
    """分发阶段 — Phase 5: branching_factor > 1 时用 Send fan-out 并行。

    串行模式（selected_leaves 长度 ≤ 1）：直接传递到 executor。
    并行模式（长度 > 1）：返回 Send 列表，每个假设一个 executor_single 实例。
    """
    selected = state.get("selected_leaves", []) or []
    if logger:
        logger.info(f"[HTR-分发] 分发 {len(selected)} 个假设")

    # 串行模式：≤1 个假设，直接传递到 executor
    if len(selected) <= 1:
        return {"executor_results": []}

    # 并行模式：>1 个假设，用 Send fan-out
    return {"executor_results": []}  # 串行 fallback


def _dispatch_cond(
    state: State,
) -> str | list[Send]:
    """分发路由：多假设 → Send fan-out 到 executor_single；单假设 → executor。"""
    selected = state.get("selected_leaves", []) or []

    if len(selected) > 1:
        return [
            Send(
                "executor_single",
                {
                    **state,
                    "_parallel_node_id": node_id,
                },
            )
            for node_id in selected
        ]
    return "executor"


def _executor_single_wrapper(
    state: dict[str, Any],
    research_agent: StrategyResearchAgent,
    develop_agent: StrategyDevelopAgent,
    backtest_service: BacktestService,
    logger: LoggerService,
) -> dict:
    """Phase 5 并行执行器入口 — 从 Send payload 提取 node_id 调 _executor_single_node。"""
    node_id = state.get("_parallel_node_id", "")
    if not node_id:
        return {"executor_results": []}
    return _executor_single_node(
        state,  # type: ignore[arg-type]
        node_id=node_id,
        research_agent=research_agent,
        develop_agent=develop_agent,
        backtest_service=backtest_service,
        logger=logger,
    )


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

            results.append(
                {
                    "node_id": node_id,
                    "dev_score": dev_score,
                    "backtest_result": backtest_result,
                    "strategy_yaml": strategy_yaml,
                    "optimized_strategy": optimized,
                }
            )

            if logger:
                logger.info(f"[HTR-执行] 节点 {node_id} dev_score={dev_score:.2f}")

        except Exception as e:
            node.status = NodeStatus.FAILED
            if logger:
                logger.error(f"[HTR-执行] 节点 {node_id} 失败: {e}")
            results.append(
                {
                    "node_id": node_id,
                    "error": str(e),
                }
            )

    return {
        "hypothesis_tree": tree.serialize(),
        "executor_results": results,
        "backtest_result": results[0].get("backtest_result", {}) if results else {},
        "strategy_yaml": results[0].get("strategy_yaml", "") if results else "",
        # 把 optimized strategy 写回 state，让下一周期的 optimize_strategy
        # 能看到累积的 evolution_lineage（否则每周期都从空 lineage 开始）
        "strategy": results[0].get("optimized_strategy", {}) if results else {},
    }


def _executor_single_node(  # noqa: PLR0913
    state: State,
    node_id: str,
    research_agent: StrategyResearchAgent,
    develop_agent: StrategyDevelopAgent,
    backtest_service: BacktestService,
    logger: LoggerService,
) -> dict:
    """单个假设的执行器（Phase 5 并行模式 — 每个 Send 一个实例）。

    与 _executor_node 逻辑相同，但只处理一个 node_id，
    返回单个 result dict（reducer _collect_executor_results 会累加）。
    """
    tree_data = state.get("hypothesis_tree", {}) or {}
    tree = HypothesisTree.deserialize(tree_data)
    node = tree.get_node(node_id)
    if node is None:
        return {"executor_results": [{"node_id": node_id, "error": "节点不存在"}]}

    node.status = NodeStatus.RUNNING
    strategy = state.get("strategy", {}) or {}
    suggestions = [node.hypothesis]
    previous_backtest = state.get("backtest_result", {})

    try:
        optimized = research_agent.optimize_strategy(
            strategy=strategy,
            improvement_suggestions=suggestions,
            previous_backtest=previous_backtest,
        )
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

        result = {
            "node_id": node_id,
            "dev_score": dev_score,
            "backtest_result": backtest_result,
            "strategy_yaml": strategy_yaml,
        }
        if logger:
            logger.info(f"[HTR-执行] 节点 {node_id} dev_score={dev_score:.2f}")

    except Exception as e:
        node.status = NodeStatus.FAILED
        if logger:
            logger.error(f"[HTR-执行] 节点 {node_id} 失败: {e}")
        result = {"node_id": node_id, "error": str(e)}

    return {
        "executor_results": [result],
        "hypothesis_tree": tree.serialize(),
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

        insight_text = (
            insight_result.get("insight", "")
            if isinstance(insight_result, dict)
            else ""
        )
        if insight_text:
            node.insight = insight_text
            tree.backpropagate_insight(node_id)

    if logger:
        logger.info("[HTR-反向传播] 洞察已传播")

    return {"hypothesis_tree": tree.serialize()}


def _evaluate_oos_and_merge(  # noqa: PLR0913
    tree: HypothesisTree,
    best_result: dict[str, Any],
    current_best_oos: float | None,
    backtest_service: BacktestService,
    oos_n_splits: int,
    oos_threshold: float,
    logger: LoggerService,
) -> str:
    """对最佳候选跑 OOS 验证并决定 merge/continue。"""
    best_node_id = best_result.get("node_id", "")
    best_yaml = best_result.get("strategy_yaml", "")

    oos_score: float | None = None
    if best_yaml and not best_result.get("error"):
        try:
            oos_result = backtest_service.run_oos(
                strategy_yaml=best_yaml,
                n_splits=oos_n_splits,
            )
            oos_score = oos_result.get("oos_sharpe")
            if logger:
                logger.info(f"[HTR-OOS] 节点 {best_node_id} oos_sharpe={oos_score}")
        except Exception as e:
            if logger:
                logger.warning(f"[HTR-OOS] OOS 验证失败: {e}")

    if best_node_id and oos_score is not None:
        tree.update_evidence(node_id=best_node_id, oos_score=oos_score)

    if oos_score is not None and (
        current_best_oos is None or oos_score > current_best_oos + oos_threshold
    ):
        tree.update_evidence(node_id=best_node_id, status=NodeStatus.MERGED)
        tree.current_best_id = best_node_id
        if logger:
            logger.info(
                f"[HTR-合并] 节点 {best_node_id} 合并 "
                f"(oos={oos_score:.2f} > best={current_best_oos})"
            )
        return "merge"
    return "continue"


def _decide_node(
    state: State,
    research_agent: StrategyResearchAgent,
    backtest_service: BacktestService,
    connector: Connector | None,
    logger: LoggerService,
) -> dict:
    """决策阶段 — 决定 merge/continue/stop。

    Phase 3: 对本轮最佳 dev 候选跑 Walk-Forward OOS，
    oos_score > current_best_oos + threshold → merge。

    ADR-014 任务2：注入 Connector 时，用图谱查相似失败案例注入 tree_state，
    LLM 决策时能看到"历史上类似假设的失败原因"。
    """
    tree_data = state.get("hypothesis_tree", {}) or {}
    tree = HypothesisTree.deserialize(tree_data)
    iteration = state.get("iteration", 0)
    oos_threshold = state.get("oos_threshold", HTR_MERGE_THRESHOLD)
    oos_n_splits = state.get("oos_n_splits", 3)

    best = tree.best_node()
    current_best_oos = best.oos_score if best else None

    results = state.get("executor_results", []) or []
    oos_score: float | None = None
    if not results:
        action = "continue"
    else:
        best_result = max(results, key=lambda r: r.get("dev_score", 0))
        action = _evaluate_oos_and_merge(
            tree,
            best_result,
            current_best_oos,
            backtest_service,
            oos_n_splits,
            oos_threshold,
            logger,
        )
        oos_score = (
            tree.get_node(best_result.get("node_id", "")).oos_score
            if best_result.get("node_id")
            else None
        )

    tree_state = {
        "node_count": tree.node_count,
        "max_depth": max((n.depth for n in tree.all_nodes()), default=0),
        "current_best_oos": current_best_oos,
        "best_dev_score": max((r.get("dev_score", 0) for r in results), default=0.0),
        "best_oos_score": oos_score,
        "cycles_used": iteration,
        "max_cycles": HTR_MAX_CYCLES,
    }

    # ADR-014 任务2：图谱查相似失败案例（注入 tree_state 供 LLM 决策参考）
    if connector is not None and best and best.hypothesis:
        try:

            fail_result = connector.get_concept(ConceptQuery(
                subject=best.hypothesis,
                aspect="动量族",  # 按策略族查经验（含失败案例）
                constraints={"k": 2},
            ))
            if isinstance(fail_result.data, list) and fail_result.data:
                tree_state["similar_experiences"] = "\n".join(
                    f"- {e.get('name', '')}: sharpe={e.get('sharpe', '?')}"
                    for e in fail_result.data
                )
        except Exception as e:
            if logger:
                logger.warning(f"[HTR-decide] 图谱失败案例查询失败: {e}")

    # ADR-014 任务4：注入 universe 财务面板摘要，辅助 LLM 决策
    # （历史经验 + 财务分布 一起供 LLM 参考）
    strategy_yaml = state.get("strategy_yaml", "") or ""
    universe = _parse_universe_from_yaml(strategy_yaml)
    financial_brief = _fetch_universe_financial_brief(connector, universe)
    if financial_brief != "无":
        existing = tree_state.get("similar_experiences", "无")
        if existing == "无":
            tree_state["similar_experiences"] = financial_brief
        else:
            tree_state["similar_experiences"] = f"{existing}\n{financial_brief}"

    llm_action = research_agent.decide(tree_state)
    # 安全兜底：达到最大周期/深度 或 LLM 判定停止 → 强制停止
    if (
        iteration >= HTR_MAX_CYCLES
        or tree_state["max_depth"] >= HTR_MAX_DEPTH
        or llm_action == "stop"
    ):
        action = "stop"

    if logger:
        logger.info(f"[HTR-决策] action={action}, iteration={iteration}")

    next_iteration = iteration + 1
    return {
        "iteration": next_iteration,
        "result": action,
        "hypothesis_tree": tree.serialize(),
    }


def _decide_cond(state: State) -> str:
    """决策路由：merge → save_tree → END; continue → observe; stop → save_tree → END。"""
    action = state.get("result", "continue")
    if action == "continue":
        return "observe"
    return "save_tree"


def create_htr_subgraph(
    context: RuntimeContext,
    *,
    checkpointer: Any = None,
    interrupt_before: list[str] | None = None,
):
    """创建 HTR 六步循环子图。

    Observe → Ideate → Select → Dispatch → Executor → Backpropagate → Decide
    →(continue)→ Observe → ...
    →(merge/stop)→ SaveTree → END

    Args:
        context: 运行时上下文
        checkpointer: LangGraph checkpointer（如 ``SqliteSaver``），启用后
            支持断点续跑与中断恢复。None 时不启用持久化。
        interrupt_before: 在指定节点前暂停（需配合 checkpointer 使用），
            常用断点：``["decide", "save_tree"]``。
    """
    research_agent = StrategyResearchAgent(context=context)
    develop_agent = StrategyDevelopAgent(context=context)

    logger = context.logger
    backtest_service = context.require_backtest()
    memory = context.require_memory()
    # ADR-014 任务2：注入 Connector 供 observe/ideate/decide 图谱关联增强
    connector = context.connector

    workflow = StateGraph(State)

    workflow.add_node("init_tree", partial(_init_tree_node, logger=logger))
    workflow.add_node(
        "observe", partial(_observe_node, research_agent=research_agent,
                           connector=connector, logger=logger)
    )
    workflow.add_node(
        "ideate",
        partial(
            _ideate_node, research_agent=research_agent, memory=memory,
            connector=connector, logger=logger
        ),
    )
    workflow.add_node(
        "select", partial(_select_node, research_agent=research_agent, logger=logger)
    )
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
    # Phase 5: 并行执行器（每个 Send 一个实例）
    workflow.add_node(
        "executor_single",
        partial(
            _executor_single_wrapper,
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
    workflow.add_node(
        "decide",
        partial(
            _decide_node,
            research_agent=research_agent,
            backtest_service=backtest_service,
            connector=connector,
            logger=logger,
        ),
    )
    workflow.add_node(
        "save_tree", partial(_save_tree_node, memory=memory, logger=logger)
    )
    # ADR-009：接入 gap_detector + operator_dev 节点（DI 注入 context，不用模块级全局）
    workflow.add_node(
        "gap_detector", partial(_gap_detector_node, context=context, logger=logger)
    )
    workflow.add_node(
        "operator_dev", partial(_operator_dev_node, context=context, logger=logger)
    )

    workflow.add_edge(START, "init_tree")
    workflow.add_edge("init_tree", "observe")
    workflow.add_edge("observe", "ideate")
    workflow.add_edge("ideate", "select")
    workflow.add_edge("select", "dispatch")

    # Phase 5: dispatch 用条件边 — 单假设 → executor（串行）；多假设 → executor_single（并行 fan-out）
    workflow.add_conditional_edges(
        "dispatch",
        _dispatch_cond,
        {"executor": "executor", "executor_single": "executor_single"},
    )
    workflow.add_edge("executor", "backpropagate")
    workflow.add_edge("executor_single", "backpropagate")
    workflow.add_edge("backpropagate", "decide")

    workflow.add_conditional_edges(
        "decide",
        _decide_cond,
        {"observe": "observe", "save_tree": "save_tree"},
    )

    # ADR-009：save_tree 后接 gap_detector → operator_dev → END
    # 非阻塞：gap_detector 无命中 / operator_dev backlog 为空时快速返回空 dict
    workflow.add_edge("save_tree", "gap_detector")
    workflow.add_edge("gap_detector", "operator_dev")
    workflow.add_edge("operator_dev", END)

    compile_kwargs: dict[str, Any] = {}
    if checkpointer is not None:
        compile_kwargs["checkpointer"] = checkpointer
    if interrupt_before:
        compile_kwargs["interrupt_before"] = interrupt_before
    return workflow.compile(**compile_kwargs)


def _save_tree_node(
    state: State,
    memory: MemoryService,
    logger: LoggerService,
) -> dict:
    """保存假设树到磁盘 + 树摘要回写 SubstanceStore（ADR-010 Phase 4）。"""
    tree_data = state.get("hypothesis_tree", {}) or {}
    tree = HypothesisTree.deserialize(tree_data)

    # 1. 保存完整树到 JSON Store
    store = HypothesisTreeStore()
    store.save(tree)

    # 2. 树摘要回写 SubstanceStore（hot-start 检索用）
    best = tree.best_node()
    best_insight = best.insight if best else ""
    best_direction = best.direction if best else ""
    try:
        memory.save_hypothesis_tree(
            run_id=tree.run_id,
            best_insight=best_insight,
            best_direction=best_direction,
            node_count=tree.node_count,
        )
    except Exception as e:
        if logger:
            logger.warning(f"[HTR] 树摘要回写失败: {e}")

    if logger:
        logger.info(f"[HTR] 假设树已保存: {tree.run_id} ({tree.node_count} 节点)")

    return {}


def _collect_reflection_texts(state: State) -> list[str]:
    """从 state 收集所有反思类文本（reflection / improvement_suggestions / 树节点 insight）。"""
    texts: list[str] = []
    for key in ("reflection", "improvement_suggestions"):
        val = state.get(key)
        if not val:
            continue
        if isinstance(val, list):
            texts.extend(str(v) for v in val)
        else:
            texts.append(str(val))
    # backpropagate 的 insight 存在 hypothesis_tree 节点里
    tree_data = state.get("hypothesis_tree", {}) or {}
    try:
        tree = HypothesisTree.deserialize(tree_data)
        texts.extend(n.insight for n in tree.nodes if n.insight)
    except Exception:
        pass
    return texts


def _gap_detector_node(
    state: State,
    context: RuntimeContext,
    logger: LoggerService,
) -> dict:
    """算子缺口检测节点（ADR-009 接入 HTR）。

    扫描本轮 backpropagate 产出的 insight / reflection / improvement_suggestions
    文本，匹配关键词；命中且算子目录暂缺时，产出 OperatorSpec 写入
    ``context.operator_backlog``，供下游 ``_operator_dev_node`` 消费研发新算子。

    非阻塞：backlog 不可用或无命中时直接返回空列表，不影响主流程。
    与旧 ``strategy_rd/subgraph.py`` 中的 gap_detector 区别：
    不依赖模块级全局变量，改为通过 ``context.operator_backlog`` 注入（DI 原则）。
    """
    backlog = context.operator_backlog
    if backlog is None:
        return {"operator_gaps": []}

    texts = _collect_reflection_texts(state)
    if not texts:
        return {"operator_gaps": []}

    strategy_yaml = state.get("strategy_yaml", "") or state.get(
        "optimized_strategy_yaml", ""
    ) or ""

    # 已注册算子名集合
    try:
        existing_ops = {op["name"] for op in list_operators()}
    except Exception:
        existing_ops = set()

    gaps: list[dict[str, str]] = []
    combined_lower = "\n".join(texts).lower()
    for keyword, (op_name, category, intent) in _GAP_KEYWORD_MAP.items():
        if keyword.lower() not in combined_lower:
            continue
        if op_name in existing_ops:
            continue  # 目录已有，不是缺口

        spec = OperatorSpec(
            name=op_name,
            intent=intent,
            input_fields=["close", "volume"] if "volume" in keyword else ["close"],
            category=category,
            expected_output="每行 float",
            reference_strategy=strategy_yaml[:500],
            motivation=f"HTR 反思命中关键词「{keyword}」，目录暂缺该算子",
            priority=OperatorSpecPriority.NORMAL,
        )
        submitted = backlog.submit(spec)
        if submitted:
            gaps.append({"name": op_name, "intent": intent, "keyword": keyword})
            if logger:
                logger.info(
                    f"[HTR-缺口检测] 发现算子缺口: {op_name} ({category}) — {intent}"
                )

    return {"operator_gaps": gaps}


def _operator_dev_node(
    state: State,  # noqa: ARG001
    context: RuntimeContext,
    logger: LoggerService,
) -> dict:
    """算子研发节点（ADR-009 接入 HTR）。

    消费 ``context.operator_backlog`` 中的 pending spec，调用
    ``create_operator_dev_subgraph`` 跑 spec→审计→因果证明→注册闭环。
    新算子通过 ``register_operator`` 热注册到 ``OPERATOR_REGISTRY``，
    下一轮 HTR 的 develop agent 即可在 operator_factors 中引用。

    非阻塞降级：backlog 为空或子图执行异常时不阻断主流程。
    """
    backlog = context.operator_backlog
    if backlog is None or backlog.is_empty():
        return {"registered_operators": []}

    pending = [s.name for s in backlog.all_specs() if s.status == "pending"]
    if not pending:
        return {"registered_operators": []}

    if logger:
        logger.info(f"[HTR-算子研发] backlog 有 {len(pending)} 个待开发算子: {pending}")

    try:
        op_subgraph = create_operator_dev_subgraph(context, backlog=backlog)
        result = op_subgraph.invoke({})
        registered = result.get("registered_names", []) or []
        if logger and registered:
            logger.info(f"[HTR-算子研发] 新注册算子: {registered}")
        elif logger:
            logger.info(
                "[HTR-算子研发] 本轮无新算子注册（可能因果证明未通过或被 blocked）"
            )
        return {"registered_operators": registered}
    except Exception as e:
        if logger:
            logger.warning(f"[HTR-算子研发] 子图执行失败，跳过: {e}")
        return {"registered_operators": []}
