"""策略研究子图 - Reflexion 模式 with 代码修复 and 自适应检索"""

from functools import partial
from typing import TYPE_CHECKING

from langgraph.graph import END, START, StateGraph

from .agents.strategy_develop_agent import StrategyDevelopAgent
from .agents.strategy_rd_supervisor import StrategyRdSupervisor
from .agents.strategy_research_agent import StrategyResearchAgent
from .state import State

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext
    from long_earn.operator_dev.backlog import OperatorBacklog

from long_earn.backtest.operators import list_operators
from long_earn.operator_dev.spec import OperatorSpec, OperatorSpecPriority
from long_earn.services import (
    BacktestService,
    LoggerService,
    MemoryService,
    StrategyExperience,
)

MAX_CODE_REFINES = 3
MAX_RETRIEVALS = 3


def _init_iteration(
    state: State,
    develop_agent: StrategyDevelopAgent,
    logger: LoggerService,
) -> dict:
    """初始化迭代计数器"""
    current_iteration = state.get("iteration", 0)
    develop_agent.clear_error_history()
    if logger:
        logger.info("=== 策略研发子图开始 ===")
    return {
        "iteration": current_iteration,
        "retrieval_count": 0,
        "max_retrievals": MAX_RETRIEVALS,
        "knowledge_context": "",
        "adaptive_retrieval_history": [],
    }


def _initial_retrieval_node(
    state: State,
    research_agent: StrategyResearchAgent,
    logger: LoggerService,
) -> dict:
    """初始检索节点 - 基础检索获取市场/策略基本信息"""
    query = state.get("query", "")
    if logger:
        logger.info(f"[初始检索] 查询: {query}")

    knowledge_context = research_agent._get_knowledge_context(
        query, node_type="research"
    )

    if logger:
        logger.info(f"[初始检索] 完成, 获取到 {len(knowledge_context)} 字符上下文")

    return {
        "knowledge_context": knowledge_context if knowledge_context else "",
        "retrieval_count": 1,
    }


def _evaluate_retrieval_node(
    state: State,
    research_agent: StrategyResearchAgent,
    logger: LoggerService,
) -> dict:
    """评估是否需要继续检索"""
    query = state.get("query", "")
    current_context = state.get("knowledge_context", "")
    retrieval_count = state.get("retrieval_count", 0)
    max_retrievals = state.get("max_retrievals", MAX_RETRIEVALS)

    if retrieval_count >= max_retrievals:
        if logger:
            logger.info(f"[检索评估] 已达最大检索次数 {max_retrievals}, 跳过")
        return {"retrieval_needed": False, "retrieval_keywords": []}

    if logger:
        logger.info(f"[检索评估] 第{retrieval_count}轮, 评估是否需要更多检索...")

    should_retrieve, keywords = research_agent._should_retrieve(query, current_context)

    if logger:
        logger.info(f"[检索评估] 结果: 需要检索={should_retrieve}, 关键词={keywords}")

    return {"retrieval_needed": should_retrieve, "retrieval_keywords": keywords}


def _adaptive_retrieval_node(
    state: State,
    research_agent: StrategyResearchAgent,
    logger: LoggerService,
) -> dict:
    """自适应检索节点 - 根据关键词执行检索"""
    keywords = state.get("retrieval_keywords", []) or []
    current_context = state.get("knowledge_context", "")
    retrieval_count = state.get("retrieval_count", 0)
    history = state.get("adaptive_retrieval_history", []) or []

    if logger:
        logger.info(f"[自适应检索] 第{retrieval_count + 1}轮, 关键词: {keywords}")

    new_results = []
    for keyword in keywords:
        retrieved = research_agent._get_knowledge_context(keyword)
        if retrieved:
            new_results.append({"keyword": keyword, "content": retrieved})
            current_context += f"\n\n### {keyword}相关知识:\n{retrieved}"

    retrieval_count += 1
    history.extend(new_results)

    if logger:
        logger.info(
            f"[自适应检索] 第{retrieval_count}轮完成, 新增{len(new_results)}条知识"
        )

    return {
        "knowledge_context": current_context,
        "retrieval_count": retrieval_count,
        "adaptive_retrieval_history": history,
        "retrieval_needed": False,
    }


