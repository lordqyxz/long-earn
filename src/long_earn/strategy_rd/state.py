from typing import Any, TypedDict


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
    code_valid: bool
    experience_saved: bool

    retrieval_count: int
    max_retrievals: int
    knowledge_context: str
    retrieval_needed: bool
    retrieval_keywords: list[str] | None
    adaptive_retrieval_history: list[dict[str, Any]] | None


StrategyResearchState = State
