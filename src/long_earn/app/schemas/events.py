"""事件流与推理管线相关模型（/api/events/*）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class EventItem(BaseModel):
    """事件物质条目。"""

    sid: str
    content: str
    sentiment: str = "neutral"
    symbols: list[str] = Field(default_factory=list)
    category: str = ""
    confidence: float = 0.0
    created_at: str = ""
    conflict_group: str | None = None
    keys: list[str] = Field(default_factory=list)
    source: str = ""


class EventsResponse(BaseModel):
    """GET /api/events"""

    count: int
    events: list[EventItem] = Field(default_factory=list)


class TopSymbol(BaseModel):
    """热门标的统计。"""

    symbol: str
    count: int


class EventStats(BaseModel):
    """GET /api/events/stats"""

    total_events: int = 0
    total_relations: int = 0
    by_sentiment: dict[str, int] = Field(default_factory=dict)
    by_category: dict[str, int] = Field(default_factory=dict)
    top_symbols: list[TopSymbol] = Field(default_factory=list)


class TimelinePoint(BaseModel):
    """事件时间线按天聚合条目。"""

    date: str
    count: int = 0
    positive: int = 0
    negative: int = 0
    neutral: int = 0


class TimelineResponse(BaseModel):
    """GET /api/events/timeline"""

    timeline: list[TimelinePoint] = Field(default_factory=list)


class RelationItem(BaseModel):
    """影响关系物质条目。"""

    sid: str
    content: str
    source_id: str | None = None
    target: str = ""
    relation_type: str = "impacts"
    direction: str = "neutral"
    confidence: float = 0.0
    created_at: str = ""


class RelationsResponse(BaseModel):
    """GET /api/events/relations"""

    count: int
    relations: list[RelationItem] = Field(default_factory=list)


class EventDetail(EventItem):
    """GET /api/events/{sid} — 事件详情 + 关联关系。"""

    relations: list[RelationItem] = Field(default_factory=list)


class TriggerRequest(BaseModel):
    """POST /api/events/trigger"""

    query: str


class TriggerResponse(BaseModel):
    """POST /api/events/trigger"""

    task_id: str
    status: str
