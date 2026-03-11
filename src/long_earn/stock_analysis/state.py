from typing import Dict, Any, Optional, TypedDict


class State(TypedDict, total=False):
    """股票分析子图的状态"""
    query: str
    stock_code: str
    stock_data: Optional[Dict[str, Any]]
    petter_analysis: Optional[str]
    charles_munger_analysis: Optional[str]
    buffett_analysis: Optional[str]
    fiske_analysis: Optional[str]
    summary: Optional[str]
    result: Optional[str]
    error: Optional[str]
    retry_count: int