from typing import Any, Dict, Optional, TypedDict


class State(TypedDict, total=False):
    """策略研究子图的状态 - Reflexion 模式"""

    query: str
    strategy: Optional[Dict[str, Any]]
    strategy_code: Optional[str]
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


StrategyResearchState = State
