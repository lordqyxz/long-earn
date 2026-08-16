"""FastAPI 可视化服务请求 / 响应模型（按领域分模块）。

为 ``app.py`` 的全部 REST 端点声明 ``response_model`` 与请求体模型，
使 FastAPI 自动生成的 OpenAPI schema（``/openapi.json``）携带完整类型，
前端据此用 openapi-ts 生成类型安全的 TypeScript API 客户端。

字段以 analyzer / event_analyzer 实际返回结构为准；对真实数据中可能缺失
或为 null 的字段使用可选类型与默认值，保持宽松校验、不改变既有响应行为。
"""

from long_earn.app.schemas.audit import (
    AttributionChain,
    AttributionChainEvents,
    AuditChainEvent,
    AuditEventItem,
    AuditEventsResponse,
    OrderInfo,
    Rationale,
    RationaleCriterion,
    RationaleSegment,
    RationaleWeights,
    SignalAttribution,
    TradeAttribution,
)
from long_earn.app.schemas.common import HealthResponse
from long_earn.app.schemas.events import (
    EventDetail,
    EventItem,
    EventsResponse,
    EventStats,
    RelationItem,
    RelationsResponse,
    TimelinePoint,
    TimelineResponse,
    TopSymbol,
    TriggerRequest,
    TriggerResponse,
)
from long_earn.app.schemas.research import (
    ResearchStartRequest,
    ResearchStartResponse,
)
from long_earn.app.schemas.runs import (
    Benchmark,
    CleanRunsResponse,
    CompareRequest,
    CompareResponse,
    CompareRow,
    DailyReturnPoint,
    DailyReturnsResponse,
    DashboardData,
    DeleteRunResponse,
    EquityPoint,
    EquityResponse,
    PricePoint,
    RiskMetrics,
    RiskResponse,
    RunInfo,
    RunsResponse,
    RunSummaryItem,
    RunSummaryResponse,
    SignalHistoryItem,
    SignalsResponse,
    SymbolChartData,
    SymbolChartsResponse,
    SymbolsResponse,
    TradePoint,
    TradeRecord,
    TradesResponse,
)
from long_earn.app.schemas.symbols import (
    FinancialsResponse,
    SectorStatsResponse,
    SymbolDetailResponse,
    SymbolNamesResponse,
)

__all__ = [
    # audit
    "AttributionChain",
    "AttributionChainEvents",
    "AuditChainEvent",
    "AuditEventItem",
    "AuditEventsResponse",
    # runs
    "Benchmark",
    "CleanRunsResponse",
    "CompareRequest",
    "CompareResponse",
    "CompareRow",
    "DailyReturnPoint",
    "DailyReturnsResponse",
    "DashboardData",
    "DeleteRunResponse",
    "EquityPoint",
    "EquityResponse",
    # events
    "EventDetail",
    "EventItem",
    "EventStats",
    "EventsResponse",
    # symbols
    "FinancialsResponse",
    # common
    "HealthResponse",
    "OrderInfo",
    "PricePoint",
    "Rationale",
    "RationaleCriterion",
    "RationaleSegment",
    "RationaleWeights",
    "RelationItem",
    "RelationsResponse",
    # research
    "ResearchStartRequest",
    "ResearchStartResponse",
    "RiskMetrics",
    "RiskResponse",
    "RunInfo",
    "RunSummaryItem",
    "RunSummaryResponse",
    "RunsResponse",
    "SectorStatsResponse",
    "SignalAttribution",
    "SignalHistoryItem",
    "SignalsResponse",
    "SymbolChartData",
    "SymbolChartsResponse",
    "SymbolDetailResponse",
    "SymbolNamesResponse",
    "SymbolsResponse",
    "TimelinePoint",
    "TimelineResponse",
    "TopSymbol",
    "TradeAttribution",
    "TradePoint",
    "TradeRecord",
    "TradesResponse",
    "TriggerRequest",
    "TriggerResponse",
]
