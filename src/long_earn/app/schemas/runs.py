"""回测运行与图表导出相关模型（/api/runs/*、/api/compare）。"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class RunInfo(BaseModel):
    """回测运行汇总条目。"""

    run_id: str
    started: str
    total_return: float = 0.0
    sharpe: float = 0.0
    trade_count: int = 0
    event_count: int = 0
    strategy_id: str = ""
    tags: list[str] = Field(default_factory=list)


class RunsResponse(BaseModel):
    """GET /api/runs"""

    runs: list[RunInfo]


class CleanRunsResponse(BaseModel):
    """DELETE /api/runs/clean"""

    deleted_runs: int
    deleted_records: int


class DeleteRunResponse(BaseModel):
    """DELETE /api/runs/{run_id}"""

    deleted_run_id: str
    deleted_records: int


class RunSummaryItem(BaseModel):
    """RUN_END 审计统计条目。"""

    event_type: str
    status: str
    count: int


class RunSummaryResponse(BaseModel):
    """GET /api/runs/{run_id}/summary"""

    run_id: str
    summary: list[RunSummaryItem]


class EquityPoint(BaseModel):
    """权益曲线采样点。"""

    time: str
    value: float


class EquityResponse(BaseModel):
    """GET /api/runs/{run_id}/equity"""

    run_id: str
    equity_curve: list[EquityPoint]


class TradeRecord(BaseModel):
    """交易日志条目（FILL 事件）。"""

    time: str
    trace_id: str
    symbol: str
    type: str
    price: float = 0.0
    quantity: float = 0.0
    portfolio_value: float = 0.0
    reason: str = ""
    attribution: dict | None = None


class TradesResponse(BaseModel):
    """GET /api/runs/{run_id}/trades"""

    run_id: str
    trades: list[TradeRecord]


class SignalHistoryItem(BaseModel):
    """SIGNAL 事件历史条目。

    ``signals`` 为策略输出的信号字典（P1-13 后为结构化 JSON）；
    兼容历史数据中可能遗留的字符串序列化形式。
    """

    time: str
    signals: dict[str, float] | str = ""


class SignalsResponse(BaseModel):
    """GET /api/runs/{run_id}/signals"""

    run_id: str
    signals: list[SignalHistoryItem]


class RiskMetrics(BaseModel):
    """风险指标集合。"""

    total_return: float = 0.0
    annual_return: float = 0.0
    annual_volatility: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_duration_days: int = 0
    var_95: float = 0.0
    var_99: float = 0.0
    cvar_95: float = 0.0


class RiskResponse(BaseModel):
    """GET /api/runs/{run_id}/risk"""

    run_id: str
    risk_metrics: RiskMetrics


class DailyReturnPoint(BaseModel):
    """日收益率采样点。"""

    date: date
    portfolio_value: float = 0.0
    daily_return: float | None = None


class DailyReturnsResponse(BaseModel):
    """GET /api/runs/{run_id}/daily_returns"""

    run_id: str
    daily_returns: list[DailyReturnPoint]


class SymbolsResponse(BaseModel):
    """GET /api/runs/{run_id}/symbols"""

    run_id: str
    symbols: list[str]


class Benchmark(BaseModel):
    """基准指标（取自 MARKET_DATA 载荷）。"""

    alpha: float = 0.0
    beta: float = 0.0
    information_ratio: float = 0.0
    tracking_error: float = 0.0
    benchmark_return: float = 0.0


class DashboardData(BaseModel):
    """GET /api/runs/{run_id}/dashboard"""

    run_id: str
    total_events: int = 0
    event_breakdown: dict[str, int] = Field(default_factory=dict)
    time_range: dict[str, str] = Field(default_factory=dict)
    equity_curve: list[EquityPoint] = Field(default_factory=list)
    trade_journal: list[TradeRecord] = Field(default_factory=list)
    signal_history: list[SignalHistoryItem] = Field(default_factory=list)
    benchmark: Benchmark = Field(default_factory=Benchmark)
    risk_metrics: RiskMetrics = Field(default_factory=RiskMetrics)
    traded_symbols: list[str] = Field(default_factory=list)


class PricePoint(BaseModel):
    """日线行情采样点（开高低收量）。"""

    date: str
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: float = 0.0


class TradePoint(BaseModel):
    """成交标注点。"""

    time: str
    direction: str
    price: float = 0.0
    quantity: float = 0.0
    amount: float = 0.0
    reason: str = ""


class SymbolChartData(BaseModel):
    """GET /api/runs/{run_id}/symbol/{symbol}/chart"""

    symbol: str
    run_id: str
    price_history: list[PricePoint] = Field(default_factory=list)
    trade_points: list[TradePoint] = Field(default_factory=list)


class SymbolChartsResponse(BaseModel):
    """GET /api/runs/{run_id}/symbol_charts"""

    run_id: str
    symbols: int
    charts: list[SymbolChartData] = Field(default_factory=list)


class CompareRow(BaseModel):
    """多策略对比指标行。"""

    run_id: str
    total_return: float = 0.0
    annual_return: float = 0.0
    annual_volatility: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    max_drawdown_duration_days: int = 0
    var_95: float = 0.0
    var_99: float = 0.0
    cvar_95: float = 0.0
    trade_count: int = 0


class CompareRequest(BaseModel):
    """POST /api/compare"""

    run_ids: list[str]


class CompareResponse(BaseModel):
    """POST /api/compare"""

    comparison: list[CompareRow]
