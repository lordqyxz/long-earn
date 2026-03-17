from typing import Any, Dict, List, Optional, TypedDict


class State(TypedDict, total=False):
    """策略研究子图的状态 - Reflexion 模式"""

    query: str
    strategy: Optional[Dict[str, Any]]
    strategy_code: Optional[str]
    strategy_name: Optional[str]
    design_rationale: Optional[str]
    backtest_result: Optional[Dict[str, Any]]
    reflection: Optional[str]
    improvement_suggestions: Optional[str]
    optimized_strategy: Optional[Dict[str, Any]]
    optimized_strategy_code: Optional[str]
    iteration: int
    max_iterations: int
    accept_optimization: bool
    should_continue: bool
    result: Optional[str]
    explored_paths: Optional[List[Dict[str, Any]]]
    selected_direction: Optional[str]
    tot_enabled: bool
    primary_issue: Optional[str]
    error_history: Optional[List[Dict[str, Any]]]
    code_valid: bool
    experience_saved: bool

    retrieval_count: int
    max_retrievals: int
    knowledge_context: str
    retrieval_needed: bool
    retrieval_keywords: Optional[List[str]]
    adaptive_retrieval_history: Optional[List[Dict[str, Any]]]


StrategyResearchState = State
