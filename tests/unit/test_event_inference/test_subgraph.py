"""事件推理子图 (event_inference/subgraph) 测试。

用 Fake 采集器 + FakeEventExtractor + FakeEventPropagator + FakeMemoryService
注入确定性实现，验证五步循环拓扑 + 落库契约 + 空素材提前结束 + 冲突分组逻辑。
不依赖真实 LLM / 外部数据源。
"""

from __future__ import annotations

from typing import Any

from long_earn.event_inference.agents import FakeEventExtractor, FakeEventPropagator
from long_earn.event_inference.collectors.base import CollectedItem, CollectorRegistry
from long_earn.event_inference.subgraph import (
    create_event_inference_subgraph,
    create_event_inference_subgraph_for_testing,
)


class _FakeMemory:
    """Fake MemoryService，仅实现 save_events，记录调用参数。"""

    def __init__(self) -> None:
        self.save_calls: list[dict[str, Any]] = []
        self._sid_counter = 0

    def save_events(
        self,
        events: list[dict[str, Any]],
        relations: list[dict[str, Any]],
        conflict_groups: dict[int, str] | None = None,
    ) -> dict[str, Any]:
        self.save_calls.append(
            {
                "events": events,
                "relations": relations,
                "conflict_groups": conflict_groups or {},
            }
        )
        event_sids: list[str] = []
        for _ in events:
            self._sid_counter += 1
            event_sids.append(f"sub_event_{self._sid_counter}")
        relation_sids: list[str] = []
        for _ in relations:
            self._sid_counter += 1
            relation_sids.append(f"sub_rel_{self._sid_counter}")
        return {
            "event_sids": event_sids,
            "relation_sids": relation_sids,
            "event_count": len(event_sids),
            "relation_count": len(relation_sids),
        }


class _FakeLogger:
    """测试日志服务，确保测试工厂的依赖完整。"""

    def debug(self, message: str) -> None: pass

    def info(self, message: str) -> None: pass

    def warning(self, message: str) -> None: pass

    def error(self, message: str) -> None: pass

    def exception(self, message: str) -> None: pass


class _FakeCollector:
    """确定性采集器。"""

    def __init__(self, items: list[CollectedItem]):
        self._items = items

    name = "fake"
    is_available = True

    def collect(self, query: str, max_items: int = 10) -> list[CollectedItem]:
        return self._items[:max_items]


def _make_subgraph(
    items: list[CollectedItem],
    memory: _FakeMemory | None = None,
) -> tuple[Any, _FakeMemory]:
    """构造注入 Fake 依赖的子图。"""
    memory = memory or _FakeMemory()
    registry = CollectorRegistry()
    registry.register(_FakeCollector(items))
    subgraph = create_event_inference_subgraph_for_testing(
        registry=registry,
        extractor=FakeEventExtractor(),
        propagator=FakeEventPropagator(),
        memory=memory,
        logger=_FakeLogger(),
    )
    return subgraph, memory


class TestSubgraphCompile:
    def test_compiles_with_fake_deps(self):
        subgraph, _ = _make_subgraph([])
        assert subgraph is not None

    def test_production_factory_requires_runtime_context(self):
        """生产工厂不允许缺失 RuntimeContext 后静默空跑。"""
        import pytest

        with pytest.raises(ValueError, match="RuntimeContext"):
            create_event_inference_subgraph(None)  # type: ignore[arg-type]

    def test_compiles_with_explicit_test_deps(self):
        """测试工厂在完整显式依赖下注入时能够编译。"""
        memory = _FakeMemory()
        subgraph = create_event_inference_subgraph_for_testing(
            registry=CollectorRegistry(),
            extractor=FakeEventExtractor(),
            propagator=FakeEventPropagator(),
            memory=memory,
            logger=_FakeLogger(),
        )
        assert subgraph is not None