def _research_node(
    state: State,
    research_agent: StrategyResearchAgent,
    logger: LoggerService,
) -> dict:
    """研究节点 - 生成初始策略"""
    query = state.get("query", "")
    knowledge_context = state.get("knowledge_context", "")

    if logger:
        logger.info(f"[策略研究] 开始研究策略: {query}")

    strategy = research_agent.research_strategy_with_context(query, knowledge_context)

    if logger:
        logger.info(
            f"[策略研究] 完成, 策略名称: {strategy.get('strategy_name', '未知')}"
        )

    return {
        "strategy": strategy,
        "strategy_name": strategy.get("strategy_name", "CustomStrategy"),
        "design_rationale": strategy.get("description", ""),
    }


def _develop_node(
    state: State,
    develop_agent: StrategyDevelopAgent,
    logger: LoggerService,
) -> dict:
    """开发节点 - 将策略转化为代码"""
    strategy = state.get("strategy", {}) or {}

    if logger:
        logger.info("[策略开发] 开始将策略转化为代码...")

    code = develop_agent.develop_strategy(strategy)

    if logger:
        logger.info(f"[策略开发] 完成, 代码长度: {len(code)} 字符")

    return {"strategy_yaml": code, "code_valid": False}


def _backtest_node(
    state: State,
    backtest_service: BacktestService,
    logger: LoggerService,
) -> dict:
    """回测节点 - 执行回测"""
    strategy_yaml = state.get("strategy_yaml", "") or state.get("strategy_code", "")

    if not strategy_yaml:
        return {
            "backtest_result": {
                "error": "策略代码为空",
                "error_category": "code_logic",
                "error_detail": "develop 节点未生成策略代码",
            },
            "code_valid": False,
        }

    if logger:
        logger.info("[回测] 开始执行回测...")

    backtest_result = backtest_service.run(strategy_yaml=strategy_yaml)

    if backtest_result.get("error"):
        if logger:
            logger.error(f"[回测] 回测错误: {backtest_result.get('error')}")
        # 引擎/数据源/数据不足错误不是策略代码逻辑问题，跳过修复循环
        error_category = backtest_result.get("error_category", "")
        non_refine_categories = {"engine_error", "insufficient_data"}
        if error_category in non_refine_categories:
            # 显式标记数值不可信：扁平 0 + 嵌套占位 metrics 同步置零，
            # 避免 reflection / save_experience 把"占位 0"当真实业绩。
            backtest_result.setdefault(
                "metrics",
                {
                    "return": 0,
                    "annual_return": 0,
                    "sharpe_ratio": 0,
                    "max_drawdown": 0,
                },
            )
            backtest_result.setdefault("metrics_unreliable", True)
            return {"backtest_result": backtest_result, "code_valid": True}
        return {"backtest_result": backtest_result, "code_valid": False}

    if logger:
        logger.info(
            f"[回测] 成功, 总收益率={backtest_result.get('total_return')}, "
            f"夏普比率={backtest_result.get('sharpe_ratio')}"
        )

    return {"backtest_result": backtest_result, "code_valid": True}


def _refine_node(
    state: State,
    develop_agent: StrategyDevelopAgent,
    logger: LoggerService,
    target: str = "initial",
) -> dict:
    """代码修复节点 - 根据错误修复代码

    target:
      - "initial"   修 strategy_yaml（首轮 develop 失败链路）
      - "optimized" 修 optimized_strategy_yaml（第 N 轮 develop_optimized 失败链路）
    第 2 轮 backtest_optimized 失败时，必须修复优化版本而非误改回初版；
    且修复后写回的字段决定下游 backtest_* 能否读到修好的代码。
    """
    strategy = state.get("strategy", {}) or {}
    if target == "optimized":
        strategy_code = (
            state.get("optimized_strategy_yaml", "")
            or state.get("optimized_strategy_code", "")
            or ""
        )
        opt_strat = state.get("optimized_strategy", {}) or {}
        if opt_strat:
            strategy = opt_strat
    else:
        strategy_code = (
            state.get("strategy_yaml", "") or state.get("strategy_code", "") or ""
        )
    backtest_result = state.get("backtest_result", {}) or {}
    error_message = backtest_result.get("error", "Unknown error")

    refine_count = len(develop_agent.get_error_history())
    if refine_count >= MAX_CODE_REFINES:
        if logger:
            logger.warning(f"[代码修复] 已达最大修复次数 ({MAX_CODE_REFINES})")
        return {"code_valid": False}

    if logger:
        logger.info(f"[代码修复-{target}] 第{refine_count + 1}次修复...")

    refined_code = develop_agent.refine_code(
        strategy=strategy,
        error_message=error_message,
        failed_code=strategy_code,
    )

    if logger:
        logger.info(f"[代码修复-{target}] 修复完成")

    if target == "optimized":
        return {"optimized_strategy_yaml": refined_code, "code_valid": False}
    return {"strategy_yaml": refined_code, "code_valid": False}


