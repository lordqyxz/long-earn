from typing import Annotated, Any, TypedDict


def _last_wins(_left: Any, right: Any) -> Any:
    """Reducer: 取最后一个值（右值优先）"""
    return right


def _collect_executor_results(_left: list[Any], right: Any) -> list[Any]:
    """Reducer: 累加并行 executor 结果（ADR-010 Phase 5 Send fan-out join）。"""
    if isinstance(right, list):
        return _left + right
    return [*_left, right]


class State(TypedDict, total=False):
    """策略研究子图的状态 - Reflexion 模式"""

    query: str
    strategy: dict[str, Any] | None
    strategy_yaml: str | None
    # Deprecated: 保留 strategy_code 以兼容旧代码
    strategy_code: str | None
    strategy_name: str | None
    design_rationale: str | None
    backtest_result: dict[str, Any] | None
    reflection: str | None
    improvement_suggestions: list[str] | None
    optimized_strategy: dict[str, Any] | None
    optimized_strategy_yaml: str | None
    # Deprecated: 保留以兼容旧代码
    optimized_strategy_code: str | None
    iteration: int
    max_iterations: int
    accept_optimization: bool
    should_continue: bool
    result: str | None
    explored_paths: list[dict[str, Any]] | None
    selected_direction: str | None
    tot_enabled: bool
    primary_issue: str | None
    error_history: list[dict[str, Any]] | None
    # code_valid 可能被并发节点同时更新，使用 last-wins reducer
    code_valid: Annotated[bool, _last_wins]
    experience_saved: bool
    operator_gaps: list[dict[str, str]] | None

    retrieval_count: int
    max_retrievals: int
    knowledge_context: str
    retrieval_needed: bool
    retrieval_keywords: list[str] | None
    adaptive_retrieval_history: list[dict[str, Any]] | None

    # HTR 假设树状态（ADR-010）
    hypothesis_tree: dict[str, Any] | None
    current_best_node_id: str | None
    selected_leaves: list[str] | None
    executor_results: Annotated[list[dict[str, Any]], _collect_executor_results] | None
    run_id: str | None
    oos_threshold: float
    oos_n_splits: int
    operator_gaps: list[dict[str, str]] | None


StrategyResearchState = State