class TestSubgraphEndToEnd:
    def test_empty_items_short_circuits_to_end(self):
        """无原始素材时 collect → END，不触发 extract/save。"""
        subgraph, memory = _make_subgraph([])
        result = subgraph.invoke({"query": "茅台"})
        assert result["collected_items"] == []
        assert memory.save_calls == []
        # save 节点未执行（summary 不存在）
        assert "summary" not in result

    def test_full_pipeline_persists_events_and_relations(self):
        """有素材时完整跑通五步循环并落库。"""
        items = [
            CollectedItem(title="茅台财报", content="净利润增长15%", source="fake"),
            CollectedItem(title="宁德时代扩产", content="新增产能", source="fake"),
        ]
        subgraph, memory = _make_subgraph(items)
        result = subgraph.invoke({"query": "财报"})

        # extract 产出事件
        assert len(result["extracted_events"]) == 2
        # propagate 产出关系（Fake 为有 symbols 的事件产出关系；FakeEventExtractor 无 symbols → 0 关系）
        assert "propagated_relations" in result
        # save 被调用一次
        assert len(memory.save_calls) == 1
        call = memory.save_calls[0]
        assert len(call["events"]) == 2
        # 落库返回的 sids
        assert len(result["saved_sids"]) >= 2
        assert result["summary"]["event_count"] == 2

    def test_conflict_group_assigned_for_opposite_sentiments(self):
        """同标的存在相反情绪 → 归入冲突组。

        FakeEventExtractor 不产 symbols，需用自定义 extractor 注入相反情绪事件。
        """
        items = [CollectedItem(title="t", content="c", source="fake")]

        class _ConflictExtractor:
            def extract(self, collected_items):
                return [
                    {
                        "content": "茅台利好",
                        "keys": ["茅台"],
                        "symbols": ["600519.SH"],
                        "sentiment": "positive",
                        "category": "财报",
                        "confidence": 0.9,
                    },
                    {
                        "content": "茅台利空",
                        "keys": ["茅台"],
                        "symbols": ["600519.SH"],
                        "sentiment": "negative",
                        "category": "风险",
                        "confidence": 0.8,
                    },
                ]

        memory = _FakeMemory()
        registry = CollectorRegistry()
        registry.register(_FakeCollector(items))
        subgraph = create_event_inference_subgraph_for_testing(
            registry=registry,
            extractor=_ConflictExtractor(),
            propagator=FakeEventPropagator(),
            memory=memory,
            logger=_FakeLogger(),
        )
        result = subgraph.invoke({"query": "茅台"})

        conflict_groups = result["conflict_groups"]
        # 两个事件都应被归入同一冲突组
        assert len(conflict_groups) == 2
        assert conflict_groups[0] == conflict_groups[1]
        # 冲突组 ID 传递到 save
        call = memory.save_calls[0]
        assert call["conflict_groups"] == conflict_groups

    def test_no_conflict_when_same_sentiment(self):
        """同标的同情绪不产生冲突组。"""
        items = [CollectedItem(title="t", content="c", source="fake")]

        class _SameSentimentExtractor:
            def extract(self, collected_items):
                return [
                    {
                        "content": "茅台利好A",
                        "keys": ["茅台"],
                        "symbols": ["600519.SH"],
                        "sentiment": "positive",
                        "category": "财报",
                        "confidence": 0.9,
                    },
                    {
                        "content": "茅台利好B",
                        "keys": ["茅台"],
                        "symbols": ["600519.SH"],
                        "sentiment": "positive",
                        "category": "财报",
                        "confidence": 0.8,
                    },
                ]

        memory = _FakeMemory()
        registry = CollectorRegistry()
        registry.register(_FakeCollector(items))
        subgraph = create_event_inference_subgraph_for_testing(
            registry=registry,
            extractor=_SameSentimentExtractor(),
            propagator=FakeEventPropagator(),
            memory=memory,
            logger=_FakeLogger(),
        )
        result = subgraph.invoke({"query": "茅台"})
        assert result["conflict_groups"] == {}


class TestMemoryServiceSaveEvents:
    """验证 MemoryServiceImpl.save_events 真实落库（用 SubstanceStore 校验）。"""

    def test_save_events_creates_event_and_relation_substances(self, tmp_path):
        from long_earn.services.memory_service import MemoryServiceImpl
        from long_earn.substance.model import SubstanceForm

        config = MagicMock()
        config.memory_path = str(tmp_path / "sub.duckdb")
        config.init_dir = ""
        logger = MagicMock()
        svc = MemoryServiceImpl(config, logger)

        events = [
            {
                "content": "茅台净利润增长15%",
                "keys": ["茅台"],
                "symbols": ["600519.SH"],
                "sentiment": "positive",
                "category": "财报",
                "confidence": 0.9,
            }
        ]
        relations = [
            {
                "event_index": 0,
                "target": "600519.SH",
                "relation_type": "impacts",
                "confidence": 0.85,
                "direction": "positive",
                "rationale": "利好估值",
            }
        ]
        result = svc.save_events(events, relations)
        assert result["event_count"] == 1
        assert result["relation_count"] == 1

        # 校验 store 中物质形态
        substances = svc._store.get_all()
        forms = {s.form for s in substances}
        assert SubstanceForm.EVENT in forms
        assert SubstanceForm.RELATION in forms

        # 关系的 source_id 指向事件 sid
        relation = next(s for s in substances if s.form is SubstanceForm.RELATION)
        event = next(s for s in substances if s.form is SubstanceForm.EVENT)
        assert relation.source_id == event.sid


from unittest.mock import MagicMock  # noqa: E402