def _reflection_node(
    state: State,
    research_agent: StrategyResearchAgent,
    logger: LoggerService,
) -> dict:
    """反思节点 - 分析回测结果并生成改进建议"""
    strategy = state.get("strategy", {}) or {}
    backtest_result = state.get("backtest_result", {}) or {}

    if logger:
        logger.info("[反思] 开始 ToT 多分支反思...")

    reflection_result = research_agent.reflect(strategy, backtest_result)

    if logger:
        logger.info(
            f"[反思] 完成, 选定方向: "
            f"{reflection_result.get('selected_direction', '未知')}"
        )

    return {
        "reflection": reflection_result.get("reflection", ""),
        "improvement_suggestions": reflection_result.get("improvement_suggestions", []),
        "explored_paths": reflection_result.get("explored_paths"),
        "selected_direction": reflection_result.get("selected_direction"),
        "tot_enabled": reflection_result.get("tot_enabled", False),
        "primary_issue": reflection_result.get("primary_issue"),
    }


# ── 算子缺口检测（ADR-009 gap_detector）─────────────────────────

# 改进建议关键词 → 算子能力映射（用于检测目录是否已覆盖）
_GAP_KEYWORD_MAP: dict[str, tuple[str, str, str]] = {
    # keyword: (operator_name, category, intent)
    "动量": ("momentum", "factor", "计算价格动量（区间收益率）"),
    "rsi": ("rsi", "technical", "RSI 超买超卖相对强弱指标"),
    "macd": ("macd", "technical", "MACD 指标移动平均收敛发散"),
    "布林": ("bollinger", "technical", "布林带上下轨计算"),
    "止盈": ("take_profit", "filter", "动态止盈条件过滤"),
    "止损": ("stop_loss", "filter", "动态止损条件过滤"),
    "成交量": ("volume_weighted", "factor", "成交量加权因子"),
    "波动率": ("realized_volatility", "factor", "已实现波动率计算"),
    "换手率": ("turnover", "factor", "换手率因子"),
}


def _gap_detector_node(
    state: State,
    logger: LoggerService,
) -> dict:
    """算子缺口检测节点 — 扫描改进建议与算子目录差异，产出 OperatorSpec 写 backlog。

    非阻塞：检测到缺口写入 backlog 后立即返回，策略研发不等待算子开发。
    """
    backlog = _operator_backlog
    if backlog is None:
        return {"operator_gaps": []}

    suggestions: list[str] = state.get("improvement_suggestions", []) or []
    if not suggestions:
        return {"operator_gaps": []}

    strategy_yaml = state.get("strategy_yaml", "") or state.get(
        "optimized_strategy_yaml", ""
    ) or ""
    if not strategy_yaml:
        return {"operator_gaps": []}

    # 获取当前算子目录已有算子名
    try:
        existing_ops = {op["name"] for op in list_operators()}
    except Exception:
        existing_ops = set()

    # 扫描改进建议，匹配关键词检测缺口
    gaps: list[dict[str, str]] = []
    for suggestion in suggestions:
        suggestion_lower = str(suggestion).lower()
        for keyword, (op_name, category, intent) in _GAP_KEYWORD_MAP.items():
            if keyword.lower() not in suggestion_lower:
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
                motivation=f"改进建议「{suggestion[:100]}」需要 {keyword} 能力，目录暂缺",
                priority=OperatorSpecPriority.NORMAL,
            )
            submitted = backlog.submit(spec)
            if submitted:
                gaps.append(
                    {"name": op_name, "intent": intent, "keyword": keyword}
                )
                if logger:
                    logger.info(
                        f"[缺口检测] 发现算子缺口: {op_name} ({category}) — {intent}"
                    )

    return {"operator_gaps": gaps}


