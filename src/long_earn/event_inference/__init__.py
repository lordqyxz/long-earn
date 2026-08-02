"""新闻事件推理引擎（ADR-007 Phase 2）。

多源采集器 + 事件推理子图（collect → extract → propagate → conflict → save）。
基于物质-运动统一架构，将新闻事件沉淀为 Substance，并推理影响传播关系。

拓扑::

    START
      ↓
    collect ──(无原始素材)──► END
      ↓
    extract ──(LLM 抽取结构化事件)
      ↓
    propagate ──(LLM 推理事件→影响标的因果链)
      ↓
    conflict ──(检测与已有物质的冲突)
      ↓
    save ──(持久化到 SubstanceStore)
      ↓
    END

关键性质：
- 采集器可注入（Kimi / ciccwm / 测试 Fake），解耦外部数据源。
- 推理节点（extract / propagate）可注入 LLM 实现，支持确定性 e2e。
- 产物是 Substance（event / relation 形态），与 ADR-007 物质-运动架构统一。
"""

from long_earn.event_inference.collectors import (
    CiccwmHotCollector,
    CiccwmTopicCollector,
    CollectedItem,
    Collector,
    CollectorRegistry,
    KimiCollector,
    create_default_collector_registry,
)
from long_earn.event_inference.state import EventInferenceState
from long_earn.event_inference.subgraph import create_event_inference_subgraph

__all__ = [
    "CiccwmHotCollector",
    "CiccwmTopicCollector",
    "CollectedItem",
    "Collector",
    "CollectorRegistry",
    "EventInferenceState",
    "KimiCollector",
    "create_default_collector_registry",
    "create_event_inference_subgraph",
]
