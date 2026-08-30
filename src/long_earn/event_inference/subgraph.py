"""事件推理子图 — LangGraph 编排（ADR-007 Phase 2）。

拓扑::

    START
      ↓
    collect ──(无原始素材)──► END
      ↓
    extract
      ↓
    propagate
      ↓
    conflict
      ↓
    save
      ↓
    END

collect 节点对所有可用采集器拉取原始素材；extract/propagate 节点用可注入的
LLM Agent 推理结构化事件与影响关系；conflict 节点做事件间情绪冲突分组；
save 节点经 :class:`MemoryService.save_events` 落库到 SubstanceStore。

为支持确定性 e2e（不依赖真实 LLM / 外部数据源），:class:`EventExtractor` /
:class:`EventPropagator` / :class:`Collector` 均可注入 Fake 实现。
"""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

from langgraph.graph import END, START, StateGraph

from long_earn.event_inference.agents import (
    EventExtractor,
    EventPropagator,
    create_default_extractors,
)
from long_earn.event_inference.collectors.base import CollectorRegistry
from long_earn.event_inference.state import EventInferenceState

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

    from long_earn.config import RuntimeContext
    from long_earn.services import LoggerService, MemoryService


# ── 节点 ────────────────────────────────────────────────────────────────


def _collect_node(
    state: EventInferenceState,
    registry: CollectorRegistry,
    logger: LoggerService,
) -> dict:
    """对所有可用采集器拉取原始素材。"""
    query = state.get("query", "")
    if not query:
        return {"collected_items": []}
    items = registry.collect_all(query)
    logger.info(f"[event_inference] collect: {len(items)} 条素材")
    return {"collected_items": items}


def _extract_node(
    state: EventInferenceState,
    extractor: EventExtractor,
    logger: LoggerService,
) -> dict:
    """LLM 抽取结构化事件。"""
    items = state.get("collected_items", [])
    events = extractor.extract(items)
    logger.info(f"[event_inference] extract: {len(events)} 个事件")
    return {"extracted_events": events}


def _propagate_node(
    state: EventInferenceState,
    propagator: EventPropagator,
    logger: LoggerService,
) -> dict:
    """LLM 推理影响传播关系。"""
    events = state.get("extracted_events", [])
    relations = propagator.propagate(events)
    logger.info(f"[event_inference] propagate: {len(relations)} 条关系")
    return {"propagated_relations": relations}


def _conflict_node(
    state: EventInferenceState,
    logger: LoggerService,
) -> dict:
    """事件间情绪冲突分组。

    同一标的上同时出现 positive / negative 情绪的事件归入同一 conflict_group，
    后续 WorldInfo 激活时按 insertion_order 互斥（取最新）。
    """
    events = state.get("extracted_events", [])
    if not events:
        return {"conflict_groups": {}}

    # 按标的分组事件下标
    symbol_to_indices: dict[str, list[int]] = {}
    for idx, ev in enumerate(events):
        for sym in ev.get("symbols") or []:
            if sym:
                symbol_to_indices.setdefault(sym, []).append(idx)

    conflict_groups: dict[int, str] = {}
    for sym, indices in symbol_to_indices.items():
        if len(indices) < 2:  # noqa: PLR2004
            continue
        sentiments = {events[i].get("sentiment", "neutral") for i in indices}
        # 同标的存在相反情绪 → 冲突组。group_id 仅由 symbol 构成（同一标的
        # 本就只归一组），不含运行内计数器，保证跨运行 group_id 稳定、
        # conflict_group 互斥语义跨运行成立
        if {"positive", "negative"} <= sentiments:
            group_id = f"conflict_{sym}"
            for i in indices:
                conflict_groups[i] = group_id

    if conflict_groups:
        logger.info(
            f"[event_inference] conflict: {len(conflict_groups)} 个事件归入冲突组"
        )
    return {"conflict_groups": conflict_groups}


def _save_node(
    state: EventInferenceState,
    memory: MemoryService,
    logger: LoggerService,
) -> dict:
    """落库事件 + 关系到 SubstanceStore。"""
    events = state.get("extracted_events", [])
    relations = state.get("propagated_relations", [])
    conflict_groups = state.get("conflict_groups", {})

    if not events:
        logger.info("[event_inference] save: 无事件可落库")
        return {"saved_sids": [], "summary": {"event_count": 0, "relation_count": 0}}

    result = memory.save_events(events, relations, conflict_groups)
    event_sids = [s for s in result.get("event_sids", []) if s]
    relation_sids = result.get("relation_sids", [])
    logger.info(
        f"[event_inference] save: {len(event_sids)} 事件 + "
        f"{len(relation_sids)} 关系已落库"
    )
    return {
        "saved_sids": [*event_sids, *relation_sids],
        "summary": {
            "event_count": result.get("event_count", len(event_sids)),
            "relation_count": result.get("relation_count", len(relation_sids)),
            "collected_count": len(state.get("collected_items", [])),
        },
    }


# ── 条件路由 ────────────────────────────────────────────────────────────


def _after_collect_cond(state: EventInferenceState) -> str:
    """无原始素材则直接结束。"""
    return "end" if not state.get("collected_items") else "extract"


# ── 子图构造 ────────────────────────────────────────────────────────────


def create_event_inference_subgraph(
    context: RuntimeContext,
) -> CompiledStateGraph:
    """用完整运行时上下文创建生产事件推理子图。"""
    if context is None:
        raise ValueError("生产事件推理子图需要 RuntimeContext")

    from long_earn.event_inference.collectors import (  # noqa: PLC0415
        create_default_collector_registry,
    )

    registry = create_default_collector_registry(
        market_intelligence=context.market_intelligence,
    )
    extractor, propagator = create_default_extractors(context)
    return _compile_event_inference_subgraph(
        registry=registry,
        extractor=extractor,
        propagator=propagator,
        memory=context.memory,
        logger=context.logger,
    )


def create_event_inference_subgraph_for_testing(
    *,
    registry: CollectorRegistry,
    extractor: EventExtractor,
    propagator: EventPropagator,
    memory: MemoryService,
    logger: LoggerService,
) -> CompiledStateGraph:
    """用完整显式依赖创建确定性测试事件推理子图。"""
    return _compile_event_inference_subgraph(
        registry=registry,
        extractor=extractor,
        propagator=propagator,
        memory=memory,
        logger=logger,
    )


def _compile_event_inference_subgraph(
    *,
    registry: CollectorRegistry,
    extractor: EventExtractor,
    propagator: EventPropagator,
    memory: MemoryService,
    logger: LoggerService,
) -> CompiledStateGraph:
    """将已完成构造的事件推理依赖编译为 LangGraph。"""

    workflow = StateGraph(EventInferenceState)

    workflow.add_node(
        "collect", partial(_collect_node, registry=registry, logger=logger)
    )
    workflow.add_node(
        "extract", partial(_extract_node, extractor=extractor, logger=logger)
    )
    workflow.add_node(
        "propagate", partial(_propagate_node, propagator=propagator, logger=logger)
    )
    workflow.add_node("conflict", partial(_conflict_node, logger=logger))
    workflow.add_node("save", partial(_save_node, memory=memory, logger=logger))

    workflow.add_edge(START, "collect")
    workflow.add_conditional_edges(
        "collect", _after_collect_cond, {"end": END, "extract": "extract"}
    )
    workflow.add_edge("extract", "propagate")
    workflow.add_edge("propagate", "conflict")
    workflow.add_edge("conflict", "save")
    workflow.add_edge("save", END)

    return workflow.compile()