_operator_backlog: "OperatorBacklog | None" = None  # type: ignore[name-defined]


def _optimize_node(
    state: State,
    research_agent: StrategyResearchAgent,
    logger: LoggerService,
) -> dict:
    """优化节点 - 根据改进建议优化策略

    多轮演进的关键：优先以 **上一轮 optimized_strategy** 作为优化起点，
    而非永远从初始 strategy 开始 —— 否则 N 轮迭代实际只是 N 次独立 v0 优化。
    """
    base_strategy = state.get("optimized_strategy") or state.get("strategy", {}) or {}
    backtest_result = state.get("backtest_result", {}) or {}
    improvement_suggestions: list[str] = state.get("improvement_suggestions", []) or []

    if isinstance(improvement_suggestions, str):
        improvement_suggestions = [improvement_suggestions]
    # 确保 improvement_suggestions 是 str 列表（LLM 可能返回 dict）
    improvement_suggestions = [str(s) for s in improvement_suggestions]

    if logger:
        base_label = "上一轮优化版" if state.get("optimized_strategy") else "初始版"
        logger.info(
            f"[优化] 起点={base_label}, 改进建议数: {len(improvement_suggestions)}"
        )

    optimized_strategy = research_agent.optimize_strategy(
        base_strategy,
        improvement_suggestions,
        previous_backtest=backtest_result,
    )

    if logger:
        logger.info("[优化] 优化完成")

    return {"optimized_strategy": optimized_strategy}


def _develop_optimized_node(
    state: State,
    develop_agent: StrategyDevelopAgent,
    logger: LoggerService,
) -> dict:
    """开发优化后的策略代码

    第 N 轮（N≥2）的入口：在调用 LLM 前必须清空 develop_agent 的错误历史，
    否则第 1 轮累积的失败次数会让 _refine_cond 立即认定"已用尽修复预算"，
    第 2 轮的优化版策略一旦回测失败就**永远进不了 refine** —— 多轮演进静默崩盘。
    """
    optimized_strategy = state.get("optimized_strategy", {}) or {}
    develop_agent.clear_error_history()

    if logger:
        logger.info("[策略开发-优化版] 开始开发优化后的策略代码（已重置错误历史）...")

    code = develop_agent.develop_strategy(optimized_strategy)

    if logger:
        logger.info(f"[策略开发-优化版] 完成, 代码长度: {len(code)} 字符")

    return {"optimized_strategy_yaml": code, "code_valid": False}


def _backtest_optimized_node(
    state: State,
    backtest_service: BacktestService,
    logger: LoggerService,
) -> dict:
    """回测优化后的策略"""
    optimized_strategy_yaml = state.get("optimized_strategy_yaml", "") or state.get(
        "optimized_strategy_code", ""
    )

    if not optimized_strategy_yaml:
        return {
            "backtest_result": {
                "error": "优化后的策略代码为空",
                "error_category": "code_logic",
                "error_detail": "develop_optimized 节点未生成策略代码",
            },
            "code_valid": False,
        }

    if logger:
        logger.info("[回测-优化版] 开始回测优化后的策略...")

    backtest_result = backtest_service.run(strategy_yaml=optimized_strategy_yaml)

    if backtest_result.get("error"):
        if logger:
            logger.error(f"[回测-优化版] 错误: {backtest_result.get('error')}")
        # 与 _backtest_node 对齐：engine_error / insufficient_data 不是策略代码逻辑问题，跳过修复循环
        error_category = backtest_result.get("error_category", "")
        non_refine_categories = {"engine_error", "insufficient_data"}
        if error_category in non_refine_categories:
            backtest_result.setdefault(
                "metrics",
                {
                    "return": 0,
                    "annual_return": 0,
                    "sharpe_ratio": 0,
                    "max_drawdown": 0,
                },
            )
            backtest_result.setdefault("metrics_unreliable", True)
            return {"backtest_result": backtest_result, "code_valid": True}
        return {"backtest_result": backtest_result, "code_valid": False}

    if logger:
        logger.info(
            f"[回测-优化版] 成功, 总收益率={backtest_result.get('total_return')}"
        )

    return {"backtest_result": backtest_result, "code_valid": True}


