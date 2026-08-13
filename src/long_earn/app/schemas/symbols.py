"""标的查询相关模型（/api/symbols/*）。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

#: 标的详情字段随数据源（xtquant / DuckDB）变化，保持宽松 dict。
SymbolDetailResponse = dict[str, Any]


class SymbolNamesResponse(BaseModel):
    """GET /api/symbols/names"""

    names: dict[str, str] = Field(default_factory=dict)


class SectorStatsResponse(BaseModel):
    """POST /api/symbols/refresh-sectors 与板块统计。"""

    total: int = 0
    with_industry: int = 0
    with_region: int = 0


class FinancialsResponse(BaseModel):
    """GET /api/symbols/{symbol}/financials"""

    symbol: str
    financials: list[dict[str, Any]] = Field(default_factory=list)
