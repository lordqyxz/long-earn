"""审计事件与归因链相关模型（/api/runs/{run_id}/audit/{trace_id}、交易归因链）。

字段以 ``BacktestAnalyzer`` 的 ``export_audit_event`` / ``export_trade_attribution``
实际返回结构为准；对真实数据中可能缺失或为 null 的字段使用可选类型与默认值，
保持宽松校验、不改变既有响应行为。payload / risk_trigger / selection 等来自
PG jsonb 的动态载荷统一用 ``dict[str, Any]`` 建模（JSON 反序列化中间态）。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AuditEventItem(BaseModel):
    """审计事件原始记录条目（GET /api/runs/{run_id}/audit/{trace_id}）。

    ``payload`` 为事件原始载荷（含下钻核验所需的完整数据），来源为 PG jsonb。
    """

    event_type: str = ""
    component: str = ""
    status: str = ""
    timestamp: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class AuditEventsResponse(BaseModel):
    """GET /api/runs/{run_id}/audit/{trace_id}"""

    run_id: str
    trace_id: str
    events: list[AuditEventItem] = Field(default_factory=list)


class AuditChainEvent(BaseModel):
    """审计链节点事件的紧凑摘要（后端预计算的一句人话摘要 + 元信息）。

    供审计链节点 hover Tooltip 展示；字段与
    ``BacktestAnalyzer._build_chain_events`` 输出的节点摘要一致。
    """

    event_type: str = ""
    component: str = ""
    status: str = ""
    timestamp: str | None = None
    summary: str = ""


class AttributionChainEvents(BaseModel):
    """归因链各环节（upstream/order/fill）的紧凑事件摘要，缺失环节为 None。"""

    upstream: AuditChainEvent | None = None
    order: AuditChainEvent | None = None
    fill: AuditChainEvent | None = None


class AttributionChain(BaseModel):
    """归因链 trace 路径（fill/order/upstream 三个 trace_id 供前端展示）。"""

    fill: str = ""
    order: str = ""
    upstream: str = ""
    events: AttributionChainEvents | None = None


class RationaleWeights(BaseModel):
    """选股权重方法。"""

    method: str = ""


class RationaleSegment(BaseModel):
    """决策步骤的结构化渲染段（前端数据驱动渲染的原子片段）。"""

    type: str = ""
    value: str | float | bool = ""
    unit: str = ""


class RationaleCriterion(BaseModel):
    """决策流水线单步（因子/过滤/排名等，含结构化渲染段）。"""

    step: str = ""
    op: str = ""
    alias: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    desc: str = ""
    format: str = ""
    kind: str = ""
    segments: list[RationaleSegment] = Field(default_factory=list)


class Rationale(BaseModel):
    """信号决策依据（公式原文 + 流水线 + 选股依据）。

    ``selection`` 为选股明细行（symbol/rank + 各因子值，键随算子变化），
    保持宽松建模。
    """

    formula: str = ""
    criteria: list[RationaleCriterion] = Field(default_factory=list)
    selection: list[dict[str, Any]] = Field(default_factory=list)
    universe_size: int | None = None
    selected_count: int | None = None
    weights: RationaleWeights | None = None


class OrderInfo(BaseModel):
    """订单摘要信息（取自 ORDER 事件 payload）。"""

    symbol: str = ""
    type: str = ""
    quantity: float = 0.0


class SignalAttribution(BaseModel):
    """上游信号归因（取自 SIGNAL 事件 payload）。

    ``signals`` 为符号→权重的映射；兼容历史数据中可能遗留的字符串序列化形式。
    """

    strategy_id: str = ""
    signals: dict[str, float] | str = ""
    risk_triggered: bool = False
    rationale: Rationale | None = None


class TradeAttribution(BaseModel):
    """单笔 FILL 的审计归因链（SIGNAL→ORDER→FILL / RISK_TRIGGER→ORDER→FILL）。

    ``risk_trigger`` 为风控载荷（字段随 risk_type 变化），保持宽松建模。
    """

    kind: str = ""
    order: OrderInfo | None = None
    signal: SignalAttribution | None = None
    risk_trigger: dict[str, Any] | None = None
    chain: AttributionChain | None = None
