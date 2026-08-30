"""Substance 数据模型 — 物质-运动统一架构的核心存在基类。

Substance 统一 event / relation / knowledge / strategy / backtest 五种形态，
每种物质可持久化、可检索、有来源（provenance）。审核分层见 ReviewStatus；
断言载荷见 Claim（写入 metadata['claim']）。采用 Pydantic BaseModel。
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


class ReviewStatus(StrEnum):
    """物质审核分层 — 决定默认激活可见性与写入权限。

    RAW: 采集原文，Agent 只读，内容不可覆盖。
    STAGING: LLM 抽取或未过门的候选，须显式 ``include_staging`` 才进入激活。
    COMMITTED: 过回测/OOS/规则升格后的正式知识，默认可激活。
    """

    RAW = "raw"
    STAGING = "staging"
    COMMITTED = "committed"


class Claim(BaseModel):
    """断言载荷 — 挂在 EVENT / STRATEGY 的 metadata['claim']。

    把「新闻 blob / 策略 YAML」升级为可验证三元组，并保留证据指针。
    """

    subject: str = ""
    predicate: str = ""
    object: str = ""
    context: str = ""
    evidence_ref: str = ""
    valid_time: str = ""

    def as_metadata(self) -> dict[str, str]:
        """序列化为可写入 Substance.metadata['claim'] 的字典。"""
        return {
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "context": self.context,
            "evidence_ref": self.evidence_ref,
            "valid_time": self.valid_time,
        }

    @classmethod
    def from_metadata(cls, metadata: dict[str, Any]) -> Claim | None:
        """从物质 metadata 读取断言；无 claim 键则返回 None。"""
        raw = metadata.get("claim")
        if not isinstance(raw, dict):
            return None
        return cls(
            subject=str(raw.get("subject") or ""),
            predicate=str(raw.get("predicate") or ""),
            object=str(raw.get("object") or ""),
            context=str(raw.get("context") or ""),
            evidence_ref=str(raw.get("evidence_ref") or ""),
            valid_time=str(raw.get("valid_time") or ""),
        )

    @classmethod
    def from_event_dict(cls, event: dict[str, Any], *, evidence_ref: str = "") -> Claim:
        """从事件抽取 dict 构造断言；缺字段时用 content / symbols 兜底。"""
        symbols = event.get("symbols") or []
        object_text = str(event.get("object") or "")
        if not object_text and isinstance(symbols, list):
            object_text = ",".join(str(s) for s in symbols if s)
        return cls(
            subject=str(event.get("subject") or event.get("content") or ""),
            predicate=str(event.get("predicate") or "impacts"),
            object=object_text,
            context=str(event.get("context") or event.get("category") or ""),
            evidence_ref=str(event.get("evidence_ref") or evidence_ref),
            valid_time=str(event.get("valid_time") or event.get("published_at") or ""),
        )


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
    decay_half_life_days: float = Field(default=90.0, gt=0)
    review_status: ReviewStatus = ReviewStatus.COMMITTED
    metadata: dict[str, Any] = Field(default_factory=dict)

    def is_activatable(self, *, include_staging: bool = False) -> bool:
        """默认激活可见性：RAW 永不注入；STAGING 须显式打开；COMMITTED 默认可注入。"""
        if self.review_status is ReviewStatus.RAW:
            return False
        if self.review_status is ReviewStatus.STAGING:
            return include_staging
        return True

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