def _save_experience_node(
    state: State,
    memory: MemoryService,
    develop_agent: StrategyDevelopAgent,
    logger: LoggerService,
) -> dict:
    """保存经验节点 - 将成功的策略保存到知识库"""
    strategy_name = state.get("strategy_name", "CustomStrategy") or "CustomStrategy"
    design_rationale = state.get("design_rationale", "") or ""
    strategy_code = (
        state.get("strategy_yaml", "") or state.get("strategy_code", "") or ""
    )
    backtest_result = state.get("backtest_result", {}) or {}
    reflection = state.get("reflection", "") or ""
    error_history = develop_agent.get_error_history()

    if logger:
        logger.info(f"[保存经验] 保存策略经验: {strategy_name}")

    experience = StrategyExperience(
        name=strategy_name,
        code=strategy_code,
        rationale=design_rationale,
        metrics=backtest_result,
        reflection=reflection,
        error_history=error_history if error_history else None,
    )
    exp_id = memory.save_experience(experience)
    saved = bool(exp_id)

    if logger:
        logger.info(f"[保存经验] {'成功' if saved else '失败'}")

    return {"experience_saved": saved}


def _supervisor_node(
    state: State,
    supervisor: StrategyRdSupervisor,
    logger: LoggerService,
) -> dict:
    """监督器节点 - 决定是否继续迭代"""
    current_iteration = state.get("iteration", 0)
    max_iterations = state.get("max_iterations", 3)

    strategy = state.get("strategy", {}) or {}
    backtest_result = state.get("backtest_result", {}) or {}
    reflection = state.get("reflection", "") or ""
    improvement_suggestions = state.get("improvement_suggestions", []) or []
    # 确保 improvement_suggestions 是 str 列表（LLM 可能返回 dict）
    str_suggestions = [str(s) for s in improvement_suggestions]

    if logger:
        logger.info(
            f"[监督器] 评估第{current_iteration}次迭代 (最大{max_iterations}次)..."
        )

    should_continue = supervisor.should_continue(
        iteration=current_iteration,
        max_iterations=max_iterations,
        strategy=strategy,
        backtest_result=backtest_result,
        reflection=reflection,
        improvement_suggestions="\n".join(str_suggestions),
    )

    next_iteration = current_iteration + 1

    if logger:
        logger.info(
            f"[监督器] 决策: {'继续迭代' if should_continue else '停止迭代'}, "
            f"下一轮迭代编号: {next_iteration}"
        )

    return {"should_continue": should_continue, "iteration": next_iteration}


def _evaluate_retrieval_cond(state: State) -> str:
    return "adaptive_retrieval" if state.get("retrieval_needed", False) else "research"


def _backtest_cond(state: State) -> str:
    return "reflection" if state.get("code_valid", False) else "refine"


def _refine_cond(_state: State, develop_agent: StrategyDevelopAgent) -> str:
    return (
        "backtest"
        if len(develop_agent.get_error_history()) < MAX_CODE_REFINES
        else "reflection"
    )


def _refine_optimized_cond(_state: State, develop_agent: StrategyDevelopAgent) -> str:
    """优化版修复后的条件路由：还在预算内 → 重新跑 backtest_optimized；用尽 → reflection。"""
    return (
        "backtest_optimized"
        if len(develop_agent.get_error_history()) < MAX_CODE_REFINES
        else "reflection"
    )


def _supervisor_cond(state: State) -> str:
    return "optimize" if state.get("should_continue", False) else "end"


def _backtest_optimized_cond(state: State) -> str:
    return "reflection" if state.get("code_valid", False) else "refine_optimized"


