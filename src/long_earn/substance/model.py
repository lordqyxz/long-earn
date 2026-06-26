"""Substance 数据模型 — 物质-运动统一架构的核心存在基类。

Substance 统一 event / relation / knowledge / strategy / backtest 五种形态，
每种物质可持久化、可检索、有来源（provenance）。采用 Pydantic BaseModel，
与项目 BacktestResult / StrategyDSL 技术栈一致。
"""

from __future__ import annotations

import math
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class SubstanceForm(StrEnum):
    """物质形态 — 对应粒子的不同存在方式。"""

    EVENT = "event"
    RELATION = "relation"
    KNOWLEDGE = "knowledge"
    STRATEGY = "strategy"
    BACKTEST = "backtest"


class FilterLogic(StrEnum):
    """WorldInfo 过滤键逻辑 — 决定 filter_keys 的匹配方式。"""

    AND_ANY = "and_any"
    AND_ALL = "and_all"
    NOT_ANY = "not_any"
    NOT_ALL = "not_all"


class Substance(BaseModel):
    """物质 — 统一存在基类，客观实在的可持久化表示。

    每条 Substance 都有唯一 sid、形态、内容、来源、时间戳。
    relation 形态额外有 source_id / target_id / relation_type。
    """

    sid: str = Field(default_factory=lambda: f"sub_{uuid.uuid4().hex[:12]}")
    form: SubstanceForm
    content: str = ""
    keys: list[str] = Field(default_factory=list)
    filter_keys: list[str] = Field(default_factory=list)
    filter_logic: FilterLogic = FilterLogic.AND_ANY
    created_at: datetime = Field(default_factory=datetime.now)
    visible_from: datetime | None = None
    expires_at: datetime | None = None
    source: str = "manual"
    confidence: float = 1.0
    source_id: str | None = None
    target_id: str | None = None
    relation_type: str | None = None
    conflict_group: str | None = None
    insertion_order: int = 0
    decay_half_life_days: float = 90.0
    metadata: dict[str, Any] = Field(default_factory=dict)

    def is_visible_at(self, when: datetime) -> bool:
        """判断物质在指定时刻是否可见（防未来函数 + 过期过滤）。

        Args:
            when: 查询时刻

        Returns:
            visible_from ≤ when 且未过期则 True
        """
        if self.visible_from is not None and when < self.visible_from:
            return False
        return not (self.expires_at is not None and when >= self.expires_at)

    def decay_factor(self, when: datetime | None = None) -> float:
        """计算时间衰减因子（指数衰减）。

        Args:
            when: 参考时刻，默认 now

        Returns:
            衰减因子 [0, 1]，半衰期后降至 0.5
        """
        if when is None:
            when = datetime.now()
        age_days = (when - self.created_at).total_seconds() / 86400.0
        if age_days <= 0:
            return 1.0
        # 使用 ln(2) 系数使半衰期后正好降至 0.5
        return math.exp(-0.6931471805599453 * age_days / self.decay_half_life_days)
