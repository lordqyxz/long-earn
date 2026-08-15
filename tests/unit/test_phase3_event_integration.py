"""ADR-007 Phase 3 子图集成测试。

覆盖：
- MemoryServiceImpl.activate_events() — WorldInfo 激活 + 格式化
- KnowledgeContextMixin._get_event_context() — 事件上下文 helper
- EventAnalyzer — Dashboard 事件流查询
- stock_analysis event_context_node — 子图集成注入
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock

from long_earn.app.event_analyzer import EventAnalyzer
from long_earn.services.memory_service import MemoryServiceImpl
from long_earn.substance.model import Substance, SubstanceForm
from long_earn.substance.store import SubstanceStore

# ── 测试夹具 ──────────────────────────────────────────────────────


def _make_event(
    content: str,
    keys: list[str],
    symbols: list[str],
    sentiment: str = "neutral",
    category: str = "",
    confidence: float = 0.8,
    days_ago: int = 0,
    conflict_group: str | None = None,
) -> Substance:
    """构造 EVENT 形态物质。"""
    created = datetime.now() - timedelta(days=days_ago)
    return Substance(
        form=SubstanceForm.EVENT,
        content=content,
        keys=keys,
        source="event_inference",
        confidence=confidence,
        conflict_group=conflict_group,
        created_at=created,
        metadata={
            "category": "新闻事件",
            "event_category": category,
            "sentiment": sentiment,
            "symbols": symbols,
        },
    )


def _make_relation(
    source_sid: str,
    target: str,
    direction: str = "positive",
    confidence: float = 0.7,
    rationale: str = "",
) -> Substance:
    """构造 RELATION 形态物质。"""
    return Substance(
        form=SubstanceForm.RELATION,
        content=rationale or f"影响 {target}",
        source_id=source_sid,
        target_id=target,
        relation_type="impacts",
        confidence=confidence,
        source="event_inference",
        metadata={"direction": direction, "target": target},
    )


def _make_memory_service(tmp_path) -> MemoryServiceImpl:
    """构造已初始化的 MemoryServiceImpl（不加载 init 目录）。"""
    config = MagicMock()
    config.memory_path = str(tmp_path / "sub.duckdb")
    config.init_dir = ""
    logger = MagicMock()
    svc = MemoryServiceImpl(config, logger)
    svc._initialized = True
    return svc


def _populate_store(store: SubstanceStore) -> dict[str, str]:
    """填充测试事件 + 关系，返回 sid 映射。"""
    e1 = _make_event(
        "茅台三季报净利润同比增长15%",
        keys=["茅台", "600519"],
        symbols=["600519.SH"],
        sentiment="positive",
        category="财报",
        confidence=0.9,
        days_ago=1,
    )
    e2 = _make_event(
        "白酒行业整体承压",
        keys=["白酒", "行业"],
        symbols=["600519.SH"],
        sentiment="negative",
        category="行业",
        confidence=0.6,
        days_ago=2,
    )
    e3 = _make_event(
        "新能源汽车销量创新高",
        keys=["新能源", "比亚迪"],
        symbols=["002594.SZ"],
        sentiment="positive",
        category="销量",
        confidence=0.85,
        days_ago=3,
    )
    # 冲突组：茅台 e1 / e2 同标的相反情绪
    e1.conflict_group = "conflict_600519_0"
    e2.conflict_group = "conflict_600519_0"
    e1.insertion_order = 2
    e2.insertion_order = 1

    sid1 = store.add(e1)
    sid2 = store.add(e2)
    sid3 = store.add(e3)

    r1 = _make_relation(sid1, "600519.SH", direction="positive", confidence=0.85)
    sid_r1 = store.add(r1)

    return {"e1": sid1, "e2": sid2, "e3": sid3, "r1": sid_r1}


# ── MemoryServiceImpl.activate_events ─────────────────────────────


class TestActivateEvents:
    """MemoryServiceImpl.activate_events — WorldInfo 激活 + 格式化。"""

    def test_returns_formatted_event_strings(self, tmp_path):
        """命中关键词的事件被激活并格式化为带元数据头的字符串。"""
        svc = _make_memory_service(tmp_path)
        _populate_store(svc._store)

        result = svc.activate_events("茅台 财报", k=5)

        assert len(result) >= 1
        # 格式：以【事件 开头
        assert any(r.startswith("【事件") for r in result)
        # 包含标的和情绪信息
        assert any("600519.SH" in r for r in result)
        assert any("positive" in r for r in result)

    def test_empty_query_returns_empty(self, tmp_path):
        """空 query 返回空列表。"""
        svc = _make_memory_service(tmp_path)
        _populate_store(svc._store)

        assert svc.activate_events("", k=5) == []
        assert svc.activate_events("   ", k=5) == []

    def test_no_match_returns_empty(self, tmp_path):
        """无关键词命中返回空列表。"""
        svc = _make_memory_service(tmp_path)
        _populate_store(svc._store)

        result = svc.activate_events("不存在的关键词xyz", k=5)
        assert result == []

    def test_conflict_group_mutually_exclusive(self, tmp_path):
        """同 conflict_group 取 insertion_order 最高者（e1 胜 e2）。"""
        svc = _make_memory_service(tmp_path)
        _populate_store(svc._store)

        # 触发 600519，应同时命中 e1（order=2）和 e2（order=1），但 conflict_group 互斥
        result = svc.activate_events("600519", k=5, include_relations=False)
        contents = "\n".join(result)
        # e1 内容应存在（insertion_order 高）
        assert "净利润同比增长15%" in contents
        # e2 内容应被互斥掉
        assert "白酒行业整体承压" not in contents

    def test_include_relations_flag(self, tmp_path):
        """include_relations=True 时返回 RELATION 物质。"""
        svc = _make_memory_service(tmp_path)
        _populate_store(svc._store)

        without_rel = svc.activate_events("茅台", k=10, include_relations=False)
        with_rel = svc.activate_events("茅台", k=10, include_relations=True)

        # 带 relation 时结果应不少于不带时
        assert len(with_rel) >= len(without_rel)
        # 带 relation 时应包含影响关系
        assert any("影响关系" in r for r in with_rel)

    def test_respects_k_limit(self, tmp_path):
        """k 限制返回条数。"""
        svc = _make_memory_service(tmp_path)
        _populate_store(svc._store)

        result = svc.activate_events("茅台", k=1)
        assert len(result) <= 1


# ── KnowledgeContextMixin._get_event_context ──────────────────────


class TestGetEventContext:
    """KnowledgeContextMixin._get_event_context — helper 注入逻辑。"""

    def test_returns_event_context_string(self, tmp_path):
        """正常返回事件上下文字符串。"""
        from long_earn.strategy_rd.agents.mixins import KnowledgeContextMixin

        svc = _make_memory_service(tmp_path)
        _populate_store(svc._store)

        mixin = KnowledgeContextMixin()
        mixin.memory = svc
        mixin.logger = MagicMock()
        mixin._event_cache = {}

        ctx = mixin._get_event_context("茅台", k=5)
        assert ctx
        assert "600519.SH" in ctx

    def test_caches_results(self, tmp_path):
        """同一 query 二次调用命中缓存。"""
        from long_earn.strategy_rd.agents.mixins import KnowledgeContextMixin

        svc = _make_memory_service(tmp_path)
        _populate_store(svc._store)

        mixin = KnowledgeContextMixin()
        mixin.memory = svc
        mixin.logger = MagicMock()
        mixin._event_cache = {}

        ctx1 = mixin._get_event_context("茅台", k=5)
        # 替换 memory 为抛异常的 mock，缓存应仍返回原值
        mixin.memory = MagicMock()
        mixin.memory.activate_events.side_effect = RuntimeError("should not call")
        ctx2 = mixin._get_event_context("茅台", k=5)

        assert ctx1 == ctx2

    def test_fallback_when_memory_lacks_activate_events(self):
        """memory 无 activate_events 方法时返回空字符串（优雅降级）。"""
        from long_earn.strategy_rd.agents.mixins import KnowledgeContextMixin

        mixin = KnowledgeContextMixin()
        mixin.memory = MagicMock(
            spec=["search", "save_experience"]
        )  # 无 activate_events
        mixin.logger = MagicMock()
        mixin._event_cache = {}

        assert mixin._get_event_context("任何查询", k=5) == ""

    def test_swallows_exceptions(self, tmp_path):
        """activate_events 抛异常时返回空字符串。"""
        from long_earn.strategy_rd.agents.mixins import KnowledgeContextMixin

        mixin = KnowledgeContextMixin()
        mixin.memory = MagicMock()
        mixin.memory.activate_events.side_effect = RuntimeError("boom")
        mixin.logger = MagicMock()
        mixin._event_cache = {}

        assert mixin._get_event_context("查询", k=5) == ""


# ── EventAnalyzer ─────────────────────────────────────────────────


class TestEventAnalyzer:
    """EventAnalyzer — Dashboard 事件流查询。"""

    def _make_analyzer(self) -> tuple[EventAnalyzer, dict[str, str]]:
        analyzer = EventAnalyzer()
        sids = _populate_store(analyzer.store)
        analyzer._loaded = True
        return analyzer, sids

    def test_list_events_returns_all_by_default(self):
        """无过滤返回所有事件，按时间倒序。"""
        analyzer, _ = self._make_analyzer()

        events = analyzer.list_events(limit=50)
        assert len(events) == 3
        # 倒序：最近创建的在前
        assert "净利润同比增长15%" in events[0]["content"]

    def test_list_events_filter_by_symbol(self):
        """按标的过滤。"""
        analyzer, _ = self._make_analyzer()

        events = analyzer.list_events(symbol="600519")
        assert len(events) == 2
        assert all("600519.SH" in e["symbols"] for e in events)

    def test_list_events_filter_by_sentiment(self):
        """按情绪过滤。"""
        analyzer, _ = self._make_analyzer()

        positive = analyzer.list_events(sentiment="positive")
        assert len(positive) == 2
        assert all(e["sentiment"] == "positive" for e in positive)

        negative = analyzer.list_events(sentiment="negative")
        assert len(negative) == 1
        assert negative[0]["sentiment"] == "negative"

    def test_list_events_respects_limit(self):
        """limit 限制返回条数。"""
        analyzer, _ = self._make_analyzer()

        events = analyzer.list_events(limit=2)
        assert len(events) == 2

    def test_list_events_empty_store(self):
        """空 store 返回空列表。"""
        analyzer = EventAnalyzer()
        assert analyzer.list_events() == []

    def test_event_stats(self):
        """统计：情绪分布、类别分布、热门标的。"""
        analyzer, _ = self._make_analyzer()

        stats = analyzer.event_stats()
        assert stats["total_events"] == 3
        assert stats["total_relations"] == 1
        assert stats["by_sentiment"]["positive"] == 2
        assert stats["by_sentiment"]["negative"] == 1
        # 600519.SH 出现 2 次（e1 + e2）
        top_sym = stats["top_symbols"][0]
        assert top_sym["symbol"] == "600519.SH"
        assert top_sym["count"] == 2

    def test_event_stats_empty_store(self):
        """空 store 返回零值统计。"""
        analyzer = EventAnalyzer()
        stats = analyzer.event_stats()
        assert stats["total_events"] == 0
        assert stats["by_sentiment"] == {}

    def test_event_timeline(self):
        """时间线按天聚合。"""
        analyzer, _ = self._make_analyzer()

        timeline = analyzer.event_timeline(days=30)
        assert len(timeline) == 3  # 3 个不同日期
        # 每个桶有 count 字段
        assert all("count" in t for t in timeline)
        # 按日期升序
        assert timeline[0]["date"] <= timeline[-1]["date"]

    def test_event_timeline_filters_old_events(self):
        """days 过小过滤掉旧事件。"""
        analyzer, _ = self._make_analyzer()
        timeline = analyzer.event_timeline(days=1)
        # 只有 1 天前的事件（e1）
        assert len(timeline) == 1

    def test_get_event_with_relations(self):
        """get_event 返回事件详情 + 关联关系。"""
        analyzer, sids = self._make_analyzer()

        event = analyzer.get_event(sids["e1"])
        assert event is not None
        assert event["sid"] == sids["e1"]
        assert event["sentiment"] == "positive"
        assert len(event["relations"]) == 1
        assert event["relations"][0]["target"] == "600519.SH"

    def test_get_event_not_found(self):
        """不存在的 sid 返回 None。"""
        analyzer, _ = self._make_analyzer()
        assert analyzer.get_event("nonexistent_sid") is None

    def test_get_event_rejects_non_event_sid(self):
        """RELATION 形态的 sid 返回 None。"""
        analyzer, sids = self._make_analyzer()
        assert analyzer.get_event(sids["r1"]) is None

    def test_list_relations(self):
        """列出关系物质，按置信度倒序。"""
        analyzer, _ = self._make_analyzer()

        relations = analyzer.list_relations()
        assert len(relations) == 1
        assert relations[0]["target"] == "600519.SH"

    def test_list_relations_filter_by_target(self):
        """按 target 过滤关系。"""
        analyzer, _ = self._make_analyzer()

        relations = analyzer.list_relations(target="600519")
        assert len(relations) == 1

        relations = analyzer.list_relations(target="notexist")
        assert relations == []

    def test_load_from_pg(self, tmp_path):
        """save 落 PostgreSQL 后 load 能读回新增物质（PG 时代无文件语义）。"""
        from long_earn.substance.persistence import delete_substance, load_all

        store = SubstanceStore()
        before = {s.sid for s in load_all()}
        _populate_store(store)
        store.save()  # path 参数已废弃，直接落 PG

        analyzer = EventAnalyzer()
        assert analyzer.load()
        assert analyzer.is_ready
        # 验证本次写入的物质已持久化（差集法，不依赖库中其他数据）
        added = {s.sid for s in load_all()} - before
        assert len(added) >= 3
        # 清理本次写入，避免污染共享库
        for sid in added:
            delete_substance(sid)


# ── stock_analysis event_context_node 集成 ─────────────────────────


class TestStockAnalysisEventContextNode:
    """stock_analysis/subgraph.event_context_node — 子图集成注入。"""

    def test_node_activates_events_for_stock(self, tmp_path, monkeypatch):
        """节点根据 stock_name/code 激活事件并写入 state。"""
        from long_earn.stock_analysis.subgraph import event_context_node

        svc = _make_memory_service(tmp_path)
        _populate_store(svc._store)

        context = MagicMock()
        context.logger = MagicMock()
        context.require_memory.return_value = svc

        state = {"stock_name": "茅台", "stock_code": "600519.SH"}

        result = event_context_node(state, context)

        assert "event_context" in result
        assert result["event_context"]
        assert "600519.SH" in result["event_context"]

    def test_node_empty_state_returns_empty(self, tmp_path):
        """无 stock_name/code 返回空事件上下文。"""
        from long_earn.stock_analysis.subgraph import event_context_node

        svc = _make_memory_service(tmp_path)
        context = MagicMock()
        context.logger = MagicMock()
        context.require_memory.return_value = svc

        result = event_context_node({}, context)
        assert result == {"event_context": ""}

    def test_node_handles_memory_without_activate_events(self, tmp_path):
        """memory 无 activate_events 方法时优雅降级。"""
        from long_earn.stock_analysis.subgraph import event_context_node

        context = MagicMock()
        context.logger = MagicMock()
        fake_memory = MagicMock(spec=["search", "save_experience"])
        context.require_memory.return_value = fake_memory

        result = event_context_node(
            {"stock_name": "茅台", "stock_code": "600519"}, context
        )
        assert result == {"event_context": ""}

    def test_node_swallows_exceptions(self, tmp_path):
        """activate_events 抛异常时记录警告并返回空。"""
        from long_earn.stock_analysis.subgraph import event_context_node

        context = MagicMock()
        context.logger = MagicMock()
        fake_memory = MagicMock()
        fake_memory.activate_events.side_effect = RuntimeError("boom")
        context.require_memory.return_value = fake_memory

        result = event_context_node(
            {"stock_name": "茅台", "stock_code": "600519"}, context
        )
        assert result == {"event_context": ""}
        context.logger.warning.assert_called_once()


# ── strategy_rd 事件上下文注入 ─────────────────────────────────────


class TestStrategyRdEventInjection:
    """strategy_rd/subgraph._initial_retrieval_node — 事件上下文注入。"""

    def test_initial_retrieval_includes_event_context(self, tmp_path):
        """_initial_retrieval_node 把事件上下文附加到 knowledge_context。"""
        from long_earn.strategy_rd.subgraph import _initial_retrieval_node

        svc = _make_memory_service(tmp_path)
        _populate_store(svc._store)

        research_agent = MagicMock()
        research_agent._get_knowledge_context.return_value = "基础知识"
        research_agent._get_event_context.return_value = "相关事件: 茅台财报"

        logger = MagicMock()
        result = _initial_retrieval_node({"query": "茅台策略"}, research_agent, logger)

        assert "### 相关市场事件" in result["knowledge_context"]
        assert "相关事件: 茅台财报" in result["knowledge_context"]
        assert "基础知识" in result["knowledge_context"]
        # 研究代理应被调用获取事件上下文
        research_agent._get_event_context.assert_called_once()

    def test_initial_retrieval_without_events(self):
        """无事件时 knowledge_context 不含事件段。"""
        from long_earn.strategy_rd.subgraph import _initial_retrieval_node

        research_agent = MagicMock()
        research_agent._get_knowledge_context.return_value = "仅知识"
        research_agent._get_event_context.return_value = ""

        result = _initial_retrieval_node({"query": "查询"}, research_agent, MagicMock())
        assert "### 相关市场事件" not in result["knowledge_context"]
        assert result["knowledge_context"] == "仅知识"
