"""HTR 六步循环子图（ADR-010 Phase 2）—— **只读兼容脚手架**

.. deprecated:: ADR-018
    策略研发控制面已翻转为 ``ResearchAgent``（ToG）。
    本模块保留为只读兼容层，仅供旧脚本/测试引用。
    **新代码请使用** ``from long_earn.strategy_rd.research_agent import ResearchAgent``。

Observe → Ideate → Select → Dispatch → Executor → Backpropagate → Decide

Phase 2 串行模式：dispatch 只选 1 个假设，executor 内部复用现有 optimize→develop→backtest→refine 逻辑。
"""

from __future__ import annotations

from functools import partial
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from langgraph.graph import END, START, StateGraph

from long_earn.strategy_rd.agents.strategy_develop_agent import StrategyDevelopAgent
from long_earn.strategy_rd.agents.strategy_research_agent import StrategyResearchAgent
from long_earn.strategy_rd.hypothesis_tree import (
    HypothesisNode,
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

# ADR-012：HTR ideate/backpropagate 节点接入 PersonaRegistry（4 大师策略生成/反思）
from long_earn.skills.personas import PersonaRegistry
from long_earn.skills.personas.protocol import PersonaContext, PersonaResult

# ADR-009 收尾：训练集门（AcceptanceGate）— 优化版 sharpe 严格提升才接受
# ADR-015: 统计过拟合门 — Walk-Forward 稳定性 + DSR + PBO
from long_earn.strategy_optimization.acceptance import AcceptanceGate
from long_earn.strategy_optimization.overfit_gates import (
    BacktestOverfitGate,
    DeflatedSharpeGate,
    WalkForwardStabilityGate,
)

# ADR-016 阶段 2+3：executor 算子缺口逃生口 + 失败路径选择
from long_earn.strategy_rd.escape_hatch import (
    escape_hatch_failure_path,
    escape_hatch_with_retry,
)

HTR_MAX_CYCLES = 10
HTR_MAX_DEPTH = 3
HTR_BRANCHING_FACTOR = 3
HTR_MERGE_THRESHOLD = 0.05

# 已尝试假设摘要截断长度（避免 ideate prompt 膨胀）
_TRUNCATE_HYPOTHESIS_LEN = 120


def _training_window(
    context: RuntimeContext | None,
    backtest_service: BacktestService,
) -> tuple[str, str]:
    """取得 HTR 开发回测的强制训练集窗口。"""
    config = context.config if context is not None else getattr(
        backtest_service, "config", SimpleNamespace()
    )
    start_date = getattr(config, "train_start_date", "")
    end_date = getattr(config, "train_end_date", "")
    if not start_date or not end_date:
        raise ValueError("HTR executor 缺少训练集日期配置")
    return start_date, end_date

# ADR-014 任务4：默认 universe 与股票数量上限
# 默认 main_board+gem（沪深除科创板所有标的），与 DSL 默认值保持一致
_DEFAULT_UNIVERSE = "main_board+gem"
_FINANCIAL_BRIEF_MAX_SYMBOLS = 50


def _invoke_personas(
    research_agent: StrategyResearchAgent,
    persona_context: PersonaContext,
    logger: LoggerService | None,
    log_tag: str,
) -> dict[str, PersonaResult]:
    """调用所有已注册大师的 analyze 方法，返回 name -> PersonaResult。

    ADR-012：HTR ideate/backpropagate 节点共用的大师调用辅助函数。
    大师调用失败时降级为空 dict，不阻塞主流程。

    Args:
        research_agent: 策略研究 Agent（提供 llm_service）
        persona_context: 大师调用上下文（mode/target/backtest_result）
        logger: 日志服务
        log_tag: 日志标签（如 "[HTR-ideate]"）

    Returns:
        name -> PersonaResult 映射；全部失败时返回空 dict
    """
    results: dict[str, PersonaResult] = {}
    try:
        llm = research_agent.llm_service.get_llm()
        personas = PersonaRegistry.create_all(llm)
        for name, persona in personas.items():
            try:
                results[name] = persona.analyze(persona_context)
            except NotImplementedError:
                # 该大师尚未支持此 mode，跳过
                continue
            except Exception as e:
                if logger:
                    logger.warning(f"{log_tag} 大师 {name} 调用失败: {e}")
                continue
    except Exception as e:
        if logger:
            logger.warning(f"{log_tag} 大师注册表初始化失败: {e}")
    return results


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
        symbols = universe_result.data if isinstance(universe_result.data, list) else []
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
        lines = [f"{aspect} 摘要（{universe} 前 {len(symbols)} 只，最新季度）:"]
        for col in numeric_cols[:6]:
            try:
                vals = data[col].drop_nulls()
                if len(vals) > 0:
                    mean_v = float(vals.mean())
                    median_v = float(vals.median())
                    lines.append(f"  {col}: mean={mean_v:.2f}, median={median_v:.2f}")
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
            result = connector.get_concept(
                ConceptQuery(
                    subject=best.hypothesis,
                    aspect="研究上下文",
                )
            )
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


def _enhance_child_insights(
    child_insights: str,
    connector: Connector | None,
    parent_hypothesis: str,
    strategy_yaml: str,
    logger: LoggerService | None,
) -> str:
    """用 Connector 图谱经验 + universe 财务面板摘要增强 child_insights。

    ADR-014 任务2/4：从 _ideate_node 抽取的辅助函数，降低主节点分支复杂度。
    两次增强：图谱按策略族检索相似经验 + universe 财务面板统计摘要。
    任一增强失败都不阻塞主流程。
    """
    # ADR-014 任务2：图谱按策略族检索相似经验
    if connector is not None and parent_hypothesis:
        try:
            exp_result = connector.get_concept(
                ConceptQuery(
                    subject=parent_hypothesis,
                    aspect="动量族",  # 默认动量族，可根据假设内容扩展
                    constraints={"k": 3},
                )
            )
            if isinstance(exp_result.data, list) and exp_result.data:
                graph_insights = "\n".join(
                    f"- [图谱] {e.get('name', '')}: sharpe={e.get('sharpe', '?')}"
                    for e in exp_result.data
                )
                child_insights = (
                    f"{child_insights}\n{graph_insights}"
                    if child_insights
                    else graph_insights
                )
        except Exception as e:
            if logger:
                logger.warning(f"[HTR-ideate] 图谱经验检索失败: {e}")

    # ADR-014 任务4：注入 universe 财务面板摘要
    universe = _parse_universe_from_yaml(strategy_yaml)
    financial_brief = _fetch_universe_financial_brief(connector, universe)
    if financial_brief != "无":
        child_insights = (
            f"{child_insights}\n{financial_brief}"
            if child_insights
            else financial_brief
        )
    return child_insights


def _collect_tried_directions(
    tree: HypothesisTree,
    parent: HypothesisNode | None,
    logger: LoggerService | None,
) -> str:
    """收集 parent 的已尝试子节点方向（failed/pruned）+ 失败原因。

    监督报告指出 8 个子节点假设同质化严重（全部"多因子复合+行业中性化"），
    反向传播未能引导 LLM 探索新方向。本函数把 parent 下所有 failed/pruned
    子节点的假设摘要注入 ideate prompt 的 ``pruned_directions`` 变量，
    让 LLM 显式避开已失败方向。

    ADR-015 A1: 扩展输出，包含 dev_score / oos_score / 失败原因（rejection_reason
    或 step_failures），让 LLM 反思时能看到"为什么失败"而非只看到"该方向失败"。

    Returns:
        格式化的方向列表字符串（每行一个方向），无已尝试方向时返回 ``"无"``。
    """
    if parent is None:
        return "无"

    tried: list[str] = []
    # 递归收集 parent 下所有 FAILED/PRUNED 子孙节点（不只直接子节点）
    failed_nodes = _collect_failed_descendants(tree, parent)

    for child in failed_nodes:
        hypothesis = child.hypothesis.strip()
        if not hypothesis:
            continue
        # 截断长假设避免 prompt 膨胀
        if len(hypothesis) > _TRUNCATE_HYPOTHESIS_LEN:
            hypothesis = hypothesis[: _TRUNCATE_HYPOTHESIS_LEN - 3] + "..."

        # 构造失败原因摘要
        reason_parts: list[str] = []
        if child.dev_score != 0.0:
            reason_parts.append(f"dev_sharpe={child.dev_score:.2f}")
        if child.oos_score is not None:
            reason_parts.append(f"oos_sharpe={child.oos_score:.2f}")
        if child.insight:
            # insight 现在含失败原因（A1 改动）
            insight_brief = child.insight[:80]
            reason_parts.append(f"原因:{insight_brief}")

        # 从 backtest_result 提取 step_failures（如存在）
        backtest_result = child.backtest_result or {}
        diag = backtest_result.get("strategy_diagnostics", {}) or {}
        step_failures = diag.get("step_failures", []) or []
        if step_failures:
            first_failure = (
                step_failures[0] if isinstance(step_failures[0], dict) else {}
            )
            step_error = str(first_failure.get("error", ""))[:60]
            if step_error:
                reason_parts.append(f"step_error:{step_error}")

        reason_str = " | ".join(reason_parts) if reason_parts else "无详情"
        tried.append(f"- [{child.status.value}] {hypothesis} ({reason_str})")

    if not tried:
        return "无"

    if logger:
        logger.info(
            f"[HTR-ideate] 检测到 {len(tried)} 个已尝试/失败方向，"
            f"将注入 ideate prompt 避免重复"
        )
    return "\n".join(tried)


def _collect_failed_descendants(
    tree: HypothesisTree,
    node: HypothesisNode,
) -> list[HypothesisNode]:
    """递归收集 node 下所有 FAILED/PRUNED 子孙节点。

    ADR-015 A1: 旧实现只看 parent 直接子节点，无法感知孙子节点的失败。
    本函数递归遍历整棵子树，让 LLM 看到完整的失败历史。
    """
    failed: list[HypothesisNode] = []
    for child_id in node.children_ids:
        child = tree.get_node(child_id)
        if child is None:
            continue
        if child.status in (NodeStatus.FAILED, NodeStatus.PRUNED):
            failed.append(child)
        # 即使子节点已失败，也递归收集孙子节点（可能含更具体的失败原因）
        failed.extend(_collect_failed_descendants(tree, child))
    return failed


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

    # ADR-015 B4: Arbor expand 动作 — Coordinator 指定下一轮的 parent
    # None 时退化为 tree.best_node()（向后兼容）
    next_parent_id = state.get("next_parent_id")
    if next_parent_id:
        parent = tree.get_node(next_parent_id)
        if parent is None:
            if logger:
                logger.warning(
                    f"[HTR-ideate] next_parent_id={next_parent_id} 不存在，"
                    f"退化为 best_node()"
                )
            parent = tree.best_node() or tree.root
        elif logger:
            logger.info(
                f"[HTR-ideate] Arbor expand: 在指定节点 {next_parent_id} "
                f"(hypothesis={parent.hypothesis[:40]}) 下展开新分支"
            )
    else:
        parent = tree.best_node() or tree.root
    parent_hypothesis = parent.hypothesis if parent else ""

    # 收集 parent 的已尝试子节点方向（failed/pruned）—— 避免 LLM 重复生成
    # 同质化假设。监督报告显示 8 个子节点全部围绕"多因子复合+行业中性化"，
    # 反向传播未能引导 LLM 探索新方向，需显式注入已尝试方向。
    tried_directions = _collect_tried_directions(tree, parent, logger)

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

    # ADR-014 任务2/4：Connector 图谱经验 + 财务面板增强 child_insights
    strategy_yaml = state.get("strategy_yaml", "") or ""
    child_insights = _enhance_child_insights(
        child_insights=child_insights,
        connector=connector,
        parent_hypothesis=parent_hypothesis,
        strategy_yaml=strategy_yaml,
        logger=logger,
    )

    # ADR-012：调用 4 大师 strategy_generate mode 提供策略生成建议
    # 大师针对当前父假设各自给出视角，注入 ideate prompt 增强假设多样性
    master_hints = _invoke_personas(
        research_agent,
        PersonaContext(
            mode="strategy_generate",
            target={
                "query": parent_hypothesis or "策略优化",
                "knowledge_context": child_insights,
            },
        ),
        logger,
        "[HTR-ideate]",
    )

    if logger and master_hints:
        logger.info(
            f"[HTR-ideate] 大师策略生成建议完成: {len(master_hints)} 位提供视角"
        )
    if logger and tried_directions != "无":
        logger.info(
            f"[HTR-ideate] 注入已尝试方向避免重复: {len(tried_directions.splitlines())} 个"
        )

    # ADR-015 B5: 家族失效检测 — 连续 _FAMILY_STAGNATION_THRESHOLD 轮无改善
    # 时标记家族失效，引导 ideate 生成异族策略
    family_state = _detect_family_stagnation(state, logger)

    hypotheses = research_agent.ideate(
        observations=observations,
        parent_hypothesis=parent_hypothesis,
        child_insights=child_insights,
        pruned_directions=tried_directions,
        branching_factor=HTR_BRANCHING_FACTOR,
        master_hints=master_hints if master_hints else None,
        family_state=family_state,
    )

    # ADR-015 B3: 同时保留完整 hypotheses dict（含 family 字段）和向后兼容的 suggestions
    return {
        "improvement_suggestions": [h.get("hypothesis", "") for h in hypotheses],
        "improvement_hypotheses": hypotheses,
    }


# ADR-015 B5: 家族失效检测阈值（连续 N 轮无改善触发家族切换）
_FAMILY_STAGNATION_THRESHOLD = 3


def _detect_family_stagnation(
    state: State,
    logger: LoggerService | None,
) -> str:
    """检测当前策略家族是否连续多轮无改善。

    ADR-015 B5: 旧 subgraph.py 的家族失效检测在 HTR 子图迁移时丢失，
    ``history_return`` / ``round_history`` 成为死代码。本函数重新激活，
    通过 round_history 统计连续无改善轮数，达到阈值时返回家族失效信号。

    Returns:
        空字符串（家族正常）/ "家族失效（连续 N 轮无改善，请生成异族策略）"。
    """
    round_history = state.get("round_history") or []
    if not round_history:
        return ""

    # 取最近 N 轮的 recent_return，统计连续无改善
    recent_returns = [
        float(r.get("recent_return", 0.0))
        for r in round_history[-_FAMILY_STAGNATION_THRESHOLD:]
        if isinstance(r, dict)
    ]
    if len(recent_returns) < _FAMILY_STAGNATION_THRESHOLD:
        return ""

    # 连续无改善：每轮 recent_return <= 前一轮（或都为负）
    stagnant = all(r <= 0 for r in recent_returns) or all(
        recent_returns[i] <= recent_returns[i - 1]
        for i in range(1, len(recent_returns))
    )
    if not stagnant:
        return ""

    msg = (
        f"家族失效（连续 {len(recent_returns)} 轮无改善，"
        f"recent_returns={recent_returns}，请生成异族策略假设）"
    )
    if logger:
        logger.warning(f"[HTR-ideate] {msg}")
    return msg


def _select_node(
    state: State,
    research_agent: StrategyResearchAgent,
    logger: LoggerService,
    max_select: int = 1,
) -> dict:
    """选择阶段 — 从假设中选择最优的进行验证。

    Args:
        max_select: 每轮选择的假设数。1=串行（向后兼容）；
            >1 激活 LangGraph Send 并行 fan-out（ADR-010 Phase 5）。
    """
    tree_data = state.get("hypothesis_tree", {}) or {}
    tree = HypothesisTree.deserialize(tree_data)

    # 从 ideate 的结果构造假设列表
    # ADR-015 B3: 优先使用 improvement_hypotheses（含 family 字段），
    # 退化到 improvement_suggestions（向后兼容）
    hypotheses = state.get("improvement_hypotheses") or []
    if not hypotheses:
        suggestions = state.get("improvement_suggestions", []) or []
        hypotheses = [{"hypothesis": s, "direction": ""} for s in suggestions]

    selected = research_agent.select(hypotheses, max_select=max_select)

    # 将选中的假设添加到树中
    # ADR-015 B4: 使用 next_parent_id（与 _ideate_node 保持一致）
    next_parent_id = state.get("next_parent_id")
    if next_parent_id:
        parent = tree.get_node(next_parent_id)
        if parent is None:
            parent = tree.best_node() or tree.root
    else:
        parent = tree.best_node() or tree.root
    parent_id = parent.id if parent else "root"

    selected_ids: list[str] = []
    for h in selected:
        node_id = tree.add_child(
            parent_id=parent_id,
            hypothesis=h.get("hypothesis", ""),
            direction=h.get("direction", ""),
            family=h.get("family", ""),
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
    """分发阶段 - ADR-010 阶段 5 收尾（2026-08）：删除 Send fan-out 伪并行，
    始终走串行 executor 节点。多候选由 _executor_node 内部三阶段批量并行处理
    （阶段1 逐候选 develop -> 阶段2 run_candidates 进程池批量回测 -> 阶段3 gate）。
    """
    selected = state.get("selected_leaves", []) or []
    if logger:
        logger.info(f"[HTR-分发] 分发 {len(selected)} 个假设（executor 内部批量）")
    return {"executor_results": []}


def _dispatch_cond(
    state: State,  # noqa: ARG001
) -> str:
    """分发路由 - ADR-010 阶段 5 收尾：始终返回 executor（删除 Send fan-out）。"""
    return "executor"


def _handle_executor_exception(  # noqa: PLR0913
    error: Exception,
    strategy_yaml: str,
    optimized: dict[str, Any],
    hypothesis: str,
    node_id: str,
    context: RuntimeContext | None,
    develop_agent: StrategyDevelopAgent,
    backtest_service: BacktestService,
    previous_backtest: dict[str, Any],
    gate: AcceptanceGate | None,
    logger: LoggerService | None,
) -> dict[str, Any]:
    """处理 executor 异常 — 逃生口入口（ADR-016 阶段 2+3）。

    阶段 2 算子缺口：
    - 算子缺失 + 研发成功 + 重试成功 → 走 AcceptanceGate 后返回 result
    - 算子缺失 + 研发失败/重试失败 → 返回 error result（含审计信息）

    阶段 3 失败路径：
    - 非算子缺失错误 → LLM 分类失败类型
    - fixable → refine + 重试 backtest → 走 AcceptanceGate
    - directional → 返回 error result（含方向性失败标记）
    """
    # 无 context 或无 strategy_yaml 时，不走逃生口（无法提取 reference_strategy）
    if context is None or not strategy_yaml:
        return {"node_id": node_id, "error": str(error)}

    train_start, train_end = _training_window(context, backtest_service)

    # ── 阶段 2：算子缺口逃生口 ──────────────────────────────────
    # 检测算子缺口 → 同步研发 → 重试 develop + backtest
    hatch_outcome = escape_hatch_with_retry(
        error=error,
        strategy_yaml=strategy_yaml,
        optimized=optimized,
        hypothesis=hypothesis,
        context=context,
        develop_func=develop_agent.develop_strategy,
        backtest_func=lambda yaml: backtest_service.run(
            strategy_yaml=yaml,
            start_date=train_start,
            end_date=train_end,
        ),
        logger=logger,
    )

    # 非算子缺失错误 → 进入阶段 3 失败路径逃生口
    if not hatch_outcome.get("escape_hatch_triggered"):
        return _apply_failure_path_escape_hatch(
            error=error,
            strategy_yaml=strategy_yaml,
            optimized=optimized,
            hypothesis=hypothesis,
            node_id=node_id,
            develop_agent=develop_agent,
            backtest_service=backtest_service,
            previous_backtest=previous_backtest,
            gate=gate,
            logger=logger,
            train_start=train_start,
            train_end=train_end,
        )

    # 算子研发失败或重试失败
    if "error" in hatch_outcome:
        return {
            "node_id": node_id,
            "error": hatch_outcome["error"],
            "escape_hatch_triggered": True,
        }

    # 算子缺口重试成功 — 走 AcceptanceGate 校验
    return _process_retry_success(
        retry_yaml=hatch_outcome.get("strategy_yaml", ""),
        retry_backtest=hatch_outcome.get("backtest_result", {}),
        optimized=optimized,
        node_id=node_id,
        previous_backtest=previous_backtest,
        gate=gate,
        logger=logger,
        log_prefix="[HTR-执行] 节点 {node_id} 逃生口重试成功",
    )


def _apply_failure_path_escape_hatch(  # noqa: PLR0913
    error: Exception,
    strategy_yaml: str,
    optimized: dict[str, Any],
    hypothesis: str,
    node_id: str,
    develop_agent: StrategyDevelopAgent,
    backtest_service: BacktestService,
    previous_backtest: dict[str, Any],
    gate: AcceptanceGate | None,
    logger: LoggerService | None,
    train_start: str,
    train_end: str,
) -> dict[str, Any]:
    """阶段 3 失败路径逃生口 — LLM 分类后选择 refine 或 prune。"""
    failure_outcome = escape_hatch_failure_path(
        error=error,
        strategy_yaml=strategy_yaml,
        optimized=optimized,
        hypothesis=hypothesis,
        llm_service=develop_agent.llm_service,
        refine_func=develop_agent.refine_code,
        backtest_func=lambda yaml: backtest_service.run(
            strategy_yaml=yaml,
            start_date=train_start,
            end_date=train_end,
        ),
        logger=logger,
    )

    # directional 失败 → 直接返回错误
    if "error" in failure_outcome:
        return {
            "node_id": node_id,
            "error": failure_outcome["error"],
            "escape_hatch_triggered": True,
            "failure_path": failure_outcome.get("failure_path", ""),
        }

    # fixable + refine 成功 → 走 AcceptanceGate 校验
    return _process_retry_success(
        retry_yaml=failure_outcome.get("strategy_yaml", ""),
        retry_backtest=failure_outcome.get("backtest_result", {}),
        optimized=optimized,
        node_id=node_id,
        previous_backtest=previous_backtest,
        gate=gate,
        logger=logger,
        log_prefix="[HTR-执行] 节点 {node_id} 失败路径 refine 重试成功",
    )


def _process_retry_success(  # noqa: PLR0913
    retry_yaml: str,
    retry_backtest: dict[str, Any],
    optimized: dict[str, Any],
    node_id: str,
    previous_backtest: dict[str, Any],
    gate: AcceptanceGate | None,
    logger: LoggerService | None,
    log_prefix: str,
) -> dict[str, Any]:
    """处理逃生口重试成功的结果 — 走 AcceptanceGate 校验。"""
    if gate is not None:
        acceptance = gate.evaluate(previous_backtest, retry_backtest)
        if not acceptance.accepted:
            if logger:
                logger.warning(
                    f"[HTR-执行] 节点 {node_id} 逃生口重试被 AcceptanceGate 拒绝: "
                    f"{acceptance.reason}"
                )
            return {
                "node_id": node_id,
                "rejected": True,
                "rejection_reason": acceptance.reason,
                "backtest_result": retry_backtest,
                "optimized_strategy": optimized,
                "escape_hatch_triggered": True,
            }

    dev_score = float(retry_backtest.get("sharpe_ratio", 0))
    if logger:
        logger.info(f"{log_prefix.format(node_id=node_id)} dev_score={dev_score:.2f}")
    return {
        "node_id": node_id,
        "dev_score": dev_score,
        "backtest_result": retry_backtest,
        "strategy_yaml": retry_yaml,
        "optimized_strategy": optimized,
        "escape_hatch_triggered": True,
    }


def _develop_one_candidate(  # noqa: PLR0913
    node: Any,
    strategy: dict[str, Any],
    previous_backtest: dict[str, Any],
    research_agent: StrategyResearchAgent,
    develop_agent: StrategyDevelopAgent,
    backtest_service: BacktestService,
    logger: LoggerService,
    gate: AcceptanceGate | None,
    context: RuntimeContext | None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """阶段1辅助：对单个候选 optimize->develop。

    成功返回 (developed_dict, None)；失败走逃生口返回 (None, escape_result)。
    逃生口 result 带 escape_hatch_triggered 标志，阶段3跳过避免 double-gate。
    """
    optimized: dict[str, Any] = {}
    strategy_yaml = ""
    try:
        optimized = research_agent.optimize_strategy(
            strategy=strategy,
            improvement_suggestions=[node.hypothesis],
            previous_backtest=previous_backtest,
        )
        strategy_yaml = develop_agent.develop_strategy(optimized)
        return (
            {
                "node_id": node.id,
                "strategy_yaml": strategy_yaml,
                "optimized": optimized,
            },
            None,
        )
    except Exception as e:
        if logger:
            logger.error(f"[HTR-执行] 节点 {node.id} 失败: {e}")
        result = _handle_executor_exception(
            error=e,
            strategy_yaml=strategy_yaml,
            optimized=optimized,
            hypothesis=node.hypothesis,
            node_id=node.id,
            context=context,
            develop_agent=develop_agent,
            backtest_service=backtest_service,
            previous_backtest=previous_backtest,
            gate=gate,
            logger=logger,
        )
        return None, result


def _gate_check_candidate(
    developed: dict[str, Any],
    backtest_result: dict[str, Any],
    previous_backtest: dict[str, Any],
    gate: AcceptanceGate | None,
    logger: LoggerService,
) -> dict[str, Any]:
    """阶段3辅助：对单个候选做 AcceptanceGate 校验，返回 result dict。"""
    if gate is not None:
        acceptance = gate.evaluate(previous_backtest, backtest_result)
        if not acceptance.accepted:
            if logger:
                logger.warning(
                    f"[HTR-执行] 节点 {developed['node_id']} 被 AcceptanceGate 拒绝: "
                    f"{acceptance.reason}"
                )
            return {
                "node_id": developed["node_id"],
                "rejected": True,
                "rejection_reason": acceptance.reason,
                "backtest_result": backtest_result,  # ADR-015 A1
                "optimized_strategy": developed["optimized"],
            }
    dev_score = float(backtest_result.get("sharpe_ratio", 0))
    if logger:
        logger.info(f"[HTR-执行] 节点 {developed['node_id']} dev_score={dev_score:.2f}")
    return {
        "node_id": developed["node_id"],
        "dev_score": dev_score,
        "backtest_result": backtest_result,
        "strategy_yaml": developed["strategy_yaml"],
        "optimized_strategy": developed["optimized"],
    }


def _executor_node(  # noqa: PLR0913
    state: State,
    research_agent: StrategyResearchAgent,
    develop_agent: StrategyDevelopAgent,
    backtest_service: BacktestService,
    logger: LoggerService,
    gate: AcceptanceGate | None = None,
    context: RuntimeContext | None = None,
) -> dict:
    """执行器 - 对选中的假设执行 optimize->develop->backtest->refine 循环。

    ADR-009 收尾：接入 AcceptanceGate 作为训练集门。优化版回测后立即校验
    ``o_sharpe > b_sharpe + eps``，未通过的候选标记 rejected 并跳过 evidence
    更新，避免无效候选进入下游 OOS 合并门浪费 held-out 测试集回测算力。
    与 _evaluate_oos_and_merge 形成双层防护：训练集门 + 测试集门。

    ADR-016 阶段 2：executor 算子缺口逃生口。backtest 因算子缺失失败时，
    在 executor 内部同步研发算子并重试，不中断六步循环。

    ADR-010 阶段 5 收尾（2026-08）：删除 Send fan-out 伪并行，改为三阶段批量：
    ①逐候选 optimize->develop（LLM IO 密集）；②backtest_service.run_candidates
    批量回测（进程池真并行 + 共享面板，ADR-008 B5/B6）；③逐候选 AcceptanceGate。
    逃生口路径（_handle_executor_exception）保持原逐候选回测语义，不进批量，
    其产出的 result 带 escape_hatch_triggered 标志，阶段③跳过避免 double-gate。
    """
    tree_data = state.get("hypothesis_tree", {}) or {}
    tree = HypothesisTree.deserialize(tree_data)
    selected = state.get("selected_leaves", []) or []

    # 所有候选共享的 state 级输入（无候选间状态耦合）
    strategy = state.get("strategy", {}) or {}
    previous_backtest = state.get("backtest_result", {})
    train_start, train_end = _training_window(context, backtest_service)

    # ── 阶段 1：逐候选 optimize -> develop（LLM，IO 密集）──
    developed: list[dict[str, Any]] = []
    escape_results: list[dict[str, Any]] = []
    for node_id in selected:
        node = tree.get_node(node_id)
        if node is None:
            continue
        dev, escape = _develop_one_candidate(
            node=node,
            strategy=strategy,
            previous_backtest=previous_backtest,
            research_agent=research_agent,
            develop_agent=develop_agent,
            backtest_service=backtest_service,
            logger=logger,
            gate=gate,
            context=context,
        )
        if dev is not None:
            developed.append(dev)
        if escape is not None:
            escape_results.append(escape)

    # ── 阶段 2：批量回测（CPU 密集，进程池真并行 + 共享面板）──
    results: list[dict[str, Any]] = list(escape_results)
    if developed:
        yamls = [d["strategy_yaml"] for d in developed]
        try:
            outcomes = backtest_service.run_candidates(
                strategy_yamls=yamls,
                start_date=train_start,
                end_date=train_end,
            )
        except Exception as e:
            if logger:
                logger.error(f"[HTR-执行] 批量回测失败，降级为逐候选失败: {e}")
            outcomes = [
                {"error": str(e), "error_category": "engine_error"} for _ in developed
            ]

        # ── 阶段 3：逐候选 AcceptanceGate（语义不变，backtest 结果就绪后校验）──
        for d, outcome in zip(developed, outcomes, strict=True):
            results.append(
                _gate_check_candidate(
                    developed=d,
                    backtest_result=outcome,
                    previous_backtest=previous_backtest,
                    gate=gate,
                    logger=logger,
                )
            )

    # results[0] -> best 选取（ADR-010 阶段 5 收尾修正）：
    # 让下一轮 optimize 的 previous_backtest 基线为本轮最佳候选而非随机首个，
    # 与 _decide 的 best_result 选取一致。失败/rejected 候选 dev_score=0 不优先。
    best_result = max(results, key=lambda r: r.get("dev_score", 0.0), default=None)
    return {
        "executor_results": results,
        "backtest_result": best_result.get("backtest_result", {})
        if best_result
        else {},
        "strategy_yaml": best_result.get("strategy_yaml", "") if best_result else "",
        # 把 optimized strategy 写回 state，让下一周期的 optimize_strategy
        # 能看到累积的 evolution_lineage（否则每周期都从空 lineage 开始）
        "strategy": best_result.get("optimized_strategy", {}) if best_result else {},
    }


def _backpropagate_node(
    state: State,
    research_agent: StrategyResearchAgent,
    logger: LoggerService,
) -> dict:
    """反向传播 — 将实验结果抽象为洞察并传播到父节点。

    ADR-012：对每个有回测结果的节点调用 4 大师 strategy_review mode 反思，
    将大师视角注入 backpropagate_insights prompt，让反思融合量化数据与投资
    大师视角。大师调用失败时降级为原行为（无大师视角），不阻塞反思流程。
    """
    tree_data = state.get("hypothesis_tree", {}) or {}
    tree = HypothesisTree.deserialize(tree_data)
    results = state.get("executor_results", []) or []
    strategy = state.get("strategy", {}) or {}

    for r in results:
        node_id = r.get("node_id", "")
        node = tree.get_node(node_id)
        if node is None:
            continue

        # tree evidence/status 更新（从 _executor_node/_executor_single_node 迁移来）
        # 并行 fan-out 时各 executor 不写 tree，此处单点更新避免 last_value 覆盖。
        # ADR-015 A1: rejected 节点也保留 backtest_result + rejection_reason，
        # 让下游 _collect_tried_directions 能把失败原因传给 ideate prompt
        if r.get("rejected") or r.get("error"):
            rejection_reason = r.get("rejection_reason", "") or r.get("error", "")
            tree.update_evidence(
                node_id=node_id,
                status=NodeStatus.FAILED,
                backtest_result=r.get("backtest_result", {}) or {},
                insight=f"失败原因: {rejection_reason}" if rejection_reason else "",
            )
        else:
            dev_score = float(r.get("dev_score", 0.0))
            tree.update_evidence(
                node_id=node_id,
                dev_score=dev_score,
                backtest_result=r.get("backtest_result", {}) or {},
                insight=f"dev sharpe={dev_score:.2f}",
            )

        parent = tree.get_node(node.parent_id) if node.parent_id else None
        if parent is None:
            continue

        # ADR-012：调用 4 大师 strategy_review mode 反思失败假设
        # 让大师针对该节点的具体回测结果从各自视角反思
        backtest_result = r.get("backtest_result", {}) or {}
        master_perspectives = _invoke_personas(
            research_agent,
            PersonaContext(
                mode="strategy_review",
                target=strategy,
                backtest_result=backtest_result,
            ),
            logger,
            "[HTR-backpropagate]",
        )

        if logger and master_perspectives:
            logger.info(
                f"[HTR-backpropagate] 大师反思完成: "
                f"{len(master_perspectives)} 位提供视角"
            )

        insight_result = research_agent.backpropagate_insights(
            parent_hypothesis=parent.hypothesis,
            child_results=results,
            master_perspectives=master_perspectives if master_perspectives else None,
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


def _collect_historical_sharpes(
    tree: HypothesisTree,
) -> tuple[list[float], list[float]]:
    """收集 HTR 历史所有候选策略的 (dev_sharpe, oos_sharpe) 配对。

    ADR-015 S3: PBO 门需要历史候选 sharpe 列表计算 CSCV。
    只收集有 oos_score 的节点（FAILED 节点 oos_score 为 None 跳过）。

    Returns:
        (is_sharpes, oos_sharpes) 两个并列列表。无有效数据时返回 ([], [])。
    """
    is_sharpes: list[float] = []
    oos_sharpes: list[float] = []
    for node in tree.all_nodes():
        if node.id == "root":
            continue
        if node.oos_score is None:
            continue
        # dev_score 作为 IS sharpe（训练集回测）
        is_sharpes.append(float(node.dev_score))
        oos_sharpes.append(float(node.oos_score))
    return is_sharpes, oos_sharpes


def _evaluate_oos_and_merge(  # noqa: PLR0913
    tree: HypothesisTree,
    best_result: dict[str, Any],
    current_best_oos: float | None,
    backtest_service: BacktestService,
    oos_n_splits: int,
    oos_threshold: float,
    logger: LoggerService,
    stability_gate: WalkForwardStabilityGate | None = None,
    dsr_gate: DeflatedSharpeGate | None = None,
) -> str:
    """对最佳候选跑 OOS 验证并决定 merge/continue。

    ADR-015: 接入三道统计过拟合门（S1 稳定性 + S2 DSR），串联在 OOS 平均 sharpe
    通过后追加调用。S3 PBO 在 _decide_node 中单独调用（需历史候选 sharpe 列表）。

    Args:
        stability_gate: Walk-Forward 稳定性门。None 时跳过 S1（向后兼容）。
        dsr_gate: DSR 门。None 时跳过 S2（向后兼容）。
    """
    best_node_id = best_result.get("node_id", "")
    best_yaml = best_result.get("strategy_yaml", "")

    oos_score: float | None = None
    oos_result: dict[str, Any] = {}
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

    # OOS 平均 sharpe 未超过阈值 → 直接 continue
    if oos_score is None or not (
        current_best_oos is None or oos_score > current_best_oos + oos_threshold
    ):
        return "continue"

    # ADR-015 S1: Walk-Forward 稳定性门
    if not _check_stability_gate(stability_gate, oos_result, best_node_id, logger):
        return "continue"

    # ADR-015 S2: Deflated Sharpe Ratio 门
    if not _check_dsr_gate(
        dsr_gate, oos_score, tree, best_result, best_node_id, logger
    ):
        return "continue"

    # 全部门通过 → 合并
    tree.update_evidence(node_id=best_node_id, status=NodeStatus.MERGED)
    tree.current_best_id = best_node_id
    if logger:
        logger.info(
            f"[HTR-合并] 节点 {best_node_id} 合并 "
            f"(oos={oos_score:.2f} > best={current_best_oos})"
        )
    return "merge"


def _check_stability_gate(
    gate: WalkForwardStabilityGate | None,
    oos_result: dict[str, Any],
    node_id: str,
    logger: LoggerService,
) -> bool:
    """S1 Walk-Forward 稳定性门检查。返回 True 表示通过/跳过。"""
    if gate is None or not oos_result:
        return True
    fold_results = oos_result.get("fold_results", []) or []
    stability = gate.evaluate(fold_results)
    if not stability.passed:
        if logger:
            logger.warning(
                f"[HTR-OOS] 节点 {node_id} 被 S1 稳定性门拒绝: {stability.reason}"
            )
        return False
    if logger:
        logger.info(f"[HTR-OOS] 节点 {node_id} 通过 S1 稳定性门: {stability.reason}")
    return True


def _check_dsr_gate(  # noqa: PLR0913
    gate: DeflatedSharpeGate | None,
    oos_score: float | None,
    tree: HypothesisTree,
    best_result: dict[str, Any],
    node_id: str,
    logger: LoggerService,
) -> bool:
    """S2 Deflated Sharpe Ratio 门检查。返回 True 表示通过/跳过。"""
    if gate is None or oos_score is None:
        return True
    n_trials = max(tree.node_count - 1, 1)
    backtest_result = best_result.get("backtest_result", {}) or {}
    n_obs = int(backtest_result.get("trading_days", 252)) or 252
    dsr_result = gate.evaluate(
        observed_sharpe=oos_score,
        n_trials=n_trials,
        n_observations=n_obs,
    )
    if not dsr_result.passed:
        if logger:
            logger.warning(
                f"[HTR-OOS] 节点 {node_id} 被 S2 DSR 门拒绝: {dsr_result.reason}"
            )
        return False
    if logger:
        logger.info(f"[HTR-OOS] 节点 {node_id} 通过 S2 DSR 门: {dsr_result.reason}")
    return True


def _check_pbo_gate(
    gate: BacktestOverfitGate | None,
    tree: HypothesisTree,
    best_node_id: str,
    best: HypothesisNode | None,
    logger: LoggerService,
) -> bool:
    """S3 PBO 概率门检查。返回 True 表示通过/跳过。

    失败时回滚 merge 状态（把节点改回 VALIDATED）。
    """
    if gate is None:
        return True
    is_sharpes, oos_sharpes = _collect_historical_sharpes(tree)
    if len(is_sharpes) < _MIN_STRATEGIES_FOR_PBO_CHECK:
        return True  # 样本不足时跳过
    pbo_result = gate.evaluate(is_sharpes, oos_sharpes)
    if not pbo_result.passed:
        if logger:
            logger.warning(
                f"[HTR-OOS] 节点 {best_node_id} 被 S3 PBO 门拒绝: {pbo_result.reason}"
            )
        # 回滚 merge：把状态改回 VALIDATED
        tree.update_evidence(node_id=best_node_id, status=NodeStatus.VALIDATED)
        tree.current_best_id = best.parent_id if best else None
        return False
    if logger:
        logger.info(f"[HTR-OOS] 通过 S3 PBO 门: {pbo_result.reason}")
    return True


# PBO 检查所需的最少策略数（< 2 无法计算 CSCV）
_MIN_STRATEGIES_FOR_PBO_CHECK = 2


def _decide_evaluate_and_merge(  # noqa: PLR0913
    state: State,
    tree: HypothesisTree,
    best: HypothesisNode | None,
    current_best_oos: float | None,
    backtest_service: BacktestService,
    oos_n_splits: int,
    oos_threshold: float,
    logger: LoggerService,
    stability_gate: WalkForwardStabilityGate | None,
    dsr_gate: DeflatedSharpeGate | None,
    pbo_gate: BacktestOverfitGate | None,
) -> tuple[str, float | None]:
    """OOS 评估与合并决策，含 ADR-015 三道统计门。"""
    results = state.get("executor_results", []) or []
    if not results:
        return "continue", None

    best_result = max(results, key=lambda r: r.get("dev_score", 0))
    action = _evaluate_oos_and_merge(
        tree,
        best_result,
        current_best_oos,
        backtest_service,
        oos_n_splits,
        oos_threshold,
        logger,
        stability_gate=stability_gate,
        dsr_gate=dsr_gate,
    )
    oos_score = (
        tree.get_node(best_result.get("node_id", "")).oos_score
        if best_result.get("node_id")
        else None
    )

    # ADR-015 S3: PBO 概率门（仅在 S1+S2 通过且 action == "merge" 时触发）
    if action == "merge" and best_result.get("backtest_result"):
        best_node_id = best_result.get("node_id", "")
        if not _check_pbo_gate(pbo_gate, tree, best_node_id, best, logger):
            action = "continue"
    return action, oos_score


def _build_tree_state(  # noqa: PLR0913
    tree: HypothesisTree,
    current_best_oos: float | None,
    results: list[dict[str, Any]],
    oos_score: float | None,
    iteration: int,
    max_cycles: int,
) -> dict[str, Any]:
    """构造 LLM 决策上下文 tree_state。"""
    return {
        "node_count": tree.node_count,
        "max_depth": max((n.depth for n in tree.all_nodes()), default=0),
        "current_best_oos": current_best_oos,
        "best_dev_score": max((r.get("dev_score", 0) for r in results), default=0.0),
        "best_oos_score": oos_score,
        "cycles_used": iteration,
        "max_cycles": max_cycles,
    }


def _should_force_stop(
    iteration: int,
    max_cycles: int,
    max_depth: int,
    llm_action: str,
) -> bool:
    """判断是否应强制停止 HTR 循环。"""
    return iteration >= max_cycles or max_depth >= HTR_MAX_DEPTH or llm_action == "stop"


def _inject_connector_context(
    tree_state: dict[str, Any],
    connector: Connector | None,
    best: HypothesisNode | None,
    state: State,
    logger: LoggerService,
) -> None:
    """注入 Connector 图谱关联信息 + universe 财务面板到 tree_state。

    ADR-014 任务2/4：让 LLM 决策时看到相似失败案例与财务分布。
    """
    if connector is not None and best and best.hypothesis:
        try:
            fail_result = connector.get_concept(
                ConceptQuery(
                    subject=best.hypothesis,
                    aspect="动量族",
                    constraints={"k": 2},
                )
            )
            if isinstance(fail_result.data, list) and fail_result.data:
                tree_state["similar_experiences"] = "\n".join(
                    f"- {e.get('name', '')}: sharpe={e.get('sharpe', '?')}"
                    for e in fail_result.data
                )
        except Exception as e:
            if logger:
                logger.warning(f"[HTR-decide] 图谱失败案例查询失败: {e}")

    strategy_yaml = state.get("strategy_yaml", "") or ""
    universe = _parse_universe_from_yaml(strategy_yaml)
    financial_brief = _fetch_universe_financial_brief(connector, universe)
    if financial_brief != "无":
        existing = tree_state.get("similar_experiences", "无")
        if existing == "无":
            tree_state["similar_experiences"] = financial_brief
        else:
            tree_state["similar_experiences"] = f"{existing}\n{financial_brief}"


def _decide_node(  # noqa: PLR0913
    state: State,
    research_agent: StrategyResearchAgent,
    backtest_service: BacktestService,
    connector: Connector | None,
    logger: LoggerService,
    max_cycles: int = HTR_MAX_CYCLES,
    stability_gate: WalkForwardStabilityGate | None = None,
    dsr_gate: DeflatedSharpeGate | None = None,
    pbo_gate: BacktestOverfitGate | None = None,
) -> dict:
    """决策阶段 — 决定 merge/continue/stop。

    Phase 3: 对本轮最佳 dev 候选跑 Walk-Forward OOS，
    oos_score > current_best_oos + threshold → merge。

    ADR-014 任务2：注入 Connector 时，用图谱查相似失败案例注入 tree_state，
    LLM 决策时能看到"历史上类似假设的失败原因"。

    ADR-015: 接入三道统计过拟合门（S1 稳定性 + S2 DSR + S3 PBO）。
    S1/S2 在 _evaluate_oos_and_merge 内调用；S3 PBO 在此处调用（需历史
    候选 sharpe 列表）。

    Args:
        max_cycles: HTR 六步循环最大周期数（从 config.htr_max_cycles 注入），
            达到时强制停止。默认 HTR_MAX_CYCLES=10。
        stability_gate: Walk-Forward 稳定性门。None 时跳过 S1（向后兼容）。
        dsr_gate: DSR 门。None 时跳过 S2（向后兼容）。
        pbo_gate: PBO 门。None 时跳过 S3（向后兼容）。
    """
    tree_data = state.get("hypothesis_tree", {}) or {}
    tree = HypothesisTree.deserialize(tree_data)
    iteration = state.get("iteration", 0)
    oos_threshold = state.get("oos_threshold", HTR_MERGE_THRESHOLD)
    oos_n_splits = state.get("oos_n_splits", 3)

    best = tree.best_node()
    current_best_oos = best.oos_score if best else None

    action, oos_score = _decide_evaluate_and_merge(
        state,
        tree,
        best,
        current_best_oos,
        backtest_service,
        oos_n_splits,
        oos_threshold,
        logger,
        stability_gate,
        dsr_gate,
        pbo_gate,
    )

    tree_state = _build_tree_state(
        tree,
        current_best_oos,
        results=state.get("executor_results", []) or [],
        oos_score=oos_score,
        iteration=iteration,
        max_cycles=max_cycles,
    )
    # ADR-015 B1/B4: 注入 frontier_summary 供 LLM 决策 expand 时参考
    tree_state["frontier_summary"] = _build_frontier_summary(tree)
    _inject_connector_context(tree_state, connector, best, state, logger)

    llm_decision = research_agent.decide(tree_state)
    llm_action = str(llm_decision.get("action", "continue"))
    next_parent_id = str(llm_decision.get("next_parent_id", "") or "")
    prune_target_id = str(llm_decision.get("prune_target_id", "") or "")

    # ADR-015 B4: 处理 Arbor expand/prune 动作
    if llm_action == "expand" and next_parent_id:
        # expand: 在指定节点展开新分支（覆盖默认 merge/continue 决策）
        action = "continue"
        if logger:
            logger.info(f"[HTR-决策] Arbor expand → 在 {next_parent_id} 下展开新分支")
    elif llm_action == "prune" and prune_target_id:
        # prune: 剪枝指定子树
        _arbor_prune(tree, prune_target_id, logger)
        action = "continue"
    # 安全兜底：达到最大周期/深度 或 LLM 判定停止 → 强制停止
    if _should_force_stop(iteration, max_cycles, tree_state["max_depth"], llm_action):
        action = "stop"

    if logger:
        logger.info(f"[HTR-决策] action={action}, iteration={iteration}")

    next_iteration = iteration + 1
    return {
        "iteration": next_iteration,
        "result": action,
        "hypothesis_tree": tree.serialize(),
        # ADR-015 B4: 透传 Arbor expand/prune 信号给下一轮 ideate/select
        "next_parent_id": next_parent_id if llm_action == "expand" else "",
        "prune_target_id": "",
    }


def _build_frontier_summary(tree: HypothesisTree) -> str:
    """构造 frontier 摘要供 LLM 决策 expand 时参考。

    ADR-015 B4: 列出所有可探索的叶节点（排除 best_node 和 root，避免 LLM
    重复选择当前路径），最多 5 个，含节点 ID / dev_score / oos_score / hypothesis。
    """
    frontier = tree.frontier()
    if not frontier:
        return "无（无可回溯探索的叶节点）"
    best = tree.best_node()
    best_id = best.id if best else ""
    # 排除 best_node 和 root，只保留真正可回溯探索的节点
    candidates = [n for n in frontier if n.id not in (best_id, "root")]
    if not candidates:
        return "无（当前最佳节点是唯一可探索节点）"
    # 最多 5 个，按 dev_score 降序
    candidates.sort(key=lambda n: n.dev_score, reverse=True)
    lines: list[str] = []
    for n in candidates[:5]:
        oos_str = f"{n.oos_score:.2f}" if n.oos_score is not None else "无"
        lines.append(
            f"- {n.id} (dev={n.dev_score:.2f}, oos={oos_str}, "
            f"depth={n.depth}): {n.hypothesis[:50]}"
        )
    return "\n".join(lines)


def _arbor_prune(
    tree: HypothesisTree,
    target_id: str,
    logger: LoggerService | None,
) -> None:
    """Arbor prune 动作 — 级联剪枝指定子树。

    ADR-015 B4: 调用 ``tree.prune_subtree`` 标记子树为 PRUNED。
    防止后续 ideate 在已剪枝节点上展开新分支。
    """
    node = tree.get_node(target_id)
    if node is None:
        if logger:
            logger.warning(f"[HTR-决策] Arbor prune 失败: 节点 {target_id} 不存在")
        return
    tree.prune_subtree(target_id)
    if logger:
        logger.info(
            f"[HTR-决策] Arbor prune → 子树 {target_id} "
            f"(hypothesis={node.hypothesis[:40]}) 已剪枝"
        )


def _decide_cond(state: State) -> str:
    """决策路由。

    ADR-015 B4: expand/prune 动作路由回 observe（继续探索），
    与 continue 一致；merge/stop → save_tree → END。
    """
    action = state.get("result", "continue")
    if action in ("continue", "expand", "prune"):
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
        "observe",
        partial(
            _observe_node,
            research_agent=research_agent,
            connector=connector,
            logger=logger,
        ),
    )
    workflow.add_node(
        "ideate",
        partial(
            _ideate_node,
            research_agent=research_agent,
            memory=memory,
            connector=connector,
            logger=logger,
        ),
    )
    # ADR-010 Phase 5: max_select 可配置（HTR_MAX_SELECT），>1 时激活 Send fan-out
    htr_max_select = max(1, getattr(context.config, "htr_max_select", 1))
    workflow.add_node(
        "select",
        partial(
            _select_node,
            research_agent=research_agent,
            logger=logger,
            max_select=htr_max_select,
        ),
    )
    workflow.add_node("dispatch", partial(_dispatch_node, logger=logger))
    # ADR-009 收尾：训练集门（AcceptanceGate）— 优化版 sharpe 严格提升才接受
    acceptance_gate = AcceptanceGate()
    workflow.add_node(
        "executor",
        partial(
            _executor_node,
            research_agent=research_agent,
            develop_agent=develop_agent,
            backtest_service=backtest_service,
            logger=logger,
            gate=acceptance_gate,
            context=context,
        ),
    )
    # ADR-010 阶段 5 收尾（2026-08）：删除 executor_single 节点注册。
    # Send fan-out 伪并行已移除，多候选由 _executor_node 内部三阶段批量并行。
    workflow.add_node(
        "backpropagate",
        partial(_backpropagate_node, research_agent=research_agent, logger=logger),
    )
    # ADR-010: max_cycles 可配置（HTR_MAX_CYCLES），控制 HTR 循环最大周期数
    htr_max_cycles = max(1, getattr(context.config, "htr_max_cycles", HTR_MAX_CYCLES))
    # ADR-015: 三道统计过拟合门（默认全部启用）
    stability_gate = WalkForwardStabilityGate()
    dsr_gate = DeflatedSharpeGate()
    pbo_gate = BacktestOverfitGate()
    workflow.add_node(
        "decide",
        partial(
            _decide_node,
            research_agent=research_agent,
            backtest_service=backtest_service,
            connector=connector,
            logger=logger,
            max_cycles=htr_max_cycles,
            stability_gate=stability_gate,
            dsr_gate=dsr_gate,
            pbo_gate=pbo_gate,
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

    # ADR-010 阶段 5 收尾（2026-08）：dispatch 固定边到 executor。
    # _dispatch_cond 始终返回 "executor"；多候选由 executor 内部批量并行。
    workflow.add_edge("dispatch", "executor")
    workflow.add_edge("executor", "backpropagate")
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

    strategy_yaml = (
        state.get("strategy_yaml", "") or state.get("optimized_strategy_yaml", "") or ""
    )

    # 所有策略被 AcceptanceGate 拒绝时 strategy_yaml 为空，
    # 此时无 reference_strategy 可用，跳过缺口检测（避免 OperatorSpec 非空校验崩溃）
    if not strategy_yaml:
        return {"operator_gaps": []}

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
