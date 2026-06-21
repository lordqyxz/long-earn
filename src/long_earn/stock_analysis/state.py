from typing import Any, TypedDict


class StockAnalysisState(TypedDict, total=False):
    """股票分析子图的状态"""

    query: str
    stock_code: str
    stock_name: str
    stock_data: dict[str, Any] | None
    petter_analysis: str | None
    charles_munger_analysis: str | None
    buffett_analysis: str | None
    fiske_analysis: str | None
    fund_flow_analysis: str | None
    summary: str | None
    result: str | None
    error: str | None
    retry_count: int
