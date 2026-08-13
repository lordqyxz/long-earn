"""通用响应模型（健康检查等）。"""

from __future__ import annotations

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """GET /api/health"""

    status: str
