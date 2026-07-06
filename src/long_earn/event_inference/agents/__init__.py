"""事件推理 Agent — 抽取与传播推理。

两类 Agent 均定义为 Protocol（鸭子类型），支持注入 LLM 实现或确定性 Fake 实现，
与 operator_dev 的 OperatorImplementer 同模式，便于无 LLM 环境的 e2e 测试。

- :class:`EventExtractor`: 原始素材 → 结构化事件 dict 列表
- :class:`EventPropagator`: 事件 → 影响传播关系 dict 列表
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Protocol

from loguru import logger

from long_earn.core.chat_prompt_loader import MarkdownChatPromptTemplate
from long_earn.event_inference.collectors.base import CollectedItem

if TYPE_CHECKING:
    from long_earn.config import RuntimeContext
    from long_earn.services import LLMService


# ── 事件/关系 dict schema（文档契约）─────────────────────────────────────
#
# extracted event dict:
#   {
#     "content": str,           # 事件一句话摘要
#     "keys": list[str],        # WorldInfo 触发词
#     "symbols": list[str],     # 受影响标的（xtquant 格式）
#     "sentiment": str,         # positive / negative / neutral
#     "category": str,          # 财报/政策/并购/...
#     "confidence": float,      # 0.0-1.0
#   }
#
# propagated relation dict:
#   {
#     "event_index": int,       # 关联事件下标
#     "target": str,            # 受影响标的或行业
#     "relation_type": str,     # impacts / propagates_to / correlates_with
#     "confidence": float,      # 0.0-1.0
#     "direction": str,         # positive / negative / neutral
#     "rationale": str,         # 影响逻辑
#   }


def _parse_json_array(text: str) -> list[dict[str, Any]]:
    """从 LLM 响应解析 JSON 数组（剥离 markdown 代码块）。"""
    content = text.strip()
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", content, re.DOTALL)
    if match:
        content = match.group(1).strip()
    try:
        data = json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning(f"[event_inference] JSON 数组解析失败: {e}")
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


# ── EventExtractor ──────────────────────────────────────────────────────


class EventExtractor(Protocol):
    """事件抽取器 — 原始素材 → 结构化事件。"""

    def extract(self, items: list[CollectedItem]) -> list[dict[str, Any]]:
        """抽取结构化事件。

        Args:
            items: 采集器产出的原始素材

        Returns:
            事件 dict 列表（见模块顶部 schema 注释）
        """
        ...


class LLMEventExtractor:
    """LLM 事件抽取器（生产实现）。"""

    def __init__(self, llm_service: LLMService) -> None:
        self._llm = llm_service
        self._prompt = MarkdownChatPromptTemplate(
            "extract_prompt.md", caller_file=__file__
        )

    def extract(self, items: list[CollectedItem]) -> list[dict[str, Any]]:
        if not items:
            return []
        items_json = json.dumps(
            [
                {"title": it.title, "content": it.content, "source": it.source}
                for it in items
            ],
            ensure_ascii=False,
        )
        messages = self._prompt.format_messages(items_json=items_json)
        try:
            response = self._llm.invoke(messages, format="json")
        except Exception as e:
            logger.error(f"[event_inference] extract LLM 调用失败: {e}")
            return []
        return _parse_json_array(response.content)


class FakeEventExtractor:
    """确定性事件抽取器（测试用）。

    将每条素材直接转为基础事件 dict，不做语义加工。适合 e2e 测试验证子图拓扑。
    """

    def extract(self, items: list[CollectedItem]) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        for it in items:
            if not it.content.strip():
                continue
            events.append(
                {
                    "content": it.title or it.content[:80],
                    "keys": [w for w in (it.title or "").split() if w][:5],
                    "symbols": [],
                    "sentiment": "neutral",
                    "category": "其他",
                    "confidence": 0.5,
                }
            )
        return events


# ── EventPropagator ─────────────────────────────────────────────────────


class EventPropagator(Protocol):
    """影响传播推理器 — 事件 → 影响关系。"""

    def propagate(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """推理事件影响传播。

        Args:
            events: 事件 dict 列表

        Returns:
            关系 dict 列表（见模块顶部 schema 注释）
        """
        ...


class LLMEventPropagator:
    """LLM 影响传播推理器（生产实现）。"""

    def __init__(self, llm_service: LLMService) -> None:
        self._llm = llm_service
        self._prompt = MarkdownChatPromptTemplate(
            "propagate_prompt.md", caller_file=__file__
        )

    def propagate(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not events:
            return []
        events_json = json.dumps(events, ensure_ascii=False)
        messages = self._prompt.format_messages(events_json=events_json)
        try:
            response = self._llm.invoke(messages, format="json")
        except Exception as e:
            logger.error(f"[event_inference] propagate LLM 调用失败: {e}")
            return []
        relations = _parse_json_array(response.content)
        # 校验 event_index 合法性
        valid: list[dict[str, Any]] = []
        for rel in relations:
            idx = rel.get("event_index")
            if isinstance(idx, int) and 0 <= idx < len(events):
                valid.append(rel)
        return valid


class FakeEventPropagator:
    """确定性影响传播推理器（测试用）。

    为每个有 symbols 的事件产出一条 ``impacts`` 关系。
    """

    def propagate(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        relations: list[dict[str, Any]] = []
        for idx, ev in enumerate(events):
            symbols = ev.get("symbols") or []
            for sym in symbols:
                relations.append(
                    {
                        "event_index": idx,
                        "target": str(sym),
                        "relation_type": "impacts",
                        "confidence": 0.7,
                        "direction": str(ev.get("sentiment", "neutral")),
                        "rationale": "Fake 推理：事件直接影响标的",
                    }
                )
        return relations


def create_default_extractors(context: RuntimeContext) -> tuple[
    EventExtractor, EventPropagator
]:
    """从 RuntimeContext 构造默认的 LLM 抽取器 + 传播器。"""
    llm = context.require_llm()
    return LLMEventExtractor(llm), LLMEventPropagator(llm)
