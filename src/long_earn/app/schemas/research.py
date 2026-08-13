"""策略研发相关模型（/api/research/*）。"""

from __future__ import annotations

from pydantic import BaseModel


class ResearchStartRequest(BaseModel):
    """POST /api/research/start"""

    idea: str
    max_rounds: int = 3
    max_iterations: int = 2
    min_improvement: float = 0.005


class ResearchStartResponse(BaseModel):
    """POST /api/research/start"""

    status: str
    idea: str
    max_rounds: int
    max_iterations: int