def create_strategy_rd_subgraph(context: "RuntimeContext"):
    """创建策略研究子图 - Reflexion 模式 with 代码修复 and 自适应检索

    Args:
        context: 运行时上下文
    """
    research_agent = StrategyResearchAgent(context=context)
    supervisor = StrategyRdSupervisor(context=context)
    develop_agent = StrategyDevelopAgent(context=context)

    logger = context.logger
    backtest_service = context.require_backtest()
    memory = context.require_memory()

    # 设置 gap_detector 的 backlog 引用（ADR-009）
    global _operator_backlog  # noqa: PLW0603
    _operator_backlog = context.operator_backlog

    workflow = StateGraph(State)

    workflow.add_node(
        "init", partial(_init_iteration, develop_agent=develop_agent, logger=logger)
    )
    workflow.add_node(
        "initial_retrieval",
        partial(_initial_retrieval_node, research_agent=research_agent, logger=logger),
    )
    workflow.add_node(
        "evaluate_retrieval",
        partial(_evaluate_retrieval_node, research_agent=research_agent, logger=logger),
    )
    workflow.add_node(
        "adaptive_retrieval",
        partial(_adaptive_retrieval_node, research_agent=research_agent, logger=logger),
    )
    workflow.add_node(
        "research",
        partial(_research_node, research_agent=research_agent, logger=logger),
    )
    workflow.add_node(
        "develop", partial(_develop_node, develop_agent=develop_agent, logger=logger)
    )
    workflow.add_node(
        "backtest",
        partial(_backtest_node, backtest_service=backtest_service, logger=logger),
    )
    workflow.add_node(
        "refine",
        partial(
            _refine_node,
            develop_agent=develop_agent,
            logger=logger,
            target="initial",
        ),
    )
    workflow.add_node(
        "refine_optimized",
        partial(
            _refine_node,
            develop_agent=develop_agent,
            logger=logger,
            target="optimized",
        ),
    )
    workflow.add_node(
        "reflection",
        partial(_reflection_node, research_agent=research_agent, logger=logger),
    )
    workflow.add_node(
        "gap_detector", partial(_gap_detector_node, logger=logger)
    )
    workflow.add_node(
        "optimize",
        partial(_optimize_node, research_agent=research_agent, logger=logger),
    )
    workflow.add_node(
        "develop_optimized",
        partial(_develop_optimized_node, develop_agent=develop_agent, logger=logger),
    )
    workflow.add_node(
        "backtest_optimized",
        partial(
            _backtest_optimized_node, backtest_service=backtest_service, logger=logger
        ),
    )
    workflow.add_node(
        "save_experience",
        partial(
            _save_experience_node,
            memory=memory,
            develop_agent=develop_agent,
            logger=logger,
        ),
    )
    workflow.add_node(
        "supervisor", partial(_supervisor_node, supervisor=supervisor, logger=logger)
    )

    workflow.add_edge(START, "init")
    workflow.add_edge("init", "initial_retrieval")
    workflow.add_edge("initial_retrieval", "evaluate_retrieval")

    workflow.add_conditional_edges(
        "evaluate_retrieval",
        _evaluate_retrieval_cond,
        {"adaptive_retrieval": "adaptive_retrieval", "research": "research"},
    )

    workflow.add_edge("adaptive_retrieval", "evaluate_retrieval")
    workflow.add_edge("research", "develop")
    workflow.add_edge("develop", "backtest")

    workflow.add_edge("refine", "backtest")

    workflow.add_conditional_edges(
        "backtest",
        _backtest_cond,
        {"refine": "refine", "reflection": "reflection"},
    )

    workflow.add_conditional_edges(
        "refine",
        partial(_refine_cond, develop_agent=develop_agent),
        {"backtest": "backtest", "reflection": "reflection"},
    )

    workflow.add_edge("reflection", "gap_detector")
    workflow.add_edge("gap_detector", "save_experience")
    workflow.add_edge("save_experience", "supervisor")

    workflow.add_conditional_edges(
        "supervisor",
        _supervisor_cond,
        {"optimize": "optimize", "end": END},
    )

    workflow.add_edge("optimize", "develop_optimized")
    workflow.add_edge("develop_optimized", "backtest_optimized")

    workflow.add_conditional_edges(
        "backtest_optimized",
        _backtest_optimized_cond,
        {"refine_optimized": "refine_optimized", "reflection": "reflection"},
    )

    workflow.add_conditional_edges(
        "refine_optimized",
        partial(_refine_optimized_cond, develop_agent=develop_agent),
        {"backtest_optimized": "backtest_optimized", "reflection": "reflection"},
    )

    return workflow.compile()
