"""事件推理子图状态。"""

from __future__ import annotations

from typing import Any, TypedDict

from long_earn.event_inference.collectors.base import CollectedItem


class EventInferenceState(TypedDict, total=False):
    """事件推理子图状态。

    一次 invoke 完成一次 collect → extract → propagate → conflict → save 流程，
    中间产物沿状态流转，最终落库到 SubstanceStore。
    """

    # 输入
    query: str

    # collect 节点产出：原始素材
    collected_items: list[CollectedItem]

    # extract 节点产出：从原始素材抽取的结构化事件物质
    # 每项是 Substance 的 dict 表示（form=EVENT），由 extract agent 产出
    extracted_events: list[dict[str, Any]]

    # propagate 节点产出：事件→影响标的的关系物质
    # 每项是 Substance 的 dict 表示（form=RELATION）
    propagated_relations: list[dict[str, Any]]

    # conflict 节点产出：冲突检测结果（冲突组分配，键为事件下标 → 组 ID）
    conflict_groups: dict[int, str]

    # save 节点产出：落库的 sid 列表 + 统计
    saved_sids: list[str]
    summary: dict[str, Any]
